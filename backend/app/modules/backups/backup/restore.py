"""Restore coordination across maintenance, storage and database phases."""

from __future__ import annotations

import secrets
import tarfile
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from sqlalchemy.engine import URL
from sqlmodel import select

import app.modules.backups.backup.archive_format as _archive_format_module
import app.modules.backups.backup.caches as _caches_module
import app.modules.backups.backup.catalogue as _catalogue_module
import app.modules.backups.backup.contracts as _contracts_module
import app.modules.backups.backup.downloads as _downloads_module
import app.modules.backups.backup.recovery as _recovery_module
import app.modules.backups.backup.restore_blobs as _restore_blobs_module
import app.modules.backups.backup.restore_journal as _restore_journal_module
import app.modules.backups.backup.restore_staging as _restore_staging_module
import app.modules.backups.backup.snapshot as _snapshot_module
from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.migrate import run_migrations
from app.db.models import (
    StagingLease,
    User,
)
from app.db.session import get_session_factory
from app.modules.administration import audit
from app.modules.storage import capacity_estimates
from app.modules.storage.capacity import CapacityManager
from app.modules.storage.storage_backend.runtime import get_backend
from app.runtime.jobs import registry
from app.runtime.maintenance import (
    RestoreConflictError,
    begin_restore_maintenance,
    end_restore_maintenance,
    exclusive_backup_operation,
    guarded_destructive_operation,
    restore_in_progress,
)

logger = get_logger(__name__)

_RESTORE_GRACE_PERIOD_S = 2.0


@exclusive_backup_operation
@guarded_destructive_operation
def restore_backup(backup_id: str, *, source_ref: str | None = None) -> dict:
    """Restore a backup with staged blobs and SQLite's online backup API.

    Downloads from S3 if the backup is only in cloud storage.
    WARNING: This replaces the current database. Archived files are created or
    reused when byte-identical; conflicting live storage keys are never
    overwritten.

    Sets a process-wide gate so background loops (GC, external scans, printer
    sync) skip their tick instead of racing the restore. Refuses with
    ``RestoreConflictError`` if ingestion work is still running after a short
    grace period, rather than restoring underneath it.
    """
    _snapshot_module._require_database_backup_support(restore=True)
    pending_journal: _contracts_module._RestoreJournalState | None = None
    pending_journal_path = settings.backup_dir / f".restore-{backup_id}.journal"
    if restore_in_progress():
        recovery_id = _recovery_module.unresolved_restore_backup_id()
        if recovery_id != backup_id:
            raise RestoreConflictError("restore_recovery_required")
        # Validate the current vault destination before discovery, download,
        # reconciliation, or any other operation that could mutate state. A
        # changed remote endpoint must never resume a journal against the same
        # keys on a different provider.
        try:
            pending_journal = _restore_journal_module._load_restore_journal(
                pending_journal_path
            )
            journal_provider_ref = pending_journal.started.get("provider_ref")
            if journal_provider_ref is not None and (
                journal_provider_ref != _restore_journal_module._restore_provider_ref()
            ):
                raise RestoreConflictError("restore_storage_provider_changed")
            if (
                pending_journal.started.get("version") == 1
                and get_backend().backend_name != "local"
            ):
                raise RestoreConflictError("restore_journal_mismatch")
        except RestoreConflictError:
            raise
        except Exception as exc:
            raise RestoreConflictError("restore_storage_provider_unknown") from exc
    meta = _catalogue_module.get_backup(backup_id, source_ref=source_ref)
    if meta is None:
        raise FileNotFoundError(f"backup {backup_id} not found")
    if restore_in_progress():
        # Resolve the pending journal's content identity before recording an
        # audit row or touching the archive. A same-id archive from another
        # provider/prefix must not enter the restore path at all.
        journal_path = pending_journal_path
        try:
            pending = pending_journal or _restore_journal_module._load_restore_journal(
                journal_path
            )
        except Exception as exc:
            raise RestoreConflictError("restore_journal_invalid") from exc
        journal_hash = pending.started.get("archive_sha256")
        if not isinstance(journal_hash, str) or meta.archive_sha256 != journal_hash:
            raise RestoreConflictError("restore_journal_mismatch")

    # Captured before any DB swap: the actor/IP behind this restore, for the
    # post-swap "complete" row (the ambient ContextVar survives the swap, but
    # writing it from a session bound to the restored DB is easiest to read).
    restoring_actor_id, restoring_ip = audit.current_audit_context()
    restored_files = 0
    maintenance_required = False
    restore_cache_path: Path | None = None

    capacity_claim = None
    begin_restore_maintenance()
    try:
        with get_session_factory().session() as session:
            audit.record(
                session,
                action="restore.start",
                resource_type="backup",
                diff={"backup_id": backup_id},
            )

        time.sleep(_RESTORE_GRACE_PERIOD_S)
        counts = registry.snapshot_counts()
        active_jobs = counts["pending"] + counts["running"]
        with get_session_factory().scoped_session() as lease_session:
            active_leases = len(
                lease_session.exec(
                    select(StagingLease).where(StagingLease.expires_at > utcnow())
                ).all()
            )
        if active_jobs or active_leases:
            with get_session_factory().session() as session:
                audit.record(
                    session,
                    action="restore.failed",
                    resource_type="backup",
                    diff={
                        "backup_id": backup_id,
                        "reason": "jobs_running",
                        "running": counts["running"],
                        "pending": counts["pending"],
                        "staging_leases": active_leases,
                    },
                )
            raise RestoreConflictError(
                f"{active_jobs} ingestion job(s) and {active_leases} staging lease(s) active"
            )

        try:
            archive_path = _downloads_module._download_backup_to_local(meta)
            with tarfile.open(archive_path, "r:gz") as capacity_archive:
                expanded_bytes = sum(member.size for member in capacity_archive)
            capacity_claim = CapacityManager(get_session_factory()).reserve(
                f"restore:{backup_id}",
                capacity_estimates.backup_restore(expanded_bytes),
            )
            if meta.location == "s3":
                restore_cache_path = archive_path
            settings.backup_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=".printstash-restore-", dir=settings.backup_dir
            ) as raw_staging_dir:
                staging_dir = Path(raw_staging_dir)
                database_path, staged_blobs = (
                    _restore_staging_module._stage_restore_archive(
                        archive_path, staging_dir
                    )
                )
                # Detect replacement/in-place mutation while the archive was
                # being staged, before any live blob or database mutation.
                _downloads_module._require_backup_archive_owned(meta)
                # Keep the authoritative remote receipt.  The local path is a
                # source-specific cache and must never replace the remote
                # source in the restored ownership ledger.
                archive_ownership = _downloads_module._require_backup_archive_owned(
                    meta
                )
                cache_ownership = (
                    _caches_module._cache_ownership_for_path(archive_path)
                    if restore_cache_path is not None
                    else None
                )
                # Upgrade the private staged copy before touching live bytes.
                # This keeps old backups restorable and guarantees the
                # ownership ledger exists for this operation's receipts.
                run_migrations(str(URL.create("sqlite", database=str(database_path))))
                rollback_dir = staging_dir / "rollback"
                rollback_dir.mkdir()
                journal_path = pending_journal_path
                resuming_journal = journal_path.exists()
                operation_nonce = secrets.token_hex(32)
                journal_state = _restore_journal_module._prepare_restore_journal(
                    journal_path,
                    backup_id=backup_id,
                    archive_sha256=_archive_format_module._sha256_path(archive_path),
                    blobs=staged_blobs,
                    operation_nonce=operation_nonce,
                )
                if restore_cache_path is not None:
                    if str(restore_cache_path) not in journal_state.cache_paths:
                        _restore_journal_module._append_restore_journal(
                            journal_path,
                            {
                                "event": "cache_pinned",
                                "cache_path": str(restore_cache_path),
                                **_restore_journal_module._journal_binding(
                                    journal_state.started
                                ),
                            },
                        )
                        journal_state.cache_paths.add(str(restore_cache_path))
                operation_nonce = str(
                    journal_state.started.get("operation_nonce", operation_nonce)
                )
                archive_sha256 = str(
                    journal_state.started.get(
                        "archive_sha256",
                        _archive_format_module._sha256_path(archive_path),
                    )
                )
                # _prepare_restore_journal upgrades v1 journals before this
                # point; marker proof is always the full nonce + archive hash.
                marker_nonce = operation_nonce

                def active_marker_state() -> bool | None:
                    return _restore_journal_module._active_restore_marker(
                        backup_id,
                        operation_nonce=marker_nonce,
                        archive_sha256=archive_sha256,
                    )

                # The database marker is authoritative if the process died
                # after swapping but before its sidecar journal acknowledgement.
                # Treat that database as active and finish forward.
                # A successful prior restore leaves its marker in the active
                # database. It is evidence for a journal being resumed, not
                # proof that this fresh restore has already swapped databases.
                if resuming_journal and journal_state.database_swap_intent:
                    marker_state = active_marker_state()
                    if marker_state is None:
                        maintenance_required = True
                        raise RestoreConflictError("restore_database_state_unknown")
                    if journal_state.database_active and marker_state is not True:
                        maintenance_required = True
                        raise RestoreConflictError("restore_database_state_unknown")
                    if marker_state is True:
                        # The marker proves the swap happened, but the
                        # sidecar still needs its durable acknowledgement
                        # before the terminal event can be accepted.  Keep
                        # this transition observable on resume so a crash
                        # between the swap and the acknowledgement is not
                        # silently collapsed into a complete journal.
                        if not journal_state.database_active:
                            _restore_journal_module._append_restore_journal(
                                journal_path,
                                {
                                    "event": "database_active",
                                    **_restore_journal_module._journal_binding(
                                        journal_state.started
                                    ),
                                },
                            )
                        journal_state = replace(
                            journal_state,
                            database_active=True,
                        )
                if journal_state.database_swap_intent and {
                    blob.key for blob in staged_blobs
                } != set(journal_state.published):
                    maintenance_required = True
                    raise RestoreConflictError("restore_journal_invalid")
                # A terminal journal can remain when the process crashed while
                # unlinking its sidecar.  The database marker is the only
                # proof that this operation completed; never replay its blob
                # publication or database swap.  If proof is unavailable the
                # maintenance gate stays set for operator recovery.
                if journal_state.complete:
                    marker_state = active_marker_state()
                    if marker_state is not True:
                        maintenance_required = True
                        raise RestoreConflictError("restore_database_state_unknown")
                    _restore_journal_module._remove_restore_journal(journal_path)
                    restored_files = len(staged_blobs)
                    return {
                        "backup_id": backup_id,
                        "restored_files": restored_files,
                    }
                if journal_state.database_active and any(
                    not _restore_blobs_module._stored_blob_matches(blob)
                    for blob in staged_blobs
                ):
                    # After the database marker is active, the journal is a
                    # forward-only recovery record.  A missing or changed
                    # published blob is an unresolved post-swap mutation, not
                    # permission to publish a replacement generation.
                    maintenance_required = True
                    raise RestoreConflictError("restore_destination_changed")
                try:
                    applied, created = _restore_blobs_module._apply_staged_blobs(
                        staged_blobs,
                        rollback_dir,
                        journal_path=journal_path,
                        journal_state=journal_state,
                    )
                except Exception:
                    if not _restore_journal_module._journal_has_mutation_evidence(
                        journal_state
                    ):
                        _restore_journal_module._remove_restore_journal(journal_path)
                    raise
                db_swapped = False
                try:
                    if any(
                        not _restore_blobs_module._stored_blob_matches(blob)
                        for blob in staged_blobs
                    ):
                        raise RestoreConflictError("restore_destination_changed")
                    if not journal_state.database_active:
                        _restore_blobs_module._sync_restored_ownership(
                            database_path,
                            applied,
                            archive_ownership=archive_ownership,
                            cache_ownership=cache_ownership,
                        )
                        _restore_blobs_module._sync_restored_storage_identity(
                            database_path
                        )
                        # Commit an active marker into the staged DB, then
                        # fsync the sidecar swap intent before touching the live
                        # database. The marker is the cross-store PONR proof.
                        _restore_journal_module._stage_restore_marker(
                            database_path,
                            backup_id,
                            operation_nonce=operation_nonce,
                            archive_sha256=archive_sha256,
                        )
                        # A retry of a journal that already recorded the swap
                        # intent must reuse that durable intent.  Appending a
                        # second one would make the journal invalid and, more
                        # importantly, would erase the ordering proof around
                        # the database point of no return.
                        if not journal_state.database_swap_intent:
                            _restore_journal_module._append_restore_journal(
                                journal_path,
                                {
                                    "event": "database_swap_intent",
                                    **_restore_journal_module._journal_binding(
                                        journal_state.started
                                    ),
                                },
                            )
                        # Restore the DB last. Until this succeeds, rollback
                        # can put every touched blob back under the
                        # still-current database.
                        try:
                            _snapshot_module._restore_database_from_path(database_path)
                        except Exception as exc:
                            # SQLite's online backup can fail after replacing
                            # the destination. Always query the marker before
                            # deciding whether blob rollback is safe.
                            marker_state = active_marker_state()
                            if marker_state is True:
                                # The swap is complete despite the reporting
                                # exception. Continue forward and acknowledge
                                # the durable marker; never retract blobs.
                                db_swapped = True
                                journal_state = replace(
                                    journal_state,
                                    database_swap_intent=True,
                                    database_active=True,
                                )
                            elif marker_state is None:
                                db_swapped = True
                                maintenance_required = True
                                raise RestoreConflictError(
                                    "restore_database_state_unknown"
                                ) from exc
                            else:
                                # The marker proves the old database remains
                                # active, so pre-PONR rollback is safe.
                                raise
                        if not db_swapped:
                            active_marker = active_marker_state()
                            if active_marker is False:
                                raise RestoreConflictError(
                                    "restore_database_swap_not_active"
                                )
                            if active_marker is None:
                                # We cannot prove whether the replacement
                                # happened; preserving blobs is the only safe
                                # recovery action.
                                db_swapped = True
                                maintenance_required = True
                                raise RestoreConflictError(
                                    "restore_database_state_unknown"
                                )
                            db_swapped = True
                        # Database replacement is the restore point of no
                        # return. A journal acknowledgement failure after this
                        # point must never retract bytes referenced by the
                        # marker. A later retry can finish the journal.
                        if not journal_state.database_active:
                            _restore_journal_module._append_restore_journal(
                                journal_path,
                                {
                                    "event": "database_active",
                                    **_restore_journal_module._journal_binding(
                                        journal_state.started
                                    ),
                                },
                            )
                            journal_state = replace(journal_state, database_active=True)
                    else:
                        db_swapped = True
                    _restore_journal_module._append_restore_journal(
                        journal_path,
                        {
                            "event": "complete",
                            **_restore_journal_module._journal_binding(
                                journal_state.started
                            ),
                        },
                    )
                    _restore_journal_module._remove_restore_journal(journal_path)
                except Exception:
                    if not db_swapped:
                        _restore_blobs_module._rollback_applied_blobs(
                            created, journal_path=journal_path
                        )
                        raise
                    if maintenance_required:
                        # Marker lookup itself failed. Preserve the original
                        # unknown outcome and the staged bytes for operator
                        # recovery; do not mask it as a generic ack failure.
                        raise
                    # The marker proves the new database owns the staged
                    # bytes. Keep maintenance enabled and leave the journal
                    # for a forward retry when acknowledgement or cleanup
                    # fails; never roll those bytes back.
                    maintenance_required = True
                    raise RestoreConflictError(
                        "restore_post_swap_recovery_required"
                    ) from None
                restored_files = len(staged_blobs)
        except Exception:
            try:
                with get_session_factory().session() as session:
                    audit.record(
                        session,
                        action="restore.failed",
                        resource_type="backup",
                        diff={"backup_id": backup_id, "reason": "restore_error"},
                    )
            except Exception:
                logger.exception(
                    "restore %s failed and audit recording also failed", backup_id
                )
            raise
    finally:
        if capacity_claim is not None and not maintenance_required:
            capacity_claim.release()
        if not maintenance_required and not _recovery_module._restore_journal_pending():
            if restore_cache_path is not None:
                _caches_module.cleanup_backup_cache(restore_cache_path)
            end_restore_maintenance()
        else:
            logger.critical(
                "restore outcome is unknown; leaving the application in maintenance mode",
                extra={"backup_id": backup_id},
            )

    logger.info("backup %s restored: %d files", backup_id, restored_files)

    # Written against the now-restored database. The pre-restore actor may not
    # exist there (an older/different backup's users table), so validate
    # before trusting the id — a foreign-key violation here must not turn a
    # successful restore into a failure.
    with get_session_factory().session() as session:
        safe_actor_id = (
            restoring_actor_id
            if restoring_actor_id is not None
            and session.get(User, restoring_actor_id) is not None
            else None
        )
        audit.record(
            session,
            action="restore.complete",
            resource_type="backup",
            actor_id=safe_actor_id,
            ip=restoring_ip,
            diff={"backup_id": backup_id, "restored_files": restored_files},
        )

    return {
        "backup_id": backup_id,
        "restored_files": restored_files,
    }
