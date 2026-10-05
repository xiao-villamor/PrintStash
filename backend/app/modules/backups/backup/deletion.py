"""Identity-verified archive retirement and deletion."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import app.modules.backups.backup.catalogue as _catalogue_module
import app.modules.backups.backup.contracts as _contracts_module
import app.modules.backups.backup.downloads as _downloads_module
import app.modules.backups.backup.targets as _targets_module
import app.modules.backups.backup_destination as _backup_destination_module
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_deletion import (
    PreparedOwnedDeletion,
    enqueue_prepared_owned_deletion,
    enqueue_prevalidated_receipt,
    process_storage_delete_intents,
)
from app.modules.storage.storage_ownership import delete_owned_key, require_owned_key
from app.modules.storage.storage_publication import PublicationReservation
from app.modules.storage.storage_receipts import owned_receipt

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def _retire_backup_receipt(session, owned) -> bool:
    receipt = owned_receipt(owned)
    prepared = PreparedOwnedDeletion(
        PublicationReservation.of(owned),
        receipt,
        owned.object_kind,
        owned.sha256,
        "verified",
        "backup",
        owned.id,
    )
    enqueue_prepared_owned_deletion(session, prepared, required_proof=True)
    intent = enqueue_prevalidated_receipt(
        session,
        receipt,
        object_kind=owned.object_kind,
        sha256=owned.sha256,
        resource_kind="backup",
        resource_id=owned.id,
    )
    assert intent.id is not None
    identifier = intent.id
    session.commit()
    return process_storage_delete_intents(intent_ids=(identifier,)).completed == 1


def delete_backup(
    backup_id: str,
    *,
    source_ref: str | None = None,
) -> bool:
    """Delete exactly the source authorized by ``source_ref``."""
    meta = _catalogue_module.get_backup(backup_id, source_ref=source_ref)
    if meta is None:
        return False

    deleted = False
    with get_session_factory().session() as session:
        local_backend = LocalStorageBackend()
        if meta.location == "local":
            try:
                local_backend.verify_destructive_access([meta.path])
                require_owned_key(session, local_backend, meta.path)
            except Exception as exc:
                raise _contracts_module.BackupOwnershipError(
                    "backup_storage_ownership_unverified"
                ) from exc
            deleted = delete_owned_key(session, local_backend, meta.path)
        elif meta.location.startswith("opendal:"):
            owned = _downloads_module._require_backup_archive_owned(meta)
            destination = _backup_destination_module.destination_for_ownership(owned)
            if destination is None:
                raise _contracts_module.BackupOwnershipError(
                    "backup_storage_ownership_unverified"
                )
            if not destination.can_delete_receipt(owned_receipt(owned)):
                raise _contracts_module.BackupDeleteUnsupportedError()
            deleted = _retire_backup_receipt(session, owned)
        else:
            target = _targets_module._get_backup_s3_target()
            if target is None:
                raise _contracts_module.BackupOwnershipError(
                    "backup_storage_ownership_unverified"
                )
            owned = _downloads_module._require_backup_archive_owned(meta, target=target)
            if not target.bucket:
                target = replace(target, bucket=owned.namespace.split("/", 1)[0])
            if not owned.version_id or owned.version_id == "null":
                raise _contracts_module.BackupDeleteUnsupportedError()
            deleted = _retire_backup_receipt(session, owned)
            if deleted:
                # A cloud download is a rebuildable derivative.  Remove only
                # the cache derived from this exact source locator and only
                # when its own local receipt still proves the bytes; sibling
                # source caches and canonical local backups are untouched.
                cache_ref = meta.source_ref or _targets_module.source_reference(
                    location="s3",
                    namespace=owned.namespace,
                    path=owned.key,
                    provider_ref=owned.provider_ref,
                )
                cache_identity = owned.version_id or owned.etag or owned.sha256
                if cache_identity:
                    cache_ref = hashlib.sha256(
                        f"{cache_ref}\x1f{cache_identity}".encode("utf-8")
                    ).hexdigest()
                cache_path = (
                    settings.backup_dir
                    / ".cloud-cache"
                    / f"{cache_ref}-{meta.path.rsplit('/', 1)[-1]}"
                )
                if cache_path.exists():
                    delete_owned_key(session, local_backend, str(cache_path))
        session.commit()

    if deleted:
        logger.info("backup %s deleted", backup_id)
    return deleted


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


def purge_old_backups(retain_days: int | None = None) -> int:
    """Remove backups older than the retention period (local + S3)."""
    if retain_days is None:
        retain_days = settings.backup_retention_days
    if retain_days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(retain_days, 1))
    removed = 0

    for meta in _catalogue_module.list_backup_sources():
        try:
            created = datetime.fromisoformat(meta.created_at)
            if created < cutoff:
                if delete_backup(meta.id, source_ref=meta.source_ref):
                    removed += 1
        except _contracts_module.BackupDeleteUnsupportedError:
            # Unsupported retention is a stable capability result shown with
            # the source. It is not a recurring provider failure.
            continue
        except Exception as exc:
            # Retention is best effort per exact source.  A stale credential,
            # provider outage, or ownership conflict must never make us probe
            # another source or abort the remaining purge.  Keep diagnostics
            # secret-safe: source_ref is an opaque digest and exception text
            # may contain provider URLs or credentials.
            logger.warning(
                "backup purge: source %s could not be removed (%s)",
                meta.source_ref or "unknown",
                type(exc).__name__,
            )
            continue

    if removed:
        logger.info("backup purge: removed %d old backups", removed)
    return removed
