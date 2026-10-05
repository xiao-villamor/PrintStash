"""Restore blob publication and exact-receipt compensation."""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

import app.modules.backups.backup.archive_format as _archive_format_module
import app.modules.backups.backup.contracts as _contracts_module
import app.modules.backups.backup.restore_journal as _restore_journal_module
from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    OwnedStorageObject,
)
from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import provider_ref_for_backend
from app.runtime.maintenance import RestoreConflictError

logger = get_logger(__name__)


def _write_staged_blob(staged_path: Path, key: str) -> int:
    with staged_path.open("rb") as source:
        return get_backend().create_stream(source, key).size


def _rollback_applied_blobs(
    applied: list[_contracts_module._AppliedBlob], *, journal_path: Path | None = None
) -> None:
    backend = get_backend()
    journal_binding: dict[str, object] = {}
    if journal_path is not None:
        try:
            state = _restore_journal_module._load_restore_journal(journal_path)
        except RestoreConflictError:
            state = None
        if (
            state is not None
            and state.started.get("version")
            == _contracts_module._RESTORE_JOURNAL_VERSION
        ):
            journal_binding = _restore_journal_module._journal_binding(state.started)
    for item in reversed(applied):
        try:
            removed = backend.rollback_create(item.receipt)
            if removed and journal_path is not None:
                _restore_journal_module._append_restore_journal(
                    journal_path,
                    {
                        "event": "retracted",
                        "key": item.key,
                        "generation": item.generation,
                        **journal_binding,
                    },
                )
            if not removed:
                logger.error(
                    "restore rollback preserved uncertain storage key %s", item.key
                )
        except Exception:
            logger.exception("restore rollback failed for storage key %s", item.key)


def _sync_restored_ownership(
    database_path: Path,
    applied: list[_contracts_module._AppliedBlob],
    *,
    archive_ownership: OwnedStorageObject,
    cache_ownership: OwnedStorageObject | None = None,
) -> None:
    """Replace archived fingerprints with proof from this restore operation."""
    with sqlite3.connect(database_path) as connection:
        for item in applied:
            receipt = item.receipt
            current_provider_ref = provider_ref_for_backend(
                get_backend(), namespace=receipt.namespace
            )
            existing = connection.execute(
                """
                SELECT object_kind FROM owned_storage_objects
                WHERE backend = ? AND namespace = ? AND key = ?
                  AND provider_ref = ? AND state != 'retiring' LIMIT 1
                """,
                (receipt.backend, receipt.namespace, item.key, current_provider_ref),
            ).fetchone()
            object_kind = str(existing[0]) if existing else "restored"
            # Archived inode/ETag values prove an old object, not the one just
            # created. Replace them with this operation's current receipt.
            connection.execute(
                """
                UPDATE owned_storage_objects SET state = 'retiring', next_recovery_at = NULL
                WHERE backend = ? AND namespace = ? AND key = ?
                  AND provider_ref = ?
                """,
                (receipt.backend, receipt.namespace, item.key, current_provider_ref),
            )
            connection.execute(
                """
                INSERT INTO owned_storage_objects (
                    backend, namespace, key, provider_ref, object_kind, state, token,
                    size_bytes, sha256, etag, version_id, device, inode, ctime_ns,
                    committed_at, created_at, publication_generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.backend,
                    receipt.namespace,
                    receipt.key,
                    provider_ref_for_backend(
                        get_backend(), namespace=receipt.namespace
                    ),
                    object_kind,
                    "committed",
                    receipt.token,
                    receipt.size,
                    item.sha256,
                    receipt.etag,
                    receipt.version_id,
                    receipt.device,
                    receipt.inode,
                    receipt.ctime_ns,
                    utcnow().isoformat(sep=" "),
                    utcnow().isoformat(sep=" "),
                    secrets.token_hex(32),
                ),
            )
        connection.execute(
            """
            UPDATE owned_storage_objects SET state = 'retiring', next_recovery_at = NULL
            WHERE backend = ? AND namespace = ? AND key = ?
              AND provider_ref IS ?
            """,
            (
                archive_ownership.backend,
                archive_ownership.namespace,
                archive_ownership.key,
                archive_ownership.provider_ref,
            ),
        )
        connection.execute(
            """
            INSERT INTO owned_storage_objects (
                backend, namespace, key, provider_ref, object_kind, state, token,
                size_bytes, sha256, etag, version_id, device, inode, ctime_ns,
                committed_at, created_at, last_error, publication_generation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                archive_ownership.backend,
                archive_ownership.namespace,
                archive_ownership.key,
                archive_ownership.provider_ref,
                archive_ownership.object_kind,
                archive_ownership.state.value,
                archive_ownership.token,
                archive_ownership.size_bytes,
                archive_ownership.sha256,
                archive_ownership.etag,
                archive_ownership.version_id,
                archive_ownership.device,
                archive_ownership.inode,
                archive_ownership.ctime_ns,
                archive_ownership.committed_at,
                archive_ownership.created_at,
                archive_ownership.last_error,
                archive_ownership.publication_generation,
            ),
        )
        if cache_ownership is not None:
            # The cache is a rebuildable derivative, but while this restore is
            # unresolved it is still an exact source locator.  Carrying its
            # receipt into the staged DB makes terminal cleanup work after the
            # database swap instead of orphaning the private cache file.
            connection.execute(
                """
                UPDATE owned_storage_objects SET state = 'retiring', next_recovery_at = NULL
                WHERE backend = ? AND namespace = ? AND key = ?
                  AND provider_ref IS ?
                """,
                (
                    cache_ownership.backend,
                    cache_ownership.namespace,
                    cache_ownership.key,
                    cache_ownership.provider_ref,
                ),
            )
            connection.execute(
                """
                INSERT INTO owned_storage_objects (
                    backend, namespace, key, provider_ref, object_kind, state, token,
                    size_bytes, sha256, etag, version_id, device, inode, ctime_ns,
                    committed_at, created_at, last_error, publication_generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_ownership.backend,
                    cache_ownership.namespace,
                    cache_ownership.key,
                    cache_ownership.provider_ref,
                    cache_ownership.object_kind,
                    cache_ownership.state.value,
                    cache_ownership.token,
                    cache_ownership.size_bytes,
                    cache_ownership.sha256,
                    cache_ownership.etag,
                    cache_ownership.version_id,
                    cache_ownership.device,
                    cache_ownership.inode,
                    cache_ownership.ctime_ns,
                    cache_ownership.committed_at,
                    cache_ownership.created_at,
                    cache_ownership.last_error,
                    cache_ownership.publication_generation,
                ),
            )
        connection.commit()


def _sync_restored_storage_identity(database_path: Path) -> None:
    """Carry the current installation binding into a restored database.

    v1/v2 databases predate the durable identity column (or contain a NULL
    value).  Swapping one in while the existing mount sentinel retains the
    current identity would otherwise make the next startup classify every
    managed root as a foreign mount.  The target identity is trusted only from
    the already-running, validated installation and is written before the DB
    swap PONR.
    """
    identity = str(getattr(settings, "storage_identity", "") or "").strip()
    if not identity:
        return
    with sqlite3.connect(database_path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(system_config)")
        }
        if "storage_identity" not in columns:
            return
        connection.execute(
            "UPDATE system_config SET storage_identity = ? WHERE id = 1",
            (identity,),
        )
        connection.commit()


def _apply_staged_blobs(
    blobs: list[_contracts_module._StagedBlob],
    rollback_dir: Path,
    *,
    journal_path: Path | None = None,
    journal_state: _contracts_module._RestoreJournalState | None = None,
) -> tuple[list[_contracts_module._AppliedBlob], list[_contracts_module._AppliedBlob]]:
    """Publish a restore into empty or byte-identical, in-bound destinations.

    Restore used to overwrite every manifest key and then attempt a best-effort
    rollback. A malicious/stale manifest or remapped root could therefore
    clobber unrelated bytes. Conflicting restores now fail before the first
    write. Existing bytes are reused only when their size and SHA-256 match the
    archive, and that adoption is journalled before it can affect ownership.
    """
    del rollback_dir
    backend = get_backend()
    applied: list[_contracts_module._AppliedBlob] = []
    created: list[_contracts_module._AppliedBlob] = []

    seen: set[str] = set()
    matching_existing: list[_contracts_module._StagedBlob] = []
    for blob in blobs:
        _archive_format_module._validate_restore_key(blob.key)
        if blob.key in seen:
            raise RestoreConflictError("restore_duplicate_destination")
        seen.add(blob.key)
        if not backend.exists(blob.key):
            continue
        intent = journal_state.intents.get(blob.key) if journal_state else None
        if intent is None:
            if not _stored_blob_matches(blob):
                raise RestoreConflictError("restore_destination_exists")
            matching_existing.append(blob)
            continue
        if not _restore_journal_module._journal_intent_matches(intent, blob):
            raise RestoreConflictError("restore_destination_exists")
        if not _stored_blob_matches(blob):
            raise RestoreConflictError("restore_destination_changed")

    # Complete collision preflight before recording adoption intent. This
    # keeps a later conflicting key from leaving partial restore evidence.
    for blob in matching_existing:
        if journal_path is None or journal_state is None:
            raise RestoreConflictError("restore_destination_exists")
        generation = journal_state.generations.get(blob.key, 0) + 1
        adoption_intent: dict[str, object] = {
            "event": "intent",
            "key": blob.key,
            "size": blob.size,
            "sha256": blob.sha256,
            "namespace": blob.namespace,
            "generation": generation,
        }
        if (
            journal_state.started.get("version")
            == _contracts_module._RESTORE_JOURNAL_VERSION
        ):
            adoption_intent.update(
                _restore_journal_module._journal_binding(journal_state.started)
            )
        _restore_journal_module._append_restore_journal(journal_path, adoption_intent)
        journal_state.intents[blob.key] = adoption_intent
        journal_state.generations[blob.key] = generation

    try:
        for blob in blobs:
            intent = journal_state.intents.get(blob.key) if journal_state else None
            published = journal_state.published.get(blob.key) if journal_state else None
            if backend.exists(blob.key):
                receipt = _adopt_restored_blob(blob, published)
                assert intent is not None
                item = _contracts_module._AppliedBlob(
                    key=blob.key,
                    receipt=receipt,
                    sha256=blob.sha256,
                    generation=_restore_journal_module._journal_generation(intent),
                )
                applied.append(item)
                if journal_path is not None and published is None:
                    event = _restore_journal_module._published_restore_event(item)
                    if (
                        journal_state is not None
                        and journal_state.started.get("version")
                        == _contracts_module._RESTORE_JOURNAL_VERSION
                    ):
                        event.update(
                            _restore_journal_module._journal_binding(
                                journal_state.started
                            )
                        )
                    _restore_journal_module._append_restore_journal(journal_path, event)
                    if journal_state is not None:
                        journal_state.published[blob.key] = event
                continue

            if published is not None and journal_path is not None:
                generation = _restore_journal_module._journal_generation(published)
                _restore_journal_module._append_restore_journal(
                    journal_path,
                    {
                        "event": "retracted",
                        "key": blob.key,
                        "generation": generation,
                        **(
                            _restore_journal_module._journal_binding(
                                journal_state.started
                            )
                            if journal_state is not None
                            and journal_state.started.get("version")
                            == _contracts_module._RESTORE_JOURNAL_VERSION
                            else {}
                        ),
                    },
                )
                if journal_state is not None:
                    journal_state.intents.pop(blob.key, None)
                    journal_state.published.pop(blob.key, None)
                intent = None

            if intent is None:
                generation = (
                    journal_state.generations.get(blob.key, 0) + 1
                    if journal_state is not None
                    else 1
                )
                intent = {
                    "event": "intent",
                    "key": blob.key,
                    "size": blob.size,
                    "sha256": blob.sha256,
                    "namespace": blob.namespace,
                    "generation": generation,
                }
                if (
                    journal_state is not None
                    and journal_state.started.get("version")
                    == _contracts_module._RESTORE_JOURNAL_VERSION
                ):
                    intent.update(
                        _restore_journal_module._journal_binding(journal_state.started)
                    )
            else:
                generation = _restore_journal_module._journal_generation(intent)
            if journal_path is not None and (
                journal_state is None or blob.key not in journal_state.intents
            ):
                _restore_journal_module._append_restore_journal(journal_path, intent)
                if journal_state is not None:
                    journal_state.intents[blob.key] = intent
                    journal_state.generations[blob.key] = generation
            with blob.path.open("rb") as source:
                receipt = backend.create_stream(source, blob.key)
            if receipt.size != blob.path.stat().st_size:
                raise RuntimeError("restore_blob_size_mismatch")
            if not _stored_blob_matches(blob):
                backend.rollback_create(receipt)
                raise RuntimeError("restore_blob_hash_mismatch")
            item = _contracts_module._AppliedBlob(
                key=blob.key,
                receipt=receipt,
                sha256=blob.sha256,
                generation=generation,
            )
            applied.append(item)
            created.append(item)
            if journal_path is not None:
                event = _restore_journal_module._published_restore_event(item)
                if (
                    journal_state is not None
                    and journal_state.started.get("version")
                    == _contracts_module._RESTORE_JOURNAL_VERSION
                ):
                    event.update(
                        _restore_journal_module._journal_binding(journal_state.started)
                    )
                _restore_journal_module._append_restore_journal(journal_path, event)
                if journal_state is not None:
                    journal_state.published[blob.key] = event
    except Exception:
        _rollback_applied_blobs(created, journal_path=journal_path)
        raise
    return applied, created


def _stored_blob_matches(blob: _contracts_module._StagedBlob) -> bool:
    backend = get_backend()
    try:
        return (
            backend.stat_size(blob.key) == blob.size
            and _archive_format_module._sha256_key(blob.key) == blob.sha256
        )
    except FileNotFoundError:
        return False


def _adopt_restored_blob(
    blob: _contracts_module._StagedBlob, published: dict[str, object] | None
) -> CreationReceipt:
    backend = get_backend()
    if published is not None:
        receipt = _restore_journal_module._receipt_from_event(published)
        if (
            receipt.key != blob.key
            or receipt.size != blob.size
            or receipt.namespace != blob.namespace
            or published.get("sha256") != blob.sha256
        ):
            raise RestoreConflictError("restore_journal_mismatch")
        try:
            if backend.creation_matches(receipt):
                return receipt
        except Exception as exc:
            raise RestoreConflictError("restore_destination_changed") from exc
    try:
        return backend.adopt_existing(
            blob.key,
            expected_size=blob.size,
            expected_sha256=blob.sha256,
        )
    except NotImplementedError:
        # Guarded transports deliberately cannot promise a deletable identity.
        # The content hash is nevertheless sufficient to resume without
        # overwriting or deleting the existing object; ownership keeps that SHA.
        return CreationReceipt(
            key=blob.key,
            size=blob.size,
            token=blob.sha256,
            backend=backend.backend_name,
            namespace=blob.namespace,
        )
    except Exception as exc:
        raise RestoreConflictError("restore_destination_changed") from exc
