"""Direct coverage for LocalStorageBackend's filesystem operations —
move/read/stream/upload/delete/list/walk/usage/health_probe — beyond the
direct_path/local_path/move_in seam already covered in test_storage_seam.py."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Barrier, Thread
from unittest.mock import MagicMock

import pytest

from app.services import storage_backend
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    StorageBackend,
    StorageCollisionError,
)


@dataclass
class _FakeSettings:
    data_dir: Path
    thumb_dir: Path
    storage_identity: str = "test-installation"


def test_unchecked_move_is_disabled_and_preserves_source(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    src = tmp_path / "staged.stl"
    src.write_bytes(b"solid")
    dest = tmp_path / "nested" / "dir" / "final.stl"

    with pytest.raises(RuntimeError, match="unchecked_storage_move_disabled"):
        backend.move(str(src), str(dest))

    assert src.read_bytes() == b"solid"
    assert not dest.exists()


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


def test_download_to_path_collision_preserves_existing_destination(
    tmp_path: Path,
) -> None:
    backend = LocalStorageBackend()
    src = tmp_path / "source.stl"
    src.write_bytes(b"new")
    dest = tmp_path / "copy.stl"
    dest.write_bytes(b"user-owned")

    with pytest.raises(StorageCollisionError):
        backend.download_to_path(str(src), dest)

    assert dest.read_bytes() == b"user-owned"


def test_download_failure_never_publishes_partial_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = LocalStorageBackend()
    src = tmp_path / "source.stl"
    src.write_bytes(b"source")
    dest = tmp_path / "copy.stl"
    real_open = Path.open

    class FailingSource(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def read(self, _size: int = -1) -> bytes:
            if self.tell() == 0:
                return super().read(3)
            raise OSError("source vanished mid-download")

    def fail_source_only(self: Path, mode: str = "r", *args, **kwargs):
        if self == src and mode == "rb":
            return FailingSource(b"partial")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_source_only)

    with pytest.raises(OSError, match="source vanished"):
        backend.download_to_path(str(src), dest)

    assert not dest.exists()


def test_upload_file_copies_into_key_path(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    src = tmp_path / "source.stl"
    src.write_bytes(b"solid")
    dest_key = tmp_path / "nested" / "uploaded.stl"

    backend.upload_file(src, str(dest_key))

    assert dest_key.read_bytes() == b"solid"
    assert src.exists()


def test_unchecked_delete_is_disabled_and_preserves_file(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    blob = tmp_path / "part.stl"
    blob.write_bytes(b"solid")

    with pytest.raises(RuntimeError, match="unchecked_storage_delete_disabled"):
        backend.delete(str(blob))
    assert blob.read_bytes() == b"solid"


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

    assert result["backend"] == "local"
    assert result["ok"] is True
    assert result["data_dir"] == str(data_dir)
    assert result["thumb_dir"] == str(thumb_dir)


def test_health_probe_reports_storage_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    backend.ensure_setup()

    result = backend.health_probe()

    assert result["capabilities"]["tier"] == "verified"
    assert result["diagnostics"]["probed"] is True


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


def _receipt(key: str = "opaque-key", *, size: int = 5) -> CreationReceipt:
    return CreationReceipt(
        key=key,
        size=size,
        token="test-creation-token",
        backend="compatibility",
        namespace="test-namespace",
    )


def test_compatibility_stream_write_returns_created_byte_count() -> None:
    backend = MagicMock()
    backend.create_stream.return_value = _receipt(size=5)

    written = StorageBackend.write_stream(backend, BytesIO(b"owned"), "blob")

    assert written == 5


def test_compatibility_byte_write_returns_created_byte_count() -> None:
    backend = MagicMock()
    backend.create_bytes.return_value = _receipt(size=5)

    written = StorageBackend.write_bytes(backend, b"owned", "blob")

    assert written == 5


def test_compatibility_backend_rejects_unsupported_atomic_creation() -> None:
    backend = LocalStorageBackend()

    with pytest.raises(NotImplementedError, match="atomic_create_not_supported"):
        StorageBackend.create_stream(backend, BytesIO(b"owned"), "opaque-key")


def test_compatibility_backend_rejects_unsupported_atomic_replacement() -> None:
    backend = LocalStorageBackend()

    with pytest.raises(NotImplementedError, match="atomic_replace_not_supported"):
        StorageBackend.replace_stream(backend, BytesIO(b"new"), _receipt())


def test_compatibility_backend_fails_closed_for_rollback() -> None:
    assert StorageBackend.rollback_create(LocalStorageBackend(), _receipt()) is False


def test_compatibility_backend_fails_closed_for_creation_match() -> None:
    assert StorageBackend.creation_matches(LocalStorageBackend(), _receipt()) is False


def test_compatibility_backend_rejects_unsupported_legacy_adoption() -> None:
    with pytest.raises(
        NotImplementedError, match="existing_storage_adoption_not_supported"
    ):
        StorageBackend.adopt_existing(
            LocalStorageBackend(),
            "opaque-key",
            expected_size=5,
            expected_sha256="a" * 64,
        )


def test_object_info_returns_none_for_missing_key() -> None:
    backend = MagicMock()
    backend.exists.return_value = False

    result = StorageBackend.object_info(backend, "missing")

    assert result is None
    backend.stat_size.assert_not_called()


def test_object_info_returns_size_for_existing_key() -> None:
    backend = MagicMock()
    backend.exists.return_value = True
    backend.stat_size.return_value = 25

    result = StorageBackend.object_info(backend, "present")

    assert result is not None
    assert result.size == 25


def test_local_compatible_backend_without_direct_path_rejects_atomic_create() -> None:
    class OpaqueBackend(LocalStorageBackend):
        def direct_path(self, key: str) -> Path | None:
            return None

    with pytest.raises(NotImplementedError, match="atomic_create_not_supported"):
        OpaqueBackend().create_stream(BytesIO(b"owned"), "opaque-key")


def test_download_cleanup_failure_preserves_published_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    backend = LocalStorageBackend()
    source = tmp_path / "source.stl"
    source.write_bytes(b"solid")
    destination = tmp_path / "copy.stl"
    original_unlink = Path.unlink

    def fail_download_temp(self: Path, *args, **kwargs) -> None:
        if self.name.startswith(".printstash-download-"):
            raise OSError("cleanup denied")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_download_temp)

    result = backend.download_to_path(str(source), destination)

    assert result.read_bytes() == b"solid"
    assert "storage download temp cleanup failed" in caplog.text


def test_usage_skips_entry_whose_stat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    good = root / "good.stl"
    good.write_bytes(b"12345")
    bad = root / "unreadable.stl"
    bad.write_bytes(b"123")
    original_stat = Path.stat
    bad_stat_calls = 0

    def fail_second_bad_stat(self: Path, *args, **kwargs):
        nonlocal bad_stat_calls
        if self == bad:
            bad_stat_calls += 1
            if bad_stat_calls >= 2:
                raise OSError("stat denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_second_bad_stat)

    result = LocalStorageBackend().usage(str(root))

    assert result["object_count"] == 1
    assert result["total_size_bytes"] == 5


def test_create_stream_is_atomic_create_only_and_receipted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    destination = data_dir / "model" / "v1" / "part.stl"

    receipt = backend.create_stream(BytesIO(b"owned"), str(destination))

    assert receipt.key == str(destination)
    assert receipt.size == 5
    assert destination.read_bytes() == b"owned"
    with pytest.raises(StorageCollisionError):
        backend.create_stream(BytesIO(b"attacker"), str(destination))
    assert destination.read_bytes() == b"owned"


def test_rollback_receipt_cannot_delete_a_replaced_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    destination = data_dir / "part.stl"
    receipt = backend.create_bytes(b"created", str(destination))
    destination.unlink()
    destination.write_bytes(b"replacement")

    assert backend.rollback_create(receipt) is False
    assert destination.read_bytes() == b"replacement"


def test_rollback_receipt_deletes_its_matching_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    destination = data_dir / "part.stl"
    receipt = backend.create_bytes(b"created", str(destination))

    assert backend.rollback_create(receipt) is True
    assert destination.exists() is False


def test_rollback_race_after_quarantine_preserves_new_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    destination = data_dir / "part.stl"
    receipt = backend.create_bytes(b"owned", str(destination))
    real_replace = storage_backend.os.replace

    def raced_replace(source, target):
        real_replace(source, target)
        Path(source).write_bytes(b"new-user-file")

    monkeypatch.setattr(storage_backend.os, "replace", raced_replace)

    assert backend.rollback_create(receipt) is True
    assert destination.read_bytes() == b"new-user-file"


def test_explicit_replace_publishes_bytes_with_a_current_creation_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    destination = data_dir / "thumbnail.webp"
    receipt = backend.create_bytes(b"first", str(destination))

    replacement = backend.replace_bytes(b"second", receipt)

    assert destination.read_bytes() == b"second"
    assert backend.creation_matches(replacement)


def test_explicit_replace_rejects_a_stale_creation_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    destination = data_dir / "thumbnail.webp"
    receipt = backend.create_bytes(b"first", str(destination))
    backend.replace_bytes(b"second", receipt)

    with pytest.raises(StorageCollisionError):
        backend.replace_bytes(b"stale", receipt)
    assert destination.read_bytes() == b"second"


def test_two_concurrent_create_only_writes_have_one_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    destination = data_dir / "race.bin"
    barrier = Barrier(3)
    outcomes: list[str] = []

    def write(payload: bytes) -> None:
        barrier.wait(timeout=5)
        try:
            LocalStorageBackend().create_bytes(payload, str(destination))
            outcomes.append("created")
        except StorageCollisionError:
            outcomes.append("collision")

    threads = [Thread(target=write, args=(value,)) for value in (b"one", b"two")]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outcomes) == ["collision", "created"]
    assert destination.read_bytes() in {b"one", b"two"}


def test_managed_create_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    thumb_dir.mkdir()
    outside.mkdir()
    (data_dir / "escaped").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))

    with pytest.raises(StorageCollisionError, match="managed_storage_symlink_escape"):
        LocalStorageBackend().create_bytes(
            b"must-not-escape", str(data_dir / "escaped" / "part.stl")
        )

    assert not (outside / "part.stl").exists()


def test_failed_create_stream_never_publishes_partial_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    destination = data_dir / "partial.bin"

    class _FailingStream:
        calls = 0

        def read(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("source failed")

    with pytest.raises(OSError, match="source failed"):
        LocalStorageBackend().create_stream(_FailingStream(), str(destination))

    assert not destination.exists()
