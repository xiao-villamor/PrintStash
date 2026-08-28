"""Builders for rows whose bytes actually exist, with a provable owner.

The rest of `tests/factories/` builds database rows and stops there. That is
right for almost everything, but not for the delete paths: PrintStash never
deletes bytes it cannot *prove* it created, and the proof is an ownership receipt
recording the object's device, inode, ctime and size at the moment it was
written. A `File` row pointing at a key nobody wrote is therefore not a
half-finished fixture — it is a *different scenario*, the "legacy or foreign
bytes" case that the GC deliberately refuses to touch.

Both cases are real and both are tested, so they get separate builders:

* `build_stored_file` — row, bytes, and receipt. Deleting this is allowed.
* `build_unowned_file` — row and bytes, **no receipt**. This is a user's own
  library mounted where the vault expects its data, or an artifact from before
  receipts existed. Any purge of it must be refused, and `resources_blocked`
  must count it.

Twelve test files construct one or the other. Getting it wrong is a silent false
pass in the worst place in the codebase: a test that thinks it proved the GC
deletes an owned object, when it actually proved the GC skipped an unprovable one.

Storage keys come from `backend.blob_key(...)` and never from a hand-written
relative path — a relative key resolves against the per-test working directory
and lands in an `external:` namespace where ownership cannot be proven at all.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db.models import File, FileType, Model
from app.services.storage_backend import CreationReceipt, StorageBackend
from app.services.storage_ownership import record_creation
from tests.factories._support import nth, reject_aliases, save, unique_hash
from tests.factories.library import _demote_current_recommendation


def store_owned_bytes(
    session: Session,
    backend: StorageBackend,
    key: str,
    data: bytes = b"x",
    *,
    object_kind: str = "test",
) -> CreationReceipt:
    """Write *data* at *key* and record the receipt that proves we own it."""
    receipt = backend.create_bytes(data, key)
    record_creation(session, receipt, object_kind=object_kind)
    session.commit()
    return receipt


def build_stored_file(
    session: Session,
    backend: StorageBackend,
    model: Model,
    *,
    filename: str | None = None,
    file_type: FileType = FileType.STL,
    data: bytes = b"x",
    recommended: bool = False,
    **overrides: Any,
) -> File:
    """An artifact whose bytes are on the backend with an ownership receipt.

    Use this for any test that exercises a delete, a purge, or the GC: without
    the receipt the operation is *correctly refused*, and the test then asserts
    against a refusal it did not intend.
    """
    reject_aliases(
        overrides,
        {
            "is_recommended": "recommended",
            "original_filename": "filename",
            "path": "filename",
            "size_bytes": "data",
        },
    )
    index = nth("stored_file")
    version = overrides.pop("version", None)
    if version is None:
        version = model.next_file_version
        model.next_file_version += 1
        session.add(model)
    name = filename or f"stored-{index}.{file_type.value.lower()}"
    key = backend.blob_key(model.slug, version, name)
    store_owned_bytes(session, backend, key, data)
    if recommended:
        # Same invariant `build_file` maintains: at most one recommended live
        # G-code per model. Two builders disagreeing on this would be its own
        # trap, since a test cannot see which one it happened to use.
        _demote_current_recommendation(session, model)
    overrides.setdefault("sha256", unique_hash("stored_sha"))
    return save(
        session,
        File(
            model_id=model.id,
            path=key,
            original_filename=name,
            file_type=file_type,
            version=version,
            size_bytes=len(data),
            is_recommended=recommended,
            **overrides,
        ),
    )


def build_unowned_file(
    session: Session,
    backend: StorageBackend,
    model: Model,
    *,
    filename: str | None = None,
    file_type: FileType = FileType.STL,
    data: bytes = b"legacy-user-bytes",
    **overrides: Any,
) -> File:
    """An artifact whose bytes exist but have **no** ownership receipt.

    The "somebody else's library" case. A configured `data_dir` may be a
    mistakenly mounted user folder, so an unclaimed path is never enough proof
    that PrintStash may delete it — every purge of this must be refused and
    counted as blocked, and the bytes must survive.
    """
    from pathlib import Path

    reject_aliases(
        overrides,
        {"original_filename": "filename", "path": "filename", "size_bytes": "data"},
    )
    index = nth("unowned_file")
    version = overrides.pop("version", None)
    if version is None:
        version = model.next_file_version
        model.next_file_version += 1
        session.add(model)
    name = filename or f"legacy-{index}.{file_type.value.lower()}"
    key = backend.blob_key(model.slug, version, name)
    written = Path(key)
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_bytes(data)
    overrides.setdefault("sha256", unique_hash("unowned_sha"))
    return save(
        session,
        File(
            model_id=model.id,
            path=key,
            original_filename=name,
            file_type=file_type,
            version=version,
            size_bytes=len(data),
            **overrides,
        ),
    )
