"""Staging cleanup."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    StagingLease,
)
from app.modules.storage.storage_backend.contracts import (
    StorageBackend,
)

from .staging_leases import (
    _entry_present,
    _quarantine_entry_path,
    _quarantine_owned_file,
)


def prune_expired(
    session: Session,
    *,
    now: datetime | None = None,
    backend: StorageBackend | None = None,
) -> tuple[int, int]:
    """Remove expired rows; unlink only exact files. Returns (rows, files)."""
    from app.modules.ingestion import review_manifests
    review_manifests.prune_expired(session)
    timestamp = now or utcnow()
    rows = list(
        session.exec(select(StagingLease).where(StagingLease.expires_at <= timestamp))
    )
    removed = unlinked = 0
    for lease in rows:
        if lease.background_job_id:
            from app.modules.ingestion.commands import retains_staging
            if retains_staging(session, lease.background_job_id):
                continue
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
