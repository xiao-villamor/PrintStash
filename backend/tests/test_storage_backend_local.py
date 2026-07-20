"""Direct coverage for LocalStorageBackend's filesystem operations —
move/read/stream/upload/delete/list/walk/usage/health_probe — beyond the
direct_path/local_path/move_in seam already covered in test_storage_seam.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.services import storage_backend
from app.services.storage_backend import LocalStorageBackend


@dataclass
class _FakeSettings:
    data_dir: Path
    thumb_dir: Path


def test_move_relocates_file_and_creates_parents(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    src = tmp_path / "staged.stl"
    src.write_bytes(b"solid")
    dest = tmp_path / "nested" / "dir" / "final.stl"

    backend.move(str(src), str(dest))

    assert not src.exists()
    assert dest.read_bytes() == b"solid"


def test_stat_size_and_read_bytes(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    blob = tmp_path / "part.stl"
    blob.write_bytes(b"0123456789")

    assert backend.stat_size(str(blob)) == 10
    assert backend.read_bytes(str(blob)) == b"0123456789"


def test_stream_chunks_yields_full_content(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    blob = tmp_path / "part.stl"
    blob.write_bytes(b"a" * 5000)

    chunks = list(backend.stream_chunks(str(blob), chunk_size=2000))

    assert len(chunks) == 3
    assert b"".join(chunks) == b"a" * 5000


def test_download_to_path_copies_and_creates_parents(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    src = tmp_path / "source.stl"
    src.write_bytes(b"solid")
    dest = tmp_path / "nested" / "copy.stl"

    result = backend.download_to_path(str(src), dest)

    assert result == dest
    assert dest.read_bytes() == b"solid"
    assert src.exists()  # copy, not move


def test_upload_file_copies_into_key_path(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    src = tmp_path / "source.stl"
    src.write_bytes(b"solid")
    dest_key = tmp_path / "nested" / "uploaded.stl"

    backend.upload_file(src, str(dest_key))

    assert dest_key.read_bytes() == b"solid"
    assert src.exists()


def test_delete_removes_file_and_swallows_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = LocalStorageBackend()
    blob = tmp_path / "part.stl"
    blob.write_bytes(b"solid")

    backend.delete(str(blob))
    assert not blob.exists()

    def boom(*_a, **_kw):
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", boom)
    backend.delete(str(blob))  # must not raise


def test_list_keys_and_walk_keys_find_files_recursively(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.stl").write_bytes(b"1")
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "two.stl").write_bytes(b"22")

    listed = backend.list_keys(str(tmp_path / "a"))
    walked = list(backend.walk_keys(str(tmp_path / "a")))

    assert len(listed) == 2
    assert len(walked) == 2
    assert {Path(p).name for p in listed} == {"one.stl", "two.stl"}


def test_list_keys_and_walk_keys_return_empty_for_missing_root(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    missing = tmp_path / "does-not-exist"

    assert backend.list_keys(str(missing)) == []
    assert list(backend.walk_keys(str(missing))) == []


def test_usage_totals_size_and_count(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    root = tmp_path / "vault"
    root.mkdir()
    (root / "one.stl").write_bytes(b"12345")
    (root / "two.stl").write_bytes(b"12")

    result = backend.usage(str(root))

    assert result["object_count"] == 2
    assert result["total_size_bytes"] == 7
    assert result["backend"] == "local"


def test_usage_missing_root_returns_zero(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    result = backend.usage(str(tmp_path / "nowhere"))
    assert result == {
        "backend": "local",
        "prefix": str(tmp_path / "nowhere"),
        "object_count": 0,
        "total_size_bytes": 0,
    }


def test_presigned_download_url_is_unsupported_locally() -> None:
    backend = LocalStorageBackend()
    assert backend.presigned_download_url("any-key", "file.stl") is None


def test_health_probe_reports_ok_when_both_dirs_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))

    result = LocalStorageBackend().health_probe()

    assert result == {
        "backend": "local",
        "ok": True,
        "data_dir": str(data_dir),
        "thumb_dir": str(thumb_dir),
    }


def test_health_probe_reports_not_ok_when_a_dir_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    data_dir.mkdir()
    thumb_dir = tmp_path / "thumbs-missing"
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))

    result = LocalStorageBackend().health_probe()

    assert result["ok"] is False


def test_ensure_setup_creates_data_and_thumb_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))

    LocalStorageBackend().ensure_setup()

    assert data_dir.is_dir()
    assert thumb_dir.is_dir()
