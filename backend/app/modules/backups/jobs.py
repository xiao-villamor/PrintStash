"""Backup Jobs: manual backups, automatic daily backups, and retention.

A manual backup no longer holds an HTTP request open while the vault is
archived: the route records a queued ``backups.create`` Job and returns it.
Automatic backups are a schedule declared in code, read from the backup
configuration (``automatic_backup_time_utc``); the domain's own daily claim
(``claim_due_backup``) still guarantees at most one automatic attempt per day,
so a resubmitted occurrence never archives twice.

Each backup run records the Job that built it. That Job's next attempt, its
failure or its cancellation settles a run it left running, so no reader ever
has to guess whether a "running" run is still live. Retrying one failed
destination is its own Job (``backups.retry_destination``), one per
destination at a time; its end settles the attempt and the destination.

Restore is deliberately *not* a Job: it replaces the application database
that every Job row lives in, so it runs on the API under the restore fence,
which drains every executor first, and the engine is reset afterwards.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

import app.modules.backups.backup.contracts as backup_contracts
import app.modules.backups.backup.creation as backup_creation
import app.modules.backups.backup.deletion as backup_deletion
from app.db.models import JobKind, LaneName, SystemConfig
from app.db.session import get_session_factory
from app.modules.backups.backup_destination import BackupTrigger
from app.modules.backups.backup_schedule import claim_due_backup, parse_backup_time
from app.modules.work.contracts import JobContext, JobDefinition, JobOutcome, Step
from app.modules.work.sources import ScheduleSource

# Every manual backup claims one subject, so a second request while one is
# queued or running is refused rather than archiving the vault twice.
MANUAL_SUBJECT = "backup/manual"


def _meta(meta: backup_contracts.BackupMeta) -> dict[str, Any]:
    """The new backup as ``GET /backups/{id}`` shows it, plus its run outcome.

    ``source_ref`` is what every later operation on this exact archive needs,
    so a client can act on the backup without listing sources first.
    """
    return {
        "location": meta.location,
        "source_ref": meta.source_ref,
        "provider_ref": meta.provider_ref,
        "namespace": meta.namespace,
        "run_id": meta.run_id,
        "outcome": meta.outcome,
        "destination_results": meta.destination_results,
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "archive_sha256": meta.archive_sha256,
    }


def _archive(ctx: JobContext, trigger: BackupTrigger) -> None:
    from app.modules.backups.backup_runs import settle_job_runs

    def report(
        stage: backup_contracts.BackupStage,
        *,
        processed: int | None = None,
        total: int | None = None,
        destination: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {"stage": stage.value}
        if processed is not None:
            fields["processed"] = processed
        if total is not None:
            fields["total"] = total
        if destination is not None:
            fields["current_item"] = destination
        ctx.update(**fields)

    # A run an earlier attempt of this Job left running was never finished:
    # the engine runs one attempt at a time, so nothing is still writing it.
    settle_job_runs(ctx.job_id)
    try:
        meta = backup_creation.create_backup(
            trigger=trigger, job_id=ctx.job_id, progress=report
        )
    except backup_contracts.DatabaseBackupNotSupportedError:
        ctx.finish(JobOutcome.FAILED, error="database_backup_not_supported")
        return
    except RuntimeError as exc:
        detail = str(exc)
        if detail in {"backup_destination_required", "backup_all_destinations_failed"}:
            ctx.finish(
                JobOutcome.FAILED,
                error=detail,
                result={"run_id": getattr(exc, "run_id", None)},
                retryable=detail == "backup_all_destinations_failed",
            )
            return
        raise
    if meta.outcome == "completed":
        completion = "complete"
    elif meta.outcome == "partial":
        completion = "partial"
    else:
        raise ValueError("unexpected_backup_outcome")
    report(backup_contracts.BackupStage.FINALIZING)
    backup_deletion.purge_old_backups()
    ctx.update(
        result=_meta(meta),
        completion=completion,
        succeeded=1,
    )


def _create(ctx: JobContext) -> None:
    _archive(ctx, BackupTrigger.MANUAL)


def _automatic(ctx: JobContext) -> None:
    with get_session_factory().scoped_session() as session:
        claimed = claim_due_backup(session)
    if not claimed:
        ctx.update(result={"skipped": "not_due"})
        return
    _archive(ctx, BackupTrigger.AUTOMATIC)


def _settle_runs_of(session: Session, subject: str) -> None:
    """A backup Job failed or was cancelled: settle the runs it left running.

    Any run of this subject's Jobs still running is one: a subject has at most
    one active Job, and it is the one ending now.
    """
    from sqlmodel import col, select

    from app.db.models import BackupRun, Job
    from app.modules.backups.backup_runs import summarise_run

    job_ids = select(Job.id).where(Job.subject_key == subject)
    for run in session.exec(
        select(BackupRun).where(
            col(BackupRun.job_id).in_(job_ids), BackupRun.outcome == "running"
        )
    ).all():
        summarise_run(session, run.id)


def _retry_step(ctx: JobContext) -> None:
    from app.modules.backups.retry_commands import run_retry

    ctx.update(result=run_retry(ctx.job_id), processed=1, total=1, succeeded=1)


def _settle_retry(reason: str):
    def hook(session: Session, subject: str, *_: object) -> None:
        from app.modules.backups.retry_commands import result_id_of, settle_open_retries

        settle_open_retries(session, result_id_of(subject), reason)

    return hook


def _automatic_cron(session: Session) -> str | None:
    """The daily automatic backup as a cron expression, or ``None`` when off."""
    config = session.get(SystemConfig, 1)
    if config is None or not config.automatic_backups_enabled:
        return None
    scheduled = parse_backup_time(config.automatic_backup_time_utc)
    return f"{scheduled.minute} {scheduled.hour} * * *"


def definitions() -> list[JobDefinition]:
    return [
        JobDefinition(
            name=JobKind.BACKUPS_CREATE,
            lane=LaneName.MAINTENANCE,
            steps=(Step(f"{JobKind.BACKUPS_CREATE.value}.archive", _create),),
            cancel=_settle_runs_of,
            on_failure=lambda session, subject, _reason: _settle_runs_of(
                session, subject
            ),
            survives_restore=False,
            label="Backups",
        ),
        JobDefinition(
            name=JobKind.BACKUPS_RETRY_DESTINATION,
            lane=LaneName.MAINTENANCE,
            steps=(
                Step(f"{JobKind.BACKUPS_RETRY_DESTINATION.value}.publish", _retry_step),
            ),
            cancel=_settle_retry("backup_retry_cancelled"),
            on_failure=_settle_retry("backup_publication_interrupted"),
            # A new retry is a new request: it re-checks the destination.
            retry=lambda _session, _subject: False,
            survives_restore=False,
            label="Backup destination retries",
        ),
        JobDefinition(
            name=JobKind.BACKUPS_AUTOMATIC,
            lane=LaneName.MAINTENANCE,
            steps=(Step(f"{JobKind.BACKUPS_AUTOMATIC.value}.archive", _automatic),),
            source=ScheduleSource(JobKind.BACKUPS_AUTOMATIC, _automatic_cron),
            retry=lambda _session, _subject: False,
            cancel=_settle_runs_of,
            on_failure=lambda session, subject, _reason: _settle_runs_of(
                session, subject
            ),
            survives_restore=False,
            label="Automatic backups",
        ),
    ]
