"""Durable execution evidence, separate from archive discovery and restore."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlmodel import col, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    BackupDestinationResult,
    BackupRetryAttempt,
    BackupRun,
    StorageConnection,
    StorageConnectionPurpose,
)
from app.db.session import get_session_factory
from app.services.backup_destination import BackupTrigger, local_destination_enabled


class BackupRunError(RuntimeError):
    def __init__(self, reason: str, run_id: str):
        super().__init__(reason)
        self.run_id = run_id


@dataclass(frozen=True)
class SelectedRun:
    run_id: str
    local_result_id: str | None
    local_directory: str
    s3_result_id: str | None
    s3_configuration: tuple[str, str, str, str, str]
    connections: tuple[tuple[str, StorageConnection], ...]

    @property
    def selected(self) -> bool:
        return bool(self.local_result_id or self.s3_result_id or self.connections)


def begin_run(
    *, backup_id: str, archive_name: str, trigger: BackupTrigger, created_at: datetime
) -> SelectedRun:
    """Commit every selected target before constructing any transport adapter.

    Credential values stay only in the in-memory selection. Retry may use rotated
    credentials, but persisted target configuration and locators never change.
    """
    from app.services.backup import _stable_backup_s3_config

    run_id = uuid.uuid4().hex
    keep_local = local_destination_enabled(trigger)
    s3_configuration = _stable_backup_s3_config()
    local_directory = str(settings.backup_dir)
    local_id = s3_id = None
    connections = []
    with get_session_factory().scoped_session() as session:
        rows = session.exec(
            select(StorageConnection)
            .where(
                col(StorageConnection.enabled).is_(True),
                col(StorageConnection.purpose).in_(
                    [StorageConnectionPurpose.BACKUP, StorageConnectionPurpose.BOTH]
                ),
                col(
                    StorageConnection.manual_backup_enabled
                    if trigger == BackupTrigger.MANUAL
                    else StorageConnection.automatic_backup_enabled
                ).is_(True),
            )
            .order_by(col(StorageConnection.id))
        ).all()
        # Capture the decrypted profile before session expiry. This object is
        # ephemeral; neither its secrets nor exception text enters run records.
        profiles = [StorageConnection.model_validate(row.model_dump()) for row in rows]
        run = BackupRun(
            id=run_id,
            backup_id=backup_id,
            trigger=trigger.value,
            archive_name=archive_name,
            created_at=created_at,
            storage_backend=settings.storage_backend,
            app_version=settings.app_version,
        )
        session.add(run)
        session.flush()
        if keep_local:
            local_id = uuid.uuid4().hex
            session.add(
                BackupDestinationResult(
                    id=local_id,
                    run_id=run_id,
                    kind="local",
                    name="Local backup",
                    configuration_json=json.dumps(
                        {"directory": str(Path(local_directory).resolve())}
                    ),
                )
            )
        if s3_configuration[0]:
            s3_id = uuid.uuid4().hex
            session.add(
                BackupDestinationResult(
                    id=s3_id,
                    run_id=run_id,
                    kind="s3",
                    name="S3 backup",
                    configuration_json=json.dumps(
                        dict(
                            zip(
                                ("bucket", "endpoint_url", "region"),
                                s3_configuration[:3],
                                strict=True,
                            )
                        )
                    ),
                )
            )
        for profile in profiles:
            result_id = uuid.uuid4().hex
            session.add(
                BackupDestinationResult(
                    id=result_id,
                    run_id=run_id,
                    kind="connection",
                    name=profile.name,
                    connection_id=profile.id,
                    configuration_json=json.dumps(
                        {
                            "kind": profile.kind.value,
                            "configuration": profile.config_json,
                        }
                    ),
                )
            )
            connections.append((result_id, profile))
        session.commit()
    return SelectedRun(
        run_id, local_id, local_directory, s3_id, s3_configuration, tuple(connections)
    )


def update_result(result_id: str, **changes) -> None:
    with get_session_factory().scoped_session() as session:
        result = session.get(BackupDestinationResult, result_id)
        if result is None:
            raise RuntimeError("backup_destination_result_missing")
        for key, value in changes.items():
            setattr(result, key, value)
        result.updated_at = utcnow()
        session.add(result)
        session.commit()


def archive_ready(run_id: str, *, digest: str, size: int, file_count: int) -> None:
    with get_session_factory().scoped_session() as session:
        run = session.get(BackupRun, run_id)
        assert run is not None
        run.archive_sha256, run.size_bytes, run.file_count = digest, size, file_count
        session.add(run)
        session.commit()


def finish_run(run_id: str, *, error_code: str | None = None) -> str:
    with get_session_factory().scoped_session() as session:
        run = session.get(BackupRun, run_id)
        assert run is not None
        results = session.exec(
            select(BackupDestinationResult).where(
                BackupDestinationResult.run_id == run_id
            )
        ).all()
        for result in results:
            if result.outcome in {"pending", "publishing"}:
                result.outcome = "failed"
                result.error_code = error_code or "backup_publication_interrupted"
                result.updated_at = utcnow()
                session.add(result)
        complete = sum(result.outcome == "completed" for result in results)
        run.outcome = (
            "completed"
            if results and complete == len(results)
            else "partial"
            if complete
            else "failed"
        )
        run.error_code = error_code
        run.finished_at = utcnow()
        session.add(run)
        session.commit()
        return run.outcome


def run_detail(run_id: str) -> dict:
    with get_session_factory().scoped_session() as session:
        run = session.get(BackupRun, run_id)
        if run is None:
            raise LookupError("backup_run_not_found")
        results = session.exec(
            select(BackupDestinationResult)
            .where(BackupDestinationResult.run_id == run_id)
            .order_by(
                col(BackupDestinationResult.created_at), col(BackupDestinationResult.id)
            )
        ).all()
        attempts = session.exec(
            select(BackupRetryAttempt)
            .join(BackupDestinationResult)
            .where(BackupDestinationResult.run_id == run_id)
            .order_by(col(BackupRetryAttempt.created_at), col(BackupRetryAttempt.id))
        ).all()
        by_destination: dict[str, list[dict]] = {}
        for attempt in attempts:
            by_destination.setdefault(attempt.destination_result_id, []).append(
                attempt.model_dump(mode="json")
            )
        return {
            **run.model_dump(mode="json"),
            "destinations": [
                {
                    **result.model_dump(mode="json", exclude={"configuration_json"}),
                    "retry_attempts": by_destination.get(result.id, []),
                }
                for result in results
            ],
        }


def publication_started(
    result_id: str, *, key: str, namespace: str, provider_ref: str, target
) -> None:
    update_result(
        result_id,
        key=key,
        namespace=namespace,
        provider_ref=provider_ref,
        target_identity_json=target.model_dump_json() if target is not None else None,
        outcome="publishing",
        error_code=None,
    )


def publication_completed(result_id: str, meta) -> None:
    from app.db.models import OwnedStorageObject, StorageObjectState

    with get_session_factory().scoped_session() as session:
        owned = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == meta.path,
                OwnedStorageObject.namespace == meta.namespace,
                OwnedStorageObject.provider_ref == meta.provider_ref,
                OwnedStorageObject.object_kind == "backup",
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
                OwnedStorageObject.sha256 == meta.archive_sha256,
            )
        ).one()
        ownership_id = owned.id
    update_result(
        result_id,
        outcome="completed",
        error_code=None,
        ownership_id=ownership_id,
        source_ref=meta.source_ref,
        published_at=utcnow(),
    )


def list_runs(*, limit: int = 50, offset: int = 0) -> list[dict]:
    reconcile_interrupted_runs()
    with get_session_factory().scoped_session() as session:
        ids = session.exec(
            select(BackupRun.id)
            .order_by(col(BackupRun.created_at).desc(), col(BackupRun.id))
            .offset(offset)
            .limit(limit)
        ).all()
    return [run_detail(run_id) for run_id in ids]


def retry_destination(result_id: str) -> dict:
    """Retry an exact destination from verified surviving bytes, never a rebuild."""
    from sqlalchemy import update

    from app.db.models import BackupRetryAttempt
    from app.services import backup
    from app.services.backup_replica_retry import (
        RetryRefused,
        publish_retry,
        reconcile_result,
        verified_survivor,
    )

    with backup._backup_restore_lock:
        reconcile_interrupted_runs()
        with get_session_factory().scoped_session() as session:
            result = session.get(BackupDestinationResult, result_id)
            if result is None:
                raise LookupError("backup_destination_result_not_found")
            run = session.get(BackupRun, result.run_id)
            if run is None or result.outcome != "failed":
                raise RetryRefused("backup_retry_not_failed")
            claimed = session.execute(
                update(BackupDestinationResult)
                .where(
                    col(BackupDestinationResult.id) == result_id,
                    col(BackupDestinationResult.outcome) == "failed",
                )
                .values(outcome="publishing", updated_at=utcnow())
                .returning(col(BackupDestinationResult.id))
            )
            if claimed.scalar_one_or_none() is None:
                raise RetryRefused("backup_retry_in_progress")
            attempt = BackupRetryAttempt(
                id=uuid.uuid4().hex,
                destination_result_id=result_id,
                archive_sha256=run.archive_sha256,
            )
            session.add(attempt)
            session.commit()
            attempt_id = attempt.id
            session.refresh(run)
            session.refresh(result)
            run = BackupRun.model_validate(run.model_dump())
            result = BackupDestinationResult.model_validate(result.model_dump())
            survivors = [
                BackupDestinationResult.model_validate(row.model_dump())
                for row in session.exec(
                    select(BackupDestinationResult).where(
                        BackupDestinationResult.run_id == run.id,
                        BackupDestinationResult.outcome == "completed",
                    )
                ).all()
            ]
        try:
            published = (
                reconcile_result(result, run) if result.target_identity_json else False
            )
            if published:
                with get_session_factory().scoped_session() as session:
                    attempt = session.get(BackupRetryAttempt, attempt_id)
                    assert attempt is not None
                    attempt.source_result_id = result.id
                    session.add(attempt)
                    session.commit()
            for survivor in [] if published else survivors:
                from contextlib import ExitStack

                with ExitStack() as resources:
                    try:
                        path = resources.enter_context(verified_survivor(survivor, run))
                    except Exception:
                        continue
                    with get_session_factory().scoped_session() as session:
                        attempt = session.get(BackupRetryAttempt, attempt_id)
                        assert attempt is not None
                        attempt.source_result_id = survivor.id
                        session.add(attempt)
                        session.commit()
                    publish_retry(result, run, path)
                    published = True
                    break
            if not published:
                raise RetryRefused("backup_retry_new_backup_required")
        except BaseException as exc:
            reason = (
                str(exc)
                if isinstance(exc, RetryRefused)
                else "backup_retry_publication_failed"
            )
            update_result(result_id, outcome="failed", error_code=reason)
            with get_session_factory().scoped_session() as session:
                attempt = session.get(BackupRetryAttempt, attempt_id)
                assert attempt is not None
                attempt.outcome, attempt.error_code, attempt.finished_at = (
                    "failed",
                    reason,
                    utcnow(),
                )
                session.add(attempt)
                session.commit()
            finish_run(run.id)
            if isinstance(exc, Exception):
                raise RetryRefused(reason) from exc
            raise
        with get_session_factory().scoped_session() as session:
            attempt = session.get(BackupRetryAttempt, attempt_id)
            assert attempt is not None
            attempt.outcome, attempt.finished_at = "completed", utcnow()
            session.add(attempt)
            session.commit()
        finish_run(run.id)
        return next(
            row for row in run_detail(run.id)["destinations"] if row["id"] == result_id
        )


def reconcile_interrupted_runs() -> None:
    """Recover execution status after acquiring the process-wide operation lock.

    The supported deployment has one process. Once this lock is held, publishing
    states from earlier operations are interrupted, never concurrent writers.
    Storage reconciliation remains exact and is performed before a retry.
    """
    from app.db.models import BackupRetryAttempt
    from app.services import backup

    with backup._backup_restore_lock:
        with get_session_factory().scoped_session() as session:
            runs = session.exec(
                select(BackupRun).where(BackupRun.outcome == "running")
            ).all()
            active_results = session.exec(
                select(BackupDestinationResult).where(
                    BackupDestinationResult.outcome == "publishing"
                )
            ).all()
            run_ids = {run.id for run in runs} | {
                result.run_id for result in active_results
            }
            for result in active_results:
                result.outcome = "failed"
                result.error_code = "backup_publication_interrupted"
                result.updated_at = utcnow()
                session.add(result)
            attempts = session.exec(
                select(BackupRetryAttempt).where(
                    BackupRetryAttempt.outcome == "running"
                )
            ).all()
            for attempt in attempts:
                result = session.get(
                    BackupDestinationResult, attempt.destination_result_id
                )
                attempt.outcome = (
                    "completed"
                    if result is not None and result.outcome == "completed"
                    else "failed"
                )
                attempt.error_code = (
                    None
                    if attempt.outcome == "completed"
                    else "backup_publication_interrupted"
                )
                attempt.finished_at = utcnow()
                session.add(attempt)
            session.commit()
        for run_id in run_ids:
            finish_run(run_id)


def record_verification(
    *, backup_id: str, source_ref: str | None, archive_path: Path, digest: str
) -> None:
    with get_session_factory().scoped_session() as session:
        statement = (
            select(BackupDestinationResult)
            .join(BackupRun)
            .where(
                BackupRun.backup_id == backup_id,
                BackupRun.archive_sha256 == digest,
                BackupDestinationResult.outcome == "completed",
            )
        )
        statement = (
            statement.where(BackupDestinationResult.source_ref == source_ref)
            if source_ref
            else statement.where(
                BackupDestinationResult.kind == "local",
                BackupDestinationResult.key == str(archive_path),
            )
        )
        for result in session.exec(statement).all():
            result.verified_at = utcnow()
            session.add(result)
        session.commit()


def execution_health() -> dict:
    from sqlalchemy import func
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with get_session_factory().scoped_session() as session:
            latest = session.exec(
                select(BackupRun).order_by(col(BackupRun.created_at).desc()).limit(1)
            ).first()
            verified = session.exec(
                select(func.max(BackupDestinationResult.verified_at)).where(
                    BackupDestinationResult.outcome == "completed"
                )
            ).one()
            return {
                "ok": latest is None or latest.outcome not in {"partial", "failed"},
                "latest_run_id": latest.id if latest else None,
                "outcome": latest.outcome if latest else None,
                "last_verified_at": verified.isoformat() if verified else None,
            }
    except SQLAlchemyError:
        return {"ok": False, "error": "backup_execution_state_unavailable"}
