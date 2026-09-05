"""Durable directory observations without transport-specific continuation guesses.

Only completed enumerations can be paged. Incomplete directories restart from
scratch, while previously completed directories survive a process restart.
Transport memory and database writes are bounded by the processing batch.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from pathlib import PurePosixPath

from sqlalchemy import delete
from sqlmodel import col, select

from app.core.time import ensure_utc, utcnow
from app.db.models import (
    RemoteDiscoveryDirectory as Directory,
)
from app.db.models import (
    RemoteDiscoveryEntry as Entry,
)
from app.db.models import (
    RemoteDiscoveryInventory as Inventory,
)
from app.db.session import get_session_factory
from app.services.remote_deadline import operation_timeout
from app.services.storage_ownership import provider_ref_for_backend

BATCH_SIZE = 1000


def key_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _insert_batch(session, model, rows, columns):
    if not rows:
        return
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    session.execute(
        insert(model).values(rows).on_conflict_do_nothing(index_elements=columns)
    )


def _cursor(inventory_id: str, after: int) -> str:
    return json.dumps(
        {"v": 1, "inventory": inventory_id, "after": after}, separators=(",", ":")
    )


def _entry_cursor(inventory_id: str, row: Entry) -> str:
    assert row.id is not None
    return _cursor(inventory_id, row.id)


def inventory_page(backend, prefix: str, *, cursor: str | None, limit: int, pace):
    from app.services.library_source import LibrarySourceError, SourceEntry, SourcePage

    if limit < 1 or limit > BATCH_SIZE:
        raise ValueError("limit must be between 1 and 1000")
    prefix = prefix.strip("/")
    if any(part in {".", ".."} for part in prefix.split("/")):
        raise LibrarySourceError("library_source_key_invalid")
    target_ref = key_hash(provider_ref_for_backend(backend))
    with get_session_factory().scoped_session() as session:
        after = 0
        if cursor:
            try:
                decoded = json.loads(cursor)
                if (
                    decoded["v"] != 1
                    or type(decoded["after"]) is not int
                    or decoded["after"] < 0
                ):
                    raise ValueError
                after = decoded["after"]
                inventory = session.get(Inventory, decoded["inventory"])
            except (ValueError, KeyError, TypeError) as exc:
                raise LibrarySourceError("library_source_cursor_invalid") from exc
            if (
                inventory is None
                or inventory.target_ref != target_ref
                or inventory.prefix != prefix
            ):
                raise LibrarySourceError("library_source_cursor_target_changed")
        else:
            # Abandoned probes/epochs have no permanent storage claim. Expired
            # cursors fail closed rather than silently adopting another epoch.
            session.execute(
                delete(Inventory).where(
                    col(Inventory.updated_at) < utcnow() - timedelta(days=30)
                )
            )
            inventory = Inventory(
                id=uuid.uuid4().hex, target_ref=target_ref, prefix=prefix
            )
            session.add(inventory)
            session.flush()
            session.add(
                Directory(
                    inventory_id=inventory.id, path=prefix, path_hash=key_hash(prefix)
                )
            )
            session.commit()
        initial_operations = inventory.metadata_ops
        try:
            while not inventory.complete:
                operation_timeout()
                directory = session.exec(
                    select(Directory)
                    .where(
                        Directory.inventory_id == inventory.id,
                        Directory.complete == False,  # noqa: E712
                    )  # noqa: E712
                    .order_by(col(Directory.id))
                    .limit(1)
                ).first()
                if directory is None:
                    inventory.complete = True
                    session.add(inventory)
                    session.commit()
                    break
                # A crash can leave a partial batch; discard it before trying
                # the directory again. Descendants cannot be enumerated until
                # their parent has finished, so no complete work is lost.
                session.execute(
                    delete(Entry).where(col(Entry.directory_id) == directory.id)
                )
                session.execute(
                    delete(Directory).where(col(Directory.parent_id) == directory.id)
                )
                session.commit()
                files, children = [], []
                inventory.metadata_ops += 1
                with backend.iter_directory(directory.path) as observations:
                    for observation in observations:
                        operation_timeout()
                        key = observation.key.strip("/")
                        if any(
                            part in {"", ".", ".."} for part in key.split("/")
                        ) or str(PurePosixPath(key).parent) != (directory.path or "."):
                            # Never follow an alias, parent, or recursive entry
                            # supplied as a direct child by a malformed server.
                            raise LibrarySourceError(
                                "library_source_directory_entry_invalid"
                            )
                        if observation.is_dir:
                            children.append(
                                dict(
                                    inventory_id=inventory.id,
                                    parent_id=directory.id,
                                    path=key,
                                    path_hash=key_hash(key),
                                    complete=False,
                                    created_at=utcnow(),
                                    updated_at=utcnow(),
                                )
                            )
                        else:
                            if observation.size < 0:
                                raise LibrarySourceError("library_source_size_invalid")
                            files.append(
                                dict(
                                    inventory_id=inventory.id,
                                    directory_id=directory.id,
                                    source_key=key,
                                    key_hash=key_hash(key),
                                    size=observation.size,
                                    modified_at=observation.modified_at,
                                    etag=observation.etag,
                                    version_id=observation.version_id,
                                    created_at=utcnow(),
                                )
                            )
                        if len(files) + len(children) >= BATCH_SIZE:
                            _insert_batch(
                                session, Entry, files, ["inventory_id", "key_hash"]
                            )
                            _insert_batch(
                                session,
                                Directory,
                                children,
                                ["inventory_id", "path_hash"],
                            )
                            session.commit()
                            files.clear()
                            children.clear()
                _insert_batch(session, Entry, files, ["inventory_id", "key_hash"])
                _insert_batch(
                    session, Directory, children, ["inventory_id", "path_hash"]
                )
                directory.complete = True
                directory.updated_at = utcnow()
                inventory.updated_at = utcnow()
                session.add(directory)
                session.add(inventory)
                session.commit()
                pace(inventory.metadata_ops - initial_operations)
        except BaseException as exc:
            session.rollback()
            # The owner can persist this token even when the first enumeration
            # fails. No partial directory can be processed or imply absence.
            if isinstance(exc, Exception):
                error = LibrarySourceError(str(exc))
                error.discovery_cursor = _cursor(inventory.id, after)
                raise error from exc
            setattr(exc, "discovery_cursor", _cursor(inventory.id, after))
            raise
        rows = session.exec(
            select(Entry)
            .where(Entry.inventory_id == inventory.id, col(Entry.id) > after)
            .order_by(col(Entry.id))
            .limit(limit + 1)
        ).all()
        complete = len(rows) <= limit
        entries = rows[:limit]
        observed = tuple(
            SourceEntry(
                row.source_key,
                row.size,
                ensure_utc(row.modified_at) if row.modified_at is not None else None,
                row.etag,
                row.version_id,
            )
            for row in entries
        )
        entry_cursors = tuple(_entry_cursor(inventory.id, row) for row in entries)
        next_cursor = None if complete else entry_cursors[-1]
        operations = inventory.metadata_ops - initial_operations
        inventory.updated_at = utcnow()
        session.add(inventory)
        session.commit()
        return SourcePage(
            observed,
            next_cursor,
            complete,
            metadata_ops=operations,
            entry_cursors=entry_cursors,
            inventory_id=inventory.id,
        )


def retire_inventory(inventory_id: str) -> None:
    """Release a completed snapshot after processing commits its final checkpoint."""
    with get_session_factory().scoped_session() as session:
        session.execute(
            delete(Inventory).where(
                col(Inventory.id) == inventory_id,
                col(Inventory.complete) == True,  # noqa: E712
            )
        )
        session.commit()
