"""Staging cleanup."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import (
    ACTIVE_JOB_STATES,
    File,
    IngestRequest,
    IngestRequestKind,
    Job,
    JobState,
    StagingLease,
    User,
)
from app.modules.storage.storage_backend.contracts import (
    StorageBackend,
)

from .staging_leases import (
    _entry_present,
    _quarantine_entry_path,
    _quarantine_owned_file,
)


def release_lease(session: Session, lease: StagingLease) -> bool:
    """Release an exact receipt, preserving replacements and uncertain ownership."""
    if lease.capture_upload_slot_origin_id is not None:
        return False
    path = Path(lease.path)
    if lease.device is not None and lease.inode is not None:
        if _quarantine_owned_file(
            path,
            receipt_id=lease.id,
            device=lease.device,
            inode=lease.inode,
            ctime_ns=lease.ctime_ns,
            size_bytes=lease.size_bytes,
        ):
            session.delete(lease)
            return True
    if not _entry_present(path) and not _entry_present(
        _quarantine_entry_path(path, lease.id)
    ):
        session.delete(lease)
        return True
    return False


def release_job(session: Session, job_id: str) -> int:
    leases = session.exec(
        select(StagingLease).where(StagingLease.job_id == job_id)
    ).all()
    released = sum(release_lease(session, lease) for lease in leases)
    session.flush()
    return released


def reconcile_jobs(session: Session, *, job_id: str | None = None) -> int:
    """Reclaim committed imports; retain active/review/failed inputs until expiry."""
    import json

    statement = (
        select(Job, IngestRequest)
        .join(IngestRequest, col(IngestRequest.job_id) == col(Job.id))
        .where(
            col(Job.state).in_(
                (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED)
            )
        )
    )
    statement = statement.where(
        col(Job.id).in_(
            select(StagingLease.job_id).where(
                col(StagingLease.capture_upload_slot_origin_id).is_(None)
            )
        )
    )
    if job_id is not None:
        statement = statement.where(Job.id == job_id)
    released = 0
    for job, request in session.exec(statement).all():
        if request.kind == IngestRequestKind.UPLOAD:
            selection = json.loads(request.selection_json)
            key = selection.get("ingestion_key")
            if key is None:
                key = job.id
            committed = session.exec(
                select(File.id).where(File.ingestion_key == key)
            ).first()
            if committed is not None:
                released += release_job(session, job.id)
        elif request.kind == IngestRequestKind.ARCHIVE_SELECTION:
            payload = json.loads(job.status_json)
            if (
                job.state == JobState.COMPLETED
                and payload.get("completion") == "complete"
            ):
                released += release_job(session, job.id)
        # Archive inspection produces a review manifest, not committed imports.
    return released


def discard(job_id: str, *, actor: User) -> None:
    """Serialize discard and retry on the Job row, without exposing a pathname."""
    import json

    from app.core.errors import ErrorKind, OperationError
    from app.db.session import get_session_factory
    from app.db.transactions import begin_write
    from app.modules.work.jobs import status_of
    from app.modules.work.service import visible_to

    from .staging_views import _releasable

    with get_session_factory().scoped_session() as session:
        begin_write(session, immediate=True)
        row = session.exec(
            select(Job).where(Job.id == job_id).with_for_update()
        ).first()
        if row is None or not visible_to(status_of(row), actor):
            raise OperationError("job_not_found", kind=ErrorKind.NOT_FOUND)
        request = session.get(IngestRequest, job_id)
        if request is None or not status_of(row).terminal:
            raise OperationError("staging_job_not_terminal", kind=ErrorKind.CONFLICT)
        leases = list(
            session.exec(select(StagingLease).where(StagingLease.job_id == job_id))
        )
        if not all(_releasable(lease) for lease in leases):
            raise OperationError("staging_ownership_uncertain", kind=ErrorKind.CONFLICT)
        uncertain = False
        for lease in leases:
            if not release_lease(session, lease):
                uncertain = True
        payload = json.loads(row.status_json)
        payload["retryable"] = False
        row.status_json = json.dumps(payload, separators=(",", ":"))
        session.add(row)
        # Cleanup is a filesystem mutation: retain progress if a later receipt
        # loses its proof. The remaining uncertain lease stays charged.
        session.commit()
        if uncertain:
            raise OperationError("staging_ownership_uncertain", kind=ErrorKind.CONFLICT)


def prune_expired(
    session: Session,
    *,
    now: datetime | None = None,
    backend: StorageBackend | None = None,
) -> tuple[int, int]:
    """Remove expired rows; unlink only exact files. Returns (rows, files)."""
    timestamp = now or utcnow()
    rows = list(
        session.exec(
            select(StagingLease).where(
                StagingLease.expires_at <= timestamp,
                or_(
                    col(StagingLease.job_id).is_(None),
                    col(StagingLease.job_id).not_in(
                        select(Job.id).where(col(Job.state).in_(ACTIVE_JOB_STATES))
                    ),
                ),
            )
        )
    )
    removed = unlinked = 0
    for lease in rows:
        if lease.model_source_cover_id is not None:
            # A cover lease never represents a local path. Reconcile its
            # backend-native publication first; if no bytes were published,
            # the reconciler removes the broken cover row and stale proof too.
            # A mismatched or unavailable object remains leased for retry —
            # expiry must never become an unverified delete.
            from app.modules.library import source_covers
            from app.modules.storage.storage_backend.runtime import get_backend

            if source_covers.expire_pending(
                session,
                backend or get_backend(),
                lease=lease,
            ):
                removed += 1
            continue
        path = Path(lease.path)
        quarantine = _quarantine_entry_path(path, lease.id)
        if lease.device is not None and lease.inode is not None:
            if _quarantine_owned_file(
                path,
                receipt_id=lease.id,
                device=lease.device,
                inode=lease.inode,
                ctime_ns=lease.ctime_ns,
                size_bytes=lease.size_bytes,
            ):
                unlinked += 1
                session.delete(lease)
                removed += 1
                continue
            if _entry_present(quarantine) or _entry_present(path):
                continue
        try:
            path.lstat()
        except FileNotFoundError:
            session.delete(lease)
            removed += 1
            continue
        except OSError:
            pass
        # Keep uncertain rows charged. A replaced or inaccessible pathname is
        # not evidence that this lease's bytes were safely reclaimed.
        continue
    session.flush()
    return removed, unlinked
