"""Backup download cache retirement and cleanup."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

from sqlmodel import select

import app.modules.backups.backup.restore_journal as _restore_journal_module
from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    OwnedStorageObject,
    StorageObjectState,
)
from app.db.session import get_session_factory
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_ownership import (
    complete_publication,
    delete_owned_key,
    provider_ref_for_backend,
    settle_observed_publication,
)
from app.modules.storage.storage_publication import PublicationReservation
from app.runtime.maintenance import RestoreConflictError

logger = get_logger(__name__)

# A committed cache receipt can survive a process crash after publication.
# Once it is no longer referenced by a valid restore journal, retain it only
# for a bounded recovery window before reclaiming the rebuildable derivative.
_BACKUP_CACHE_STALE_AFTER_S = 24 * 60 * 60


def _cache_path_pinned_by_restore_journal(path: str) -> bool:
    """Return whether an unresolved restore names this exact cache locator."""
    try:
        journals = settings.backup_dir.glob(".restore-*.journal")
        for journal in journals:
            try:
                state = _restore_journal_module._load_restore_journal(journal)
            except RestoreConflictError:
                # Arbitrary or malformed text is not ownership evidence for a
                # cache.  It may keep restore maintenance fail-closed, but it
                # must not pin an unrelated derivative forever.
                continue
            if str(path) in state.cache_paths:
                return True
        return False
    except OSError:
        # An unreadable journal directory is already fail-closed for restore;
        # retaining the cache is the safe choice for reconciliation as well.
        return True


def reconcile_backup_caches(limit: int = 100) -> int:
    """Reconcile only crash-staged per-source cloud cache projections.

    Cache rows are deliberately not handled by the generic storage sweep: a
    journal may still need the exact file after a database swap.  Missing rows
    are safe to clear; present rows are never deleted on a hash mismatch.
    """
    reconciled = 0
    backend = LocalStorageBackend()
    with get_session_factory().session() as session:
        rows = session.exec(
            select(OwnedStorageObject)
            .where(
                OwnedStorageObject.backend == "local",
                OwnedStorageObject.object_kind == "backup-cloud-cache",
                OwnedStorageObject.state.in_(
                    (StorageObjectState.PENDING, StorageObjectState.COMMITTED)
                ),
            )
            .order_by(OwnedStorageObject.id.asc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
        for row in rows:
            publication_reservation = PublicationReservation.of(row)
            observed_state = row.state

            def settle(
                state,
                error=None,
                *,
                reservation=publication_reservation,
                expected_state=observed_state,
            ):
                settle_observed_publication(
                    session,
                    reservation,
                    observed_state=expected_state,
                    state=state,
                    last_error=error,
                )
                session.commit()

            if _cache_path_pinned_by_restore_journal(row.key):
                continue
            path = Path(row.key).resolve(strict=False)
            cache_root = (settings.backup_dir / ".cloud-cache").resolve(strict=False)
            if path.parent != cache_root or not path.name:
                settle(StorageObjectState.BLOCKED, "backup_cache_path_invalid")
                continue
            if not path.exists():
                if settle_observed_publication(
                    session,
                    publication_reservation,
                    observed_state=observed_state,
                    state=StorageObjectState.RETIRING,
                    last_error=None,
                ):
                    reconciled += 1
                session.commit()
                continue
            if row.state == StorageObjectState.COMMITTED:
                created_at = row.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age_s = (utcnow() - created_at).total_seconds()
                if age_s >= _BACKUP_CACHE_STALE_AFTER_S:
                    # A committed cache is a rebuildable projection, not a
                    # root backup.  Reclaim only an exact, still-provable
                    # receipt; pinned journals were filtered above.
                    try:
                        if delete_owned_key(session, backend, str(path)):
                            reconciled += 1
                    except Exception as exc:
                        settle(StorageObjectState.BLOCKED, type(exc).__name__[:255])
                    continue
            if row.size_bytes is None or not row.sha256:
                settle(StorageObjectState.BLOCKED, "backup_cache_evidence_missing")
                continue
            try:
                receipt = backend.adopt_existing(
                    str(path),
                    expected_size=row.size_bytes,
                    expected_sha256=row.sha256,
                )
                if row.state == StorageObjectState.PENDING:
                    complete_publication(
                        session,
                        publication_reservation,
                        receipt,
                        object_kind="backup-cloud-cache",
                        sha256=row.sha256,
                        provider_ref=provider_ref_for_backend(
                            backend, namespace=backend.namespace_for(str(path))
                        ),
                    )
                    session.commit()
                reconciled += 1
                session.commit()
            except Exception as exc:
                settle(StorageObjectState.BLOCKED, type(exc).__name__[:255])
        session.commit()
    return reconciled


def _cache_ownership_for_path(path: Path) -> OwnedStorageObject | None:
    """Load the exact committed/pending receipt for one cache path."""
    backend = LocalStorageBackend()
    namespace = backend.namespace_for(str(path))
    with get_session_factory().session() as session:
        row = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == "local",
                OwnedStorageObject.namespace == namespace,
                OwnedStorageObject.key == str(path),
                OwnedStorageObject.object_kind == "backup-cloud-cache",
                OwnedStorageObject.state.in_(
                    (StorageObjectState.PENDING, StorageObjectState.COMMITTED)
                ),
            )
        ).first()
        if row is None:
            return None
        # Return a detached value so the staged database copy remains usable
        # after the source session closes.
        return OwnedStorageObject.model_validate(row.model_dump())


def cleanup_backup_cache(path: Path) -> None:
    """Remove one per-source cloud derivative after its consumer is done."""
    cache_root = (settings.backup_dir / ".cloud-cache").resolve(strict=False)
    candidate = path.resolve(strict=False)
    if candidate.parent != cache_root or not candidate.name:
        return
    if _cache_path_pinned_by_restore_journal(str(candidate)):
        return
    with get_session_factory().session() as session:
        delete_owned_key(session, LocalStorageBackend(), str(candidate))
        session.commit()
