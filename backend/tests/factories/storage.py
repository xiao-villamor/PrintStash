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

import hashlib
from typing import Any

from sqlmodel import Session

from app.db.models import (
    File,
    FileType,
    Model,
    OwnedStorageObject,
    StorageDeleteIntent,
    StorageObjectState,
)
from app.services.storage_backend import CreationReceipt, StorageBackend
from app.services.storage_ownership import provider_ref_for_backend, record_creation
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
    record_creation(
        session,
        receipt,
        object_kind=object_kind,
        sha256=hashlib.sha256(data).hexdigest(),
        provider_ref=provider_ref_for_backend(backend, namespace=receipt.namespace),
    )
    session.commit()
    return receipt


def build_owned_storage_object(
    session: Session,
    *,
    backend: str = "local",
    namespace: str = "local/test",
    key: str = "files/test.stl",
    object_kind: str = "artifact",
    state: StorageObjectState = StorageObjectState.COMMITTED,
    token: str | None = "test-token",
    size_bytes: int | None = 1,
    sha256: str | None = None,
    provider_ref: str | None = None,
    etag: str | None = None,
    version_id: str | None = None,
    **overrides: Any,
) -> OwnedStorageObject:
    """One explicit ownership receipt for ledger/recovery tests.

    The state and identity fields are named here because constructing a receipt
    inline is otherwise easy to make internally inconsistent (for example a
    committed remote row without either an ETag or version id).
    """
    return save(
        session,
        OwnedStorageObject(
            backend=backend,
            namespace=namespace,
            key=key,
            object_kind=object_kind,
            state=state,
            token=token,
            size_bytes=size_bytes,
            sha256=sha256,
            provider_ref=provider_ref,
            etag=etag,
            version_id=version_id,
            **overrides,
        ),
    )


def build_storage_delete_intent(
    session: Session,
    backend: StorageBackend,
    receipt: CreationReceipt,
    *,
    authorization_mode: str = "verified",
    sha256: str | None = None,
    quarantine_state: str = "none",
    **overrides: Any,
) -> StorageDeleteIntent:
    """Persist one outbox row for restart/reconciliation scenarios.

    The builder keeps authorization metadata explicit so tests cannot create a
    row that accidentally relies on a process-local confirmation flag.
    """
    overrides.setdefault("authorization_mode", authorization_mode)
    overrides.setdefault("quarantine_state", quarantine_state)
    overrides.setdefault("sha256", sha256)
    overrides.setdefault(
        "provider_ref",
        receipt.provider_ref
        or provider_ref_for_backend(backend, namespace=receipt.namespace),
    )
    row = StorageDeleteIntent(
        backend=receipt.backend,
        namespace=receipt.namespace,
        key=receipt.key,
        object_kind="test",
        token=receipt.token,
        size_bytes=receipt.size,
        sha256=overrides.pop("sha256"),
        etag=receipt.etag,
        version_id=receipt.version_id,
        device=receipt.device,
        inode=receipt.inode,
        ctime_ns=receipt.ctime_ns,
        provider_ref=overrides.pop("provider_ref"),
        **overrides,
    )
    return save(session, row)


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
    overrides.setdefault("sha256", hashlib.sha256(data).hexdigest())
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
