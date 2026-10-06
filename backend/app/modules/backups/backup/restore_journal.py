"""Durable restoration journal and database commit markers."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import URL
from sqlmodel import Session, create_engine, delete, select

import app.modules.backups.backup.contracts as _contracts_module
from app.core.logging import get_logger
from app.db.models import LibraryRevision, RestoreMarker
from app.db.session import get_session_factory
from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import provider_ref_for_backend
from app.runtime.maintenance import RestoreConflictError

logger = get_logger(__name__)


def _journal_has_mutation_evidence(
    state: _contracts_module._RestoreJournalState,
) -> bool:
    """Return whether a restore journal must remain for recovery.

    A validated journal containing only ``started`` (and an optional cache pin)
    cannot have authorized a storage write or database swap. It is therefore
    safe to remove after deterministic preflight rejection.
    """
    return bool(
        state.intents
        or state.published
        or state.database_swap_intent
        or state.database_active
        or state.complete
    )


def _restore_provider_ref(
    blobs: list[_contracts_module._StagedBlob] | None = None,
) -> str:
    """Return the current vault destination identity for restore journaling.

    Remote adapters derive this from their immutable endpoint/configuration.
    The journal is checked before the archive is staged on resume, so it must
    use an adapter-level identity that does not depend on discovering blob
    namespaces from the archive. Local v1 recovery remains the compatibility
    path for historical journals without this field.
    """
    backend = get_backend()
    del blobs
    return provider_ref_for_backend(backend)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_restore_journal(path: Path, event: dict[str, object]) -> None:
    """Durably append one restore transition before proceeding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _remove_restore_journal(path: Path) -> None:
    path.unlink(missing_ok=True)
    _fsync_directory(path.parent)


def _stage_restore_marker(
    database_path: Path,
    backup_id: str,
    *,
    operation_nonce: str,
    archive_sha256: str,
) -> None:
    """Commit the PONR marker into the private database before swapping it."""
    engine = create_engine(URL.create("sqlite", database=str(database_path)))
    try:
        with Session(engine) as session:
            authority = session.get(LibraryRevision, 1)
            if authority is None:
                raise RuntimeError("restore_library_revision_missing")
            # A snapshot copies its counters and epoch. Renew the incarnation
            # in the same private transaction as the publication marker so a
            # physical restore cannot reuse an earlier browse/access stamp.
            # Forward recovery of an already-active marker skips this stage.
            authority.epoch = uuid4().hex
            session.add(authority)
            # Markers are operation evidence, not a historical restore log.
            # Remove stale rows copied from an older backup before inserting
            # this operation's marker, avoiding a false active result when the
            # same backup is restored again.
            session.exec(delete(RestoreMarker))
            session.add(
                RestoreMarker(
                    backup_id=backup_id,
                    operation_nonce=operation_nonce,
                    archive_sha256=archive_sha256,
                    state="database_active",
                )
            )
            session.commit()
    finally:
        engine.dispose()
    fd = os.open(database_path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _active_restore_marker(
    backup_id: str,
    *,
    operation_nonce: str | None = None,
    archive_sha256: str | None = None,
) -> bool | None:
    """Read the active DB's marker; ``None`` means the state is unknowable."""
    # Backup id is a routing label, not an ownership proof. Never query on it
    # alone: an older database can contain a marker for a different object
    # with the same id.
    if operation_nonce is None or archive_sha256 is None:
        return None
    try:
        with get_session_factory().session() as session:
            statement = select(RestoreMarker).where(
                RestoreMarker.backup_id == backup_id
            )
            statement = statement.where(
                RestoreMarker.operation_nonce == operation_nonce,
                RestoreMarker.archive_sha256 == archive_sha256,
            )
            marker = session.exec(statement).first()
            return marker is not None and marker.state == "database_active"
    except Exception:
        logger.exception(
            "unable to prove restored database marker", extra={"backup_id": backup_id}
        )
        return None


def _load_restore_journal(path: Path) -> _contracts_module._RestoreJournalState:
    try:
        raw_lines = path.read_bytes().splitlines()
        events = [json.loads(line.decode("utf-8")) for line in raw_lines]
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise RestoreConflictError("restore_journal_invalid") from exc
    if not events or any(not isinstance(event, dict) for event in events):
        raise RestoreConflictError("restore_journal_invalid")
    raw_started = events[0]
    if raw_started.get("event") != "started":
        raise RestoreConflictError("restore_journal_invalid")
    if raw_started.get("version") not in {
        1,
        _contracts_module._RESTORE_JOURNAL_VERSION,
    }:
        raise RestoreConflictError("restore_journal_invalid")
    started = dict(raw_started)
    upgrade_events = [
        event for event in events[1:] if event.get("event") == "journal_upgrade"
    ]
    if raw_started.get("version") == _contracts_module._RESTORE_JOURNAL_VERSION:
        if upgrade_events:
            raise RestoreConflictError("restore_journal_invalid")
        nonce = started.get("operation_nonce")
        archive_sha = started.get("archive_sha256")
        if (
            not isinstance(nonce, str)
            or len(nonce) != 64
            or any(char not in "0123456789abcdef" for char in nonce)
            or not isinstance(archive_sha, str)
            or len(archive_sha) != 64
            or any(char not in "0123456789abcdef" for char in archive_sha)
            or (
                started.get("provider_ref") is not None
                and (
                    not isinstance(started.get("provider_ref"), str)
                    or len(str(started.get("provider_ref"))) != 64
                    or any(
                        char not in "0123456789abcdef"
                        for char in str(started.get("provider_ref"))
                    )
                )
            )
        ):
            raise RestoreConflictError("restore_journal_invalid")
    upgrade_index: int | None = None
    if raw_started.get("version") == 1 and upgrade_events:
        # v1 journals predate the database marker. Once an upgrade is present,
        # every subsequent proof is bound to the newly generated nonce *and*
        # the original archive hash. The effective state is v2 even though the
        # immutable first line remains v1 for forensic compatibility.
        if len(upgrade_events) != 1:
            raise RestoreConflictError("restore_journal_invalid")
        upgrade = upgrade_events[0]
        upgrade_index = next(
            (index for index, event in enumerate(events) if event is upgrade), None
        )
        if upgrade_index is None:
            raise RestoreConflictError("restore_journal_invalid")
        nonce = upgrade.get("operation_nonce")
        archive_sha = upgrade.get("archive_sha256")
        provider_ref = upgrade.get("provider_ref")
        if (
            upgrade.get("backup_id") != raw_started.get("backup_id")
            or upgrade.get("from_version") != 1
            or upgrade.get("to_version") != _contracts_module._RESTORE_JOURNAL_VERSION
            or not isinstance(nonce, str)
            or len(nonce) != 64
            or any(char not in "0123456789abcdef" for char in nonce)
            or not isinstance(archive_sha, str)
            or len(archive_sha) != 64
            or any(char not in "0123456789abcdef" for char in archive_sha)
            or raw_started.get("archive_sha256") != archive_sha
            or (
                provider_ref is not None
                and (
                    not isinstance(provider_ref, str)
                    or len(provider_ref) != 64
                    or any(char not in "0123456789abcdef" for char in provider_ref)
                )
            )
        ):
            raise RestoreConflictError("restore_journal_invalid")
        started["version"] = _contracts_module._RESTORE_JOURNAL_VERSION
        started["operation_nonce"] = nonce
        started["archive_sha256"] = archive_sha
        if provider_ref is not None:
            started["provider_ref"] = provider_ref
    intents: dict[str, dict[str, object]] = {}
    published: dict[str, dict[str, object]] = {}
    generations: dict[str, int] = {}
    database_swap_intent = False
    database_active = False
    complete = False
    cache_paths: set[str] = set()
    lifecycle_closed = False
    for event_index, event in enumerate(events[1:], start=1):
        event_name = event.get("event")
        if complete:
            raise RestoreConflictError("restore_journal_invalid")
        if raw_started.get(
            "version"
        ) == _contracts_module._RESTORE_JOURNAL_VERSION and event_name in {
            "database_swap_intent",
            "database_active",
            "complete",
        }:
            if any(
                event.get(field) != started.get(field)
                for field in (
                    "backup_id",
                    "operation_nonce",
                    "archive_sha256",
                    "provider_ref",
                )
            ):
                raise RestoreConflictError("restore_journal_invalid")
        # A v1 journal may have durable blob intent/publication transitions
        # before the process is upgraded.  Database swap evidence did not
        # exist in v1, however, so accepting it before the upgrade would let
        # a stale backup-id-only marker masquerade as this operation.  Once
        # the upgrade record is present, all database evidence must carry the
        # projected nonce and immutable archive hash checked above.
        if (
            raw_started.get("version") == 1
            and event_name in {"database_swap_intent", "database_active"}
            and (upgrade_index is None or event_index < upgrade_index)
        ):
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "database_swap_intent":
            if (
                database_swap_intent
                or database_active
                or lifecycle_closed
                or any(key not in published for key in intents)
                or event.get("backup_id") != started.get("backup_id")
                or event.get("operation_nonce") != started.get("operation_nonce")
                or event.get("archive_sha256") != started.get("archive_sha256")
                or (
                    started.get("provider_ref") is not None
                    and event.get("provider_ref") != started.get("provider_ref")
                )
            ):
                raise RestoreConflictError("restore_journal_invalid")
            database_swap_intent = True
            lifecycle_closed = True
            continue
        if event_name == "journal_upgrade":
            # Validated above, and a second upgrade was rejected there.
            continue
        if event_name == "cache_pinned":
            cache_path = event.get("cache_path")
            if not isinstance(cache_path, str) or not cache_path:
                raise RestoreConflictError("restore_journal_invalid")
            if raw_started.get(
                "version"
            ) == _contracts_module._RESTORE_JOURNAL_VERSION and any(
                event.get(field) != started.get(field)
                for field in (
                    "backup_id",
                    "operation_nonce",
                    "archive_sha256",
                    "provider_ref",
                )
            ):
                raise RestoreConflictError("restore_journal_invalid")
            if cache_path in cache_paths:
                raise RestoreConflictError("restore_journal_invalid")
            cache_paths.add(cache_path)
            continue
        if event_name == "database_active":
            if (
                not database_swap_intent
                or database_active
                or event.get("backup_id") != started.get("backup_id")
                or event.get("operation_nonce") != started.get("operation_nonce")
                or event.get("archive_sha256") != started.get("archive_sha256")
                or event.get("provider_ref") != started.get("provider_ref")
            ):
                raise RestoreConflictError("restore_journal_invalid")
            database_active = True
            continue
        if event_name not in {"intent", "published", "retracted", "complete"}:
            raise RestoreConflictError("restore_journal_invalid")
        # A v2 journal (or a v1 journal after its explicit upgrade) binds every
        # subsequent blob lifecycle transition to the same current provider.
        # Historical v1 transitions before the upgrade remain readable so local
        # recovery can proceed forward without replaying them.
        if (
            started.get("provider_ref") is not None
            and (
                raw_started.get("version") == _contracts_module._RESTORE_JOURNAL_VERSION
                or upgrade_index is None
                or event_index > upgrade_index
            )
            and event.get("provider_ref") != started.get("provider_ref")
        ):
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "complete":
            if raw_started.get("version") != 1 and not database_active:
                raise RestoreConflictError("restore_journal_invalid")
            complete = True
            continue
        if lifecycle_closed:
            raise RestoreConflictError("restore_journal_invalid")
        key = event.get("key")
        generation = event.get("generation")
        if not isinstance(key, str) or not isinstance(generation, int):
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "intent":
            if key in intents or generation != generations.get(key, 0) + 1:
                raise RestoreConflictError("restore_journal_invalid")
            intents[key] = event
            generations[key] = generation
            continue
        active_intent = intents.get(key)
        if active_intent is None or active_intent.get("generation") != generation:
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "published":
            if key in published:
                raise RestoreConflictError("restore_journal_invalid")
            published[key] = event
            continue
        intents.pop(key)
        published.pop(key)
    return _contracts_module._RestoreJournalState(
        started,
        intents,
        published,
        generations,
        database_swap_intent,
        database_active,
        complete,
        cache_paths,
    )


def _prepare_restore_journal(
    path: Path,
    *,
    backup_id: str,
    archive_sha256: str,
    blobs: list[_contracts_module._StagedBlob],
    operation_nonce: str | None = None,
) -> _contracts_module._RestoreJournalState:
    operation_nonce = operation_nonce or secrets.token_hex(32)
    for other in path.parent.glob(".restore-*.journal"):
        if other != path:
            raise RestoreConflictError("restore_incomplete_other_backup")
    backend = get_backend()
    current_provider_ref = _restore_provider_ref(blobs)
    expected_start: dict[str, object] = {
        "event": "started",
        "version": _contracts_module._RESTORE_JOURNAL_VERSION,
        "backup_id": backup_id,
        "archive_sha256": archive_sha256,
        "operation_nonce": operation_nonce,
        "backend": backend.backend_name,
        "namespaces": sorted({blob.namespace for blob in blobs}),
        "provider_ref": current_provider_ref,
    }
    if not path.exists():
        _append_restore_journal(path, expected_start)
        return _contracts_module._RestoreJournalState(expected_start, {}, {}, {})
    state = _load_restore_journal(path)
    if state.started.get("version") == 1 and state.complete:
        # A legacy terminal journal has no nonce-bound marker proof. It is
        # forensic evidence only and must never be projected into a resumable
        # v2 operation.
        raise RestoreConflictError("restore_journal_terminal")
    if state.started.get("version") == _contracts_module._RESTORE_JOURNAL_VERSION:
        existing_nonce = state.started.get("operation_nonce")
        if isinstance(existing_nonce, str):
            expected_start["operation_nonce"] = existing_nonce
    if state.started != expected_start:
        # v1 had no DB swap intent/marker. Upgrade only a journal whose
        # identity and archive hash are an exact match, and keep its existing
        # events for forward-only resume.
        legacy_identity = {
            key: state.started.get(key)
            for key in expected_start
            if key not in {"version", "operation_nonce", "provider_ref"}
        }
        expected_identity = {
            key: expected_start[key]
            for key in expected_start
            if key not in {"version", "operation_nonce", "provider_ref"}
        }
        # v1 journals have no provider binding. They can be resumed safely only
        # against the local backend, whose storage identity is the filesystem
        # root and whose legacy journal already pins its namespace list. A
        # remote v1 journal must be explicitly re-created/adopted rather than
        # guessing which endpoint owns its keys.
        if (
            state.started.get("version") != 1
            or legacy_identity != expected_identity
            or backend.backend_name != "local"
        ):
            raise RestoreConflictError("restore_journal_mismatch")
        _append_restore_journal(
            path,
            {
                "event": "journal_upgrade",
                "backup_id": backup_id,
                "from_version": 1,
                "to_version": _contracts_module._RESTORE_JOURNAL_VERSION,
                "operation_nonce": operation_nonce,
                "archive_sha256": archive_sha256,
                "provider_ref": current_provider_ref,
            },
        )
        # Re-read so callers use the effective v2 identity immediately. A
        # stale v1 state must never perform a backup-id-only marker lookup.
        state = _load_restore_journal(path)
    expected_keys = {blob.key for blob in blobs}
    if not set(state.intents).issubset(expected_keys) or not set(
        state.published
    ).issubset(expected_keys):
        raise RestoreConflictError("restore_journal_invalid")
    by_key = {blob.key: blob for blob in blobs}
    if any(
        not _journal_intent_matches(event, by_key[key])
        for key, event in state.intents.items()
    ):
        raise RestoreConflictError("restore_journal_mismatch")
    return state


def _journal_intent_matches(
    event: dict[str, object], blob: _contracts_module._StagedBlob
) -> bool:
    generation = event.get("generation")
    required = {
        "event": "intent",
        "key": blob.key,
        "size": blob.size,
        "sha256": blob.sha256,
        "namespace": blob.namespace,
        "generation": generation,
    }
    return isinstance(generation, int) and all(
        event.get(key) == value for key, value in required.items()
    )


def _journal_generation(event: dict[str, object]) -> int:
    generation = event.get("generation")
    if not isinstance(generation, int):
        raise RestoreConflictError("restore_journal_invalid")
    return generation


def _receipt_from_event(event: dict[str, object]) -> CreationReceipt:
    key = event.get("key")
    size = event.get("size")
    token = event.get("token")
    backend = event.get("backend")
    namespace = event.get("namespace")
    etag = event.get("etag")
    version_id = event.get("version_id")
    device = event.get("device")
    inode = event.get("inode")
    ctime_ns = event.get("ctime_ns")
    if (
        not isinstance(key, str)
        or not isinstance(size, int)
        or not isinstance(token, str)
        or not isinstance(backend, str)
        or not isinstance(namespace, str)
        or (etag is not None and not isinstance(etag, str))
        or (version_id is not None and not isinstance(version_id, str))
        or (device is not None and not isinstance(device, int))
        or (inode is not None and not isinstance(inode, int))
        or (ctime_ns is not None and not isinstance(ctime_ns, int))
    ):
        raise RestoreConflictError("restore_journal_invalid")
    return CreationReceipt(
        key=key,
        size=size,
        token=token,
        backend=backend,
        namespace=namespace,
        etag=etag,
        version_id=version_id,
        device=device,
        inode=inode,
        ctime_ns=ctime_ns,
    )


def _journal_binding(started: dict[str, object]) -> dict[str, object]:
    binding = {
        "backup_id": started["backup_id"],
        "operation_nonce": started["operation_nonce"],
        "archive_sha256": started["archive_sha256"],
    }
    provider_ref = started.get("provider_ref")
    if provider_ref is not None:
        binding["provider_ref"] = provider_ref
    return binding


def _published_restore_event(item: _contracts_module._AppliedBlob) -> dict[str, object]:
    receipt = item.receipt
    return {
        "event": "published",
        "key": item.key,
        "generation": item.generation,
        "size": receipt.size,
        "sha256": item.sha256,
        "token": receipt.token,
        "backend": receipt.backend,
        "namespace": receipt.namespace,
        "etag": receipt.etag,
        "version_id": receipt.version_id,
        "device": receipt.device,
        "inode": receipt.inode,
        "ctime_ns": receipt.ctime_ns,
    }
