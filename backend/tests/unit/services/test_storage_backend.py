"""Direct coverage for LocalStorageBackend's filesystem operations —
move/read/stream/upload/delete/list/walk/usage/health_probe — beyond the
direct_path/local_path/move_in seam already covered in test_storage_seam.py."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Barrier, Thread

import pytest

from app.services import storage_backend
from app.services.storage_backend import LocalStorageBackend, StorageCollisionError


@dataclass
class _FakeSettings:
    data_dir: Path
    thumb_dir: Path
    storage_identity: str = "test-installation"


class TestReplaceStream:
    def test_explicit_replace_requires_current_creation_receipt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )
        backend = LocalStorageBackend()
        destination = data_dir / "thumbnail.webp"
        receipt = backend.create_bytes(b"first", str(destination))

        replacement = backend.replace_bytes(b"second", receipt)

        assert destination.read_bytes() == b"second"
        assert backend.creation_matches(replacement)
        with pytest.raises(StorageCollisionError):
            backend.replace_bytes(b"stale", receipt)
        assert destination.read_bytes() == b"second"

    def test_rollback_race_after_quarantine_preserves_new_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )
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


class TestCreateOnlyWrites:
    """Two writers reaching the same key, and the one that must lose.

    Create-only is the whole safety model: a write that would land on an existing
    object raises instead of overwriting it, so two ingests that dedup to the
    same key cannot destroy each other's bytes. These pin the loser's side —
    the failed writer leaves nothing behind, including no partial file."""

    def test_two_concurrent_create_only_writes_have_one_winner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        outside = tmp_path / "outside"
        data_dir.mkdir()
        thumb_dir.mkdir()
        outside.mkdir()
        (data_dir / "escaped").symlink_to(outside, target_is_directory=True)
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )

        with pytest.raises(
            StorageCollisionError, match="managed_storage_symlink_escape"
        ):
            LocalStorageBackend().create_bytes(
                b"must-not-escape", str(data_dir / "escaped" / "part.stl")
            )

        assert not (outside / "part.stl").exists()

    def test_refuses_an_unchecked_move_without_touching_the_source(
        self, tmp_path: Path
    ) -> None:
        backend = LocalStorageBackend()
        src = tmp_path / "staged.stl"
        src.write_bytes(b"solid")
        dest = tmp_path / "nested" / "dir" / "final.stl"

        with pytest.raises(RuntimeError, match="unchecked_storage_move_disabled"):
            backend.move(str(src), str(dest))

        assert src.read_bytes() == b"solid"
        assert not dest.exists()


class TestCreateStream:
    def test_returns_a_receipt_for_the_bytes_it_wrote(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )
        backend = LocalStorageBackend()
        destination = data_dir / "model" / "v1" / "part.stl"

        receipt = backend.create_stream(BytesIO(b"owned"), str(destination))

        # The receipt is the proof of ownership every later delete checks, so it
        # has to describe the object that was actually written.
        assert (receipt.key, receipt.size) == (str(destination), 5)
        assert destination.read_bytes() == b"owned"

    def test_refuses_a_second_write_to_the_same_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )
        backend = LocalStorageBackend()
        destination = data_dir / "model" / "v1" / "part.stl"
        backend.create_stream(BytesIO(b"owned"), str(destination))

        with pytest.raises(StorageCollisionError):
            backend.create_stream(BytesIO(b"attacker"), str(destination))

        # Create-only is the whole safety model: the loser of a race must not be
        # able to replace bytes somebody else already owns.
        assert destination.read_bytes() == b"owned"

    def test_failed_create_stream_never_publishes_partial_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )
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


class TestStatSize:
    def test_reports_the_size_of_the_object(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        blob = tmp_path / "part.stl"
        blob.write_bytes(b"0123456789")

        assert backend.stat_size(str(blob)) == 10


class TestReadBytes:
    def test_returns_the_whole_object(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        blob = tmp_path / "part.stl"
        blob.write_bytes(b"0123456789")

        assert backend.read_bytes(str(blob)) == b"0123456789"


class TestStreamChunks:
    def test_stream_chunks_yields_full_content(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        blob = tmp_path / "part.stl"
        blob.write_bytes(b"a" * 5000)

        chunks = list(backend.stream_chunks(str(blob), chunk_size=2000))

        assert len(chunks) == 3
        assert b"".join(chunks) == b"a" * 5000


class TestDownloadToPath:
    def test_copies_into_a_destination_whose_parents_do_not_exist(
        self, tmp_path: Path
    ) -> None:
        backend = LocalStorageBackend()
        src = tmp_path / "source.stl"
        src.write_bytes(b"solid")
        dest = tmp_path / "nested" / "copy.stl"

        result = backend.download_to_path(str(src), dest)

        assert result == dest
        assert dest.read_bytes() == b"solid"
        assert src.exists()  # copy, not move

    def test_download_to_path_collision_preserves_existing_destination(
        self,
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


class TestUploadFile:
    def test_upload_file_copies_into_key_path(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        src = tmp_path / "source.stl"
        src.write_bytes(b"solid")
        dest_key = tmp_path / "nested" / "uploaded.stl"

        backend.upload_file(src, str(dest_key))

        assert dest_key.read_bytes() == b"solid"
        assert src.exists()


class TestEnsureSetup:
    def test_creates_every_directory_the_backend_writes_into(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )

        LocalStorageBackend().ensure_setup()

        assert data_dir.is_dir()
        assert thumb_dir.is_dir()


class TestDelete:
    def test_refuses_an_unchecked_delete_without_removing_the_file(
        self, tmp_path: Path
    ) -> None:
        backend = LocalStorageBackend()
        blob = tmp_path / "part.stl"
        blob.write_bytes(b"solid")

        with pytest.raises(RuntimeError, match="unchecked_storage_delete_disabled"):
            backend.delete(str(blob))
        assert blob.read_bytes() == b"solid"

    def test_rollback_receipt_cannot_delete_a_replaced_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )
        backend = LocalStorageBackend()
        destination = data_dir / "part.stl"
        receipt = backend.create_bytes(b"created", str(destination))
        destination.unlink()
        destination.write_bytes(b"replacement")

        assert backend.rollback_create(receipt) is False
        assert destination.read_bytes() == b"replacement"


def _nested_tree(root: Path) -> Path:
    """Two objects, one of them a directory deep, under a single prefix."""
    (root / "a").mkdir()
    (root / "a" / "one.stl").write_bytes(b"1")
    (root / "a" / "b").mkdir()
    (root / "a" / "b" / "two.stl").write_bytes(b"22")
    return root / "a"


class TestListKeys:
    def test_finds_objects_nested_below_the_prefix(self, tmp_path: Path) -> None:
        prefix = _nested_tree(tmp_path)

        listed = LocalStorageBackend().list_keys(str(prefix))

        # A non-recursive listing would find one of the two and report a vault
        # smaller than it is — which the audit then reads as missing objects.
        assert {Path(p).name for p in listed} == {"one.stl", "two.stl"}

    def test_returns_nothing_for_a_prefix_that_does_not_exist(
        self, tmp_path: Path
    ) -> None:
        # Empty rather than raising: the audit walks prefixes that legitimately
        # hold nothing yet, and an exception there aborts the whole sweep.
        assert LocalStorageBackend().list_keys(str(tmp_path / "does-not-exist")) == []


class TestWalkKeys:
    def test_finds_objects_nested_below_the_prefix(self, tmp_path: Path) -> None:
        prefix = _nested_tree(tmp_path)

        walked = list(LocalStorageBackend().walk_keys(str(prefix)))

        assert {Path(p).name for p in walked} == {"one.stl", "two.stl"}

    def test_returns_nothing_for_a_prefix_that_does_not_exist(
        self, tmp_path: Path
    ) -> None:
        assert (
            list(LocalStorageBackend().walk_keys(str(tmp_path / "does-not-exist")))
            == []
        )


class TestUsage:
    def test_summarises_the_objects_under_a_prefix(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        root = tmp_path / "vault"
        root.mkdir()
        (root / "one.stl").write_bytes(b"12345")
        (root / "two.stl").write_bytes(b"12")

        result = backend.usage(str(root))

        assert result["object_count"] == 2
        assert result["total_size_bytes"] == 7
        assert result["backend"] == "local"

    def test_usage_missing_root_returns_zero(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        result = backend.usage(str(tmp_path / "nowhere"))
        assert result == {
            "backend": "local",
            "prefix": str(tmp_path / "nowhere"),
            "object_count": 0,
            "total_size_bytes": 0,
        }


class TestPresignedDownloadUrl:
    def test_presigned_download_url_is_unsupported_locally(self) -> None:
        backend = LocalStorageBackend()
        assert backend.presigned_download_url("any-key", "file.stl") is None


class TestHealthProbe:
    def test_health_probe_reports_ok_when_both_dirs_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )

        result = LocalStorageBackend().health_probe()

        assert result["backend"] == "local"
        assert result["ok"] is True
        assert result["data_dir"] == str(data_dir)
        assert result["thumb_dir"] == str(thumb_dir)
        assert result["capabilities"]["object_identity"] == "inode"
        assert isinstance(result["diagnostics"], dict)

    def test_health_probe_reports_not_ok_when_a_dir_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        data_dir.mkdir()
        thumb_dir = tmp_path / "thumbs-missing"
        monkeypatch.setattr(
            storage_backend, "settings", _FakeSettings(data_dir, thumb_dir)
        )

        result = LocalStorageBackend().health_probe()

        assert result["ok"] is False
