"""Verified upload handoff: the ``ingestion.artifact_upload`` Job.

Finalizing an upload session claims it (``INGESTING``), records a queued Job
that owns the verified staged object through a staging lease, and nudges. This
Job commits the object as an Artifact (or attaches it as a revision) and
mirrors its terminal result onto the upload session.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import Session, select

from app.core.metrics import record_artifact_upload_event
from app.core.time import utcnow
from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    ArtifactUploadSession,
    ArtifactUploadState,
    FileRevisionStatus,
    FileType,
    JobKind,
    JobState,
    Model,
    StagingLease,
)
from app.db.scopes import live
from app.db.session import SessionFactory, get_session_factory
from app.modules.ingestion import staging_leases
from app.modules.ingestion.ingestion import (
    StagedArtifact,
    add_gcode_revision_to_model,
    ingest_staged_file,
)
from app.modules.work.contracts import JobContext, JobOutcome
from app.modules.work.jobs import TERMINAL_STATES, status_of
from app.modules.work.jobs import jobs as registry

from .manager import SqlArtifactUploadManager
from .native_parts import NativeMultipartUploadAdapter
from .state import require_transition


def subject_key(upload_id: str) -> str:
    return f"artifact_upload/{upload_id}"


def _staged_path(session: Session, job_id: str) -> Path | None:
    lease = session.exec(
        select(StagingLease).where(StagingLease.job_id == job_id)
    ).first()
    return Path(lease.path) if lease is not None else None


def run_verified_upload_ingestion(
    *,
    upload_id: str,
    job_context: JobContext,
    session_factory: SessionFactory,
) -> None:
    """Run the commit for one verified upload, then mirror its terminal result."""
    job_id = job_context.job_id

    with session_factory.scoped_session() as session:
        upload = session.get(ArtifactUploadSession, upload_id)
        if upload is None or upload.state != ArtifactUploadState.INGESTING:
            return
        options = json.loads(upload.request_json)
        purpose = upload.purpose
        filename = upload.filename
        owner_user_id = upload.owner_user_id
        target_id = upload.target_id
        staged_path = _staged_path(session, job_id)
        received = (
            staging_leases.received_digest(session, job_id, staged_path)
            if staged_path is not None
            else None
        )
        # Native uploads already have verified bytes in the store. Preserve
        # their identity through the Job so publication can copy them there.
        staged_origin = (
            NativeMultipartUploadAdapter.staged_origin(upload)
            if upload.adapter_id == NativeMultipartUploadAdapter.adapter_id
            else None
        )

    if staged_path is None or not staged_path.exists():
        job_context.finish(JobOutcome.FAILED, error="staging_expired", retryable=False)
    elif purpose == "revision":
        job_context.update(label="Attaching revision")
        try:
            with session_factory.scoped_session() as session:
                model = session.exec(
                    select(Model).where(Model.id == int(target_id or ""), live(Model))
                ).first()
                if model is None:
                    raise ValueError("model_not_found")
                file_row = add_gcode_revision_to_model(
                    session=session,
                    model=model,
                    staged_path=staged_path,
                    staged_origin=staged_origin,
                    original_filename=filename,
                    revision_label=options.get("revision_label"),
                    revision_status=FileRevisionStatus(
                        options.get("revision_status") or FileRevisionStatus.NEEDS_TEST
                    ),
                    revision_notes=options.get("revision_notes"),
                    is_recommended=bool(options.get("is_recommended", False)),
                )
                job_context.finish(
                    JobOutcome.COMPLETED,
                    model_id=model.id,
                    file_id=file_row.id,
                )
        except Exception:
            job_context.finish(
                JobOutcome.FAILED,
                error="artifact_revision_ingestion_failed",
                retryable=True,
            )
    else:
        suffix = Path(filename).suffix.lower()
        file_type: FileType | None = None
        if purpose in {"gcode", "slicer"}:
            file_type = FileType.GCODE
        elif purpose in {"model", "external_writeback"}:
            file_type = SUFFIX_TO_FILE_TYPE.get(suffix)
        if file_type is None:
            job_context.finish(
                JobOutcome.FAILED,
                error="artifact_upload_purpose_not_supported",
                retryable=False,
            )
        else:
            ingest_staged_file(
                job_context=job_context,
                artifact=StagedArtifact(
                    staged_path=staged_path,
                    original_filename=filename,
                    model_name=str(options.get("model_name") or Path(filename).stem),
                    file_type=file_type,
                    collection=options.get("collection"),
                    tags=options.get("tags"),
                    source_hash=options.get("source_hash"),
                    target_library_id=options.get("target_library_id"),
                    staged_origin=staged_origin,
                    received_sha256=received,
                ),
                actor_user_id=owner_user_id,
                session_factory=session_factory,
            )

    result = registry.get(job_id)
    completed = result is not None and result.state is JobState.COMPLETED
    with session_factory.scoped_session() as session:
        upload = session.get(ArtifactUploadSession, upload_id)
        if upload is None:
            return
        mirrored = upload.state != ArtifactUploadState.INGESTING
        if mirrored:
            # The Job's terminal write already mirrored its result (``_settled``
            # for a commit, ``_on_failure`` otherwise). Only what must not run
            # inside that transaction is left: the provider call.
            if completed and upload.state == ArtifactUploadState.COMPLETED:
                root = staged_path.parents[1] if staged_path is not None else None
                record_artifact_upload_event("completed", upload.adapter_id)
                SqlArtifactUploadManager(session, staging_root=root).adapter_for(
                    upload
                ).abort_owned(upload)
            return
    # The settled cleanup did not commit with the terminal write (it failed, or
    # this execution was recovered): mirror under the Job's lock, as it would.
    with session_factory.scoped_session() as session:
        if (
            registry.lock_execution(
                session,
                job_context.job_id,
                epoch=job_context.execution_epoch,
                attempt=job_context.attempt,
                states=TERMINAL_STATES,
            )
            is None
        ):
            return
        upload = session.get(ArtifactUploadSession, upload_id)
        if upload is None or upload.state != ArtifactUploadState.INGESTING:
            return
        target = (
            ArtifactUploadState.COMPLETED if completed else ArtifactUploadState.FAILED
        )
        root = staged_path.parents[1] if staged_path is not None else None
        manager = SqlArtifactUploadManager(session, staging_root=root)
        manager.transition(
            upload,
            target,
            error_code=None if completed else "artifact_ingestion_failed",
            retryable=bool(result.retryable) if result is not None else True,
        )
        record_artifact_upload_event(
            "completed" if completed else "failed", upload.adapter_id
        )
        if completed:
            lease = session.exec(
                select(StagingLease).where(StagingLease.job_id == job_id)
            ).first()
            if lease is not None:
                session.delete(lease)
                session.commit()
            manager.adapter_for(upload).abort_owned(upload)


def _upload_id(subject: str) -> str:
    return subject.split("/", 1)[1]


def _step(ctx) -> None:
    run_verified_upload_ingestion(
        upload_id=_upload_id(ctx.subject_key),
        job_context=ctx,
        session_factory=get_session_factory(),
    )


def _cancel(session: Session, subject: str) -> None:
    upload = session.get(ArtifactUploadSession, _upload_id(subject))
    if upload is None or upload.state != ArtifactUploadState.INGESTING:
        return
    upload.state = ArtifactUploadState.FAILED
    upload.error_code = "artifact_upload_cancelled"
    upload.retryable = False
    upload.version += 1
    session.add(upload)


def _on_failure(session: Session, subject: str, reason: str) -> None:
    upload = session.get(ArtifactUploadSession, _upload_id(subject))
    if upload is None or upload.state != ArtifactUploadState.INGESTING:
        return
    upload.state = ArtifactUploadState.FAILED
    upload.error_code = "artifact_ingestion_failed"
    job = registry.row(session, upload.job_id) if upload.job_id is not None else None
    if job is None:
        raise RuntimeError("artifact_upload_job_missing")
    upload.retryable = bool(status_of(job).retryable)
    upload.version += 1
    session.add(upload)
    del reason


def _settled(session: Session, subject: str) -> None:
    """Mirror a committed upload onto its session with the Job's terminal write.

    Runs in that write's transaction, so it only touches rows: the staged
    receipt is released, and the session leaves ``INGESTING`` in the same
    commit that made its Job terminal. Replayed for an already settled Job it
    finds the session mirrored and changes nothing.
    """
    from app.modules.ingestion.staging_cleanup import release_job

    upload = session.get(ArtifactUploadSession, _upload_id(subject))
    if (
        upload is None
        or upload.job_id is None
        or upload.state != ArtifactUploadState.INGESTING
    ):
        return
    job = registry.row(session, upload.job_id)
    if job is None or job.state is not JobState.COMPLETED:
        return
    release_job(session, job.id)
    for lease in session.exec(
        select(StagingLease).where(StagingLease.job_id == job.id)
    ).all():
        session.delete(lease)
    require_transition(
        ArtifactUploadState.INGESTING,
        ArtifactUploadState.COMPLETED,
        retryable=upload.retryable,
    )
    upload.state = ArtifactUploadState.COMPLETED
    upload.error_code = None
    upload.retryable = bool(status_of(job).retryable)
    upload.version += 1
    upload.updated_at = utcnow()
    session.add(upload)


def _recover_uploads() -> dict[str, int]:
    from .recovery import reconcile_artifact_uploads

    outcome = reconcile_artifact_uploads()
    return {
        "reconciled": outcome.reconciled,
        "expired": outcome.expired,
        "retained": outcome.retained,
    }


def definitions():
    from app.db.models import LaneName
    from app.modules.work.contracts import JobDefinition, Step
    from app.modules.work.sources import fixed, scheduled

    return [
        scheduled(
            JobKind.INGESTION_UPLOAD_RECOVERY,
            cron=fixed("55 * * * *"),
            run=_recover_uploads,
            label="Resumable upload expiry",
        ),
        JobDefinition(
            name=JobKind.INGESTION_ARTIFACT_UPLOAD,
            lane=LaneName.INGEST,
            steps=(Step(f"{JobKind.INGESTION_ARTIFACT_UPLOAD.value}.run", _step),),
            cancel=_cancel,
            on_failure=_on_failure,
            on_settled=_settled,
            retry=lambda _session, _subject: False,
            label="Resumable uploads",
            adds_to_library=True,
        ),
    ]
