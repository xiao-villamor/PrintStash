"""Startup reconciliation and fail-closed expiry for Artifact uploads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import (
    ArtifactUploadSession,
    ArtifactUploadState,
    BackgroundJob,
)
from app.db.session import SessionFactory, get_session_factory

from .manager import SqlArtifactUploadManager


@dataclass(frozen=True)
class ArtifactUploadRecoveryResult:
    reconciled: int = 0
    expired: int = 0
    retained: int = 0


def _expire_one(
    session: Session,
    manager: SqlArtifactUploadManager,
    upload: ArtifactUploadSession,
) -> bool:
    """Remove only adapter-proven owned bytes before terminalizing expiry."""

    if upload.adapter_id != manager.adapter.adapter_id:
        manager.transition(
            upload,
            ArtifactUploadState.FAILED,
            error_code="artifact_upload_cleanup_unproven",
            retryable=True,
        )
        return False
    try:
        manager.adapter.abort_owned(upload)
    except OSError:
        manager.transition(
            upload,
            ArtifactUploadState.FAILED,
            error_code="artifact_upload_cleanup_unproven",
            retryable=True,
        )
        return False
    for part in manager.parts(upload):
        session.delete(part)
    session.flush()
    manager.transition(
        upload,
        ArtifactUploadState.EXPIRED,
        received_bytes=0,
        error_code="artifact_upload_expired",
        retryable=False,
    )
    return True


def reconcile_artifact_uploads(
    session_factory: SessionFactory | None = None,
    *,
    now: datetime | None = None,
) -> ArtifactUploadRecoveryResult:
    """Reconcile terminal jobs and expire inactive session-owned staging."""

    factory = session_factory or get_session_factory()
    timestamp = now or utcnow()
    reconciled = expired = retained = 0
    with factory.scoped_session() as session:
        manager = SqlArtifactUploadManager(session)
        ingesting = list(
            session.exec(
                select(ArtifactUploadSession).where(
                    ArtifactUploadSession.state == ArtifactUploadState.INGESTING
                )
            )
        )
        for upload in ingesting:
            job = (
                session.get(BackgroundJob, upload.background_job_id)
                if upload.background_job_id
                else None
            )
            if job is not None and job.state == "completed":
                manager.transition(
                    upload,
                    ArtifactUploadState.COMPLETED,
                    error_code=None,
                    retryable=False,
                )
                try:
                    manager.adapter.abort_owned(upload)
                except OSError:
                    # The canonical Artifact is already committed. Retain the
                    # private staging directory for a later exact cleanup pass.
                    pass
                reconciled += 1
            elif job is None or job.state == "failed":
                manager.transition(
                    upload,
                    ArtifactUploadState.FAILED,
                    error_code="artifact_upload_ingestion_interrupted",
                    retryable=True if job is None else bool(_job_retryable(job)),
                )
                reconciled += 1

        expirable = list(
            session.exec(
                select(ArtifactUploadSession).where(
                    col(ArtifactUploadSession.state).in_(
                        (
                            ArtifactUploadState.CREATED,
                            ArtifactUploadState.UPLOADING,
                            ArtifactUploadState.VERIFYING,
                            ArtifactUploadState.FAILED,
                        )
                    ),
                    ArtifactUploadSession.expires_at <= timestamp,
                )
            )
        )
        for upload in expirable:
            if _expire_one(session, manager, upload):
                expired += 1
            else:
                retained += 1
    return ArtifactUploadRecoveryResult(
        reconciled=reconciled,
        expired=expired,
        retained=retained,
    )


def _job_retryable(job: BackgroundJob) -> bool:
    import json

    try:
        payload = json.loads(job.status_json)
    except (TypeError, ValueError):
        return True
    return bool(payload.get("retryable", True))
