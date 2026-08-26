"""Trash + GC safety on a remote (S3/R2-style) storage backend.

The local-backend trash tests never exercise the ``direct_path() is None`` branch
that S3/R2 deployments take. These inject a remote-style backend (no direct
filesystem path) to pin two blob-ownership invariants on that path:

* hard delete removes the vault blob key and the thumbnail keys, but must never
  delete the bytes of a NAS-linked (external) file;
* maintenance never discovers and deletes objects merely because no database
  row references them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import File, FileType, Model
from app.services import trash
from app.services.storage_backend import LocalStorageBackend
from app.services.storage_ownership import UnsafeStorageDeleteError


class _RecordingRemoteBackend(LocalStorageBackend):
    """A remote-style backend: no direct path, every ``delete`` is recorded.

    Inherits key derivation (``blob_key``/``thumbnail_key``/…) from the local
    backend so the keys are realistic; only the filesystem semantics change."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def direct_path(self, key: str) -> Path | None:
        return None

    def delete(self, key: str) -> None:
        self.deleted.append(key)


class _WalkRecordingBackend(_RecordingRemoteBackend):
    """Remote backend that yields a fixed key listing and records the prefix it
    was asked to walk — for asserting destructive GC never enumerates storage."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__()
        self._keys = list(keys)
        self.walked: list[str] = []

    def walk_keys(self, prefix: str = ""):
        self.walked.append(prefix)
        yield from self._keys


def _add_model(session: Session, slug: str) -> Model:
    m = Model(name=slug, slug=slug, hash=slug.ljust(64, "0"))
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


def _add_file(session: Session, model: Model, path: str, **kw) -> File:
    f = File(
        model_id=model.id,
        path=path,
        original_filename=Path(path).name,
        file_type=FileType.GCODE,
        version=kw.pop("version", 1),
        size_bytes=1,
        sha256=kw.pop("sha256", path.ljust(64, "0")[:64]),
        **kw,
    )
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


def test_hard_delete_on_remote_backend_respects_blob_ownership(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    backend = _RecordingRemoteBackend()
    monkeypatch.setattr("app.services.storage_backend._backend", backend)

    model = _add_model(db_session, "mixed")
    vault_key = "vault-data/files/mixed/v1/part.gcode"
    nas_path = "/mnt/nas/3d/part.gcode"
    vault_file = _add_file(db_session, model, vault_key, sha256="a" * 64)
    ext_file = _add_file(
        db_session, model, nas_path, version=2, sha256="b" * 64, is_external=True
    )

    with pytest.raises(UnsafeStorageDeleteError):
        trash.hard_delete_model(db_session, model)
    db_session.rollback()

    # These hand-built legacy rows have no positive creation receipts, so even
    # the vault-shaped key is preserved. The NAS-linked path is never eligible.
    assert vault_key not in backend.deleted
    assert nas_path not in backend.deleted
    assert backend.thumbnail_key(vault_file.id) not in backend.deleted
    assert backend.thumbnail_key(ext_file.id) not in backend.deleted
    # Fail-closed also preserves the rows so an operator can recover/adopt them.
    db_session.expire_all()
    assert db_session.get(Model, model.id) is not None
    assert db_session.get(File, vault_file.id) is not None
    assert db_session.get(File, ext_file.id) is not None


def test_gc_on_remote_backend_never_discovers_or_deletes_unclaimed_objects(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    _overlay["storage_backend"] = "s3"
    keep_key = "vault-data/files/keep/v1/a.gcode"
    orphan_key = "vault-data/files/orphan/v1/b.gcode"

    model = _add_model(db_session, "keep")
    _add_file(db_session, model, keep_key, sha256="a" * 64)

    backend = _WalkRecordingBackend([keep_key, orphan_key])
    monkeypatch.setattr("app.services.storage_backend._backend", backend)

    removed = trash._cleanup_orphan_blobs(db_session)

    assert backend.walked == []
    assert backend.deleted == []
    assert removed == 0
