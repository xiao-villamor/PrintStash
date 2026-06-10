"""Tests for the deepened StorageBackend interface (direct_path / local_path /
move_in) and the live/trashed query scopes."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from app.db.models import Model
from app.db.scopes import live, trashed
from app.services.storage_backend import LocalStorageBackend, StorageBackend
from app.services.trash import restore_model, soft_delete_model


class _FakeRemoteBackend(LocalStorageBackend):
    """Backend with no direct filesystem representation — exercises the
    temp-download path of ``local_path()`` and the upload path of
    ``move_in()`` without S3."""

    def __init__(self, store_dir: Path) -> None:
        self._store = store_dir

    def direct_path(self, key: str) -> Path | None:
        return None

    def _resolve(self, key: str) -> Path:
        return self._store / key

    def download_to_path(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._resolve(key).read_bytes())
        return dest

    def upload_file(self, src: Path, key: str) -> None:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())


def test_local_backend_local_path_yields_real_path(tmp_path: Path) -> None:
    backend: StorageBackend = LocalStorageBackend()
    blob = tmp_path / "part.stl"
    blob.write_bytes(b"solid")

    with backend.local_path(str(blob)) as path:
        assert path == blob
        assert path.read_bytes() == b"solid"
    # Real path must survive the context exit.
    assert blob.exists()


def test_remote_backend_local_path_downloads_and_cleans_up(tmp_path: Path) -> None:
    backend = _FakeRemoteBackend(tmp_path / "store")
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "key.gcode").write_bytes(b"G1 X0")

    seen: Path | None = None
    with backend.local_path("key.gcode") as path:
        seen = path
        assert path.read_bytes() == b"G1 X0"
        assert path != tmp_path / "store" / "key.gcode"  # temp copy
    assert seen is not None and not seen.exists()  # cleaned up on exit


def test_local_backend_move_in_renames(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"data")
    dest = tmp_path / "vault" / "v1" / "staged.bin"

    backend.move_in(staged, str(dest))

    assert not staged.exists()
    assert dest.read_bytes() == b"data"


def test_remote_backend_move_in_uploads_and_removes_staged(tmp_path: Path) -> None:
    backend = _FakeRemoteBackend(tmp_path / "store")
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"data")

    backend.move_in(staged, "blobs/staged.bin")

    assert not staged.exists()
    assert (tmp_path / "store" / "blobs" / "staged.bin").read_bytes() == b"data"


def test_live_and_trashed_scopes(db_session: Session) -> None:
    m = Model(name="ScopeTest", slug="scope-test", hash="f" * 64)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    assert m in db_session.exec(select(Model).where(live(Model))).all()
    assert m not in db_session.exec(select(Model).where(trashed(Model))).all()

    soft_delete_model(db_session, m)
    assert m not in db_session.exec(select(Model).where(live(Model))).all()
    assert m in db_session.exec(select(Model).where(trashed(Model))).all()

    restore_model(db_session, m)
    assert m in db_session.exec(select(Model).where(live(Model))).all()
