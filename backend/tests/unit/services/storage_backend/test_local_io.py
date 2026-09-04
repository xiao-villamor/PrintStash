"""Local storage reads, downloads, inventories, and probes stay predictable.

These filesystem operations are the local-first default and must preserve
source bytes, reject destination collisions, and report accurate inventories.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest

from app.services import storage_backend
from app.services.storage_backend import LocalStorageBackend, StorageCollisionError


@dataclass
class FakeSettings:
    data_dir: Path
    thumb_dir: Path
    storage_identity: str = "a" * 64


class TestStatSize:
    def test_reports_the_size_of_the_object(self, tmp_path: Path) -> None:
        blob = tmp_path / "part.stl"
        blob.write_bytes(b"0123456789")

        assert LocalStorageBackend().stat_size(str(blob)) == 10


class TestReadBytes:
    def test_returns_the_whole_object(self, tmp_path: Path) -> None:
        blob = tmp_path / "part.stl"
        blob.write_bytes(b"0123456789")

        assert LocalStorageBackend().read_bytes(str(blob)) == b"0123456789"


class TestStreamChunks:
    def test_stream_chunks_yields_full_content(self, tmp_path: Path) -> None:
        blob = tmp_path / "part.stl"
        blob.write_bytes(b"a" * 5000)

        chunks = list(LocalStorageBackend().stream_chunks(str(blob), chunk_size=2000))

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
        assert src.exists()

    def test_download_to_path_collision_preserves_existing_destination(
        self, tmp_path: Path
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
    def test_explicit_enrollment_upgrades_a_proven_legacy_marker(
        self, tmp_path: Path
    ) -> None:
        from app.services.storage_backend import enroll_legacy_local_root

        root = tmp_path / "legacy"
        root.mkdir()
        payload = root / "part.stl"
        payload.write_bytes(b"legacy-bytes")
        (root / ".printstash-storage-root.json").write_text(
            '{"installation":"installation","role":"data"}', encoding="utf-8"
        )

        assert enroll_legacy_local_root(
            root,
            role="data",
            installation="installation",
            proofs=[
                (
                    payload,
                    payload.stat().st_size,
                    hashlib.sha256(payload.read_bytes()).hexdigest(),
                )
            ],
        )
        assert json.loads(
            (root / ".printstash-storage-root.json").read_text(encoding="utf-8")
        ) == {"format": 1, "installation": "installation", "role": "data"}

    def test_legacy_enrollment_rejects_size_only_evidence(self, tmp_path: Path) -> None:
        from app.services.storage_backend import enroll_legacy_local_root

        root = tmp_path / "legacy"
        root.mkdir()
        payload = root / "part.stl"
        payload.write_bytes(b"legacy-bytes")

        assert not enroll_legacy_local_root(
            root,
            role="data",
            installation="installation",
            proofs=[(payload, payload.stat().st_size, None)],
        )
        assert not (root / ".printstash-storage-root.json").exists()

    def test_does_not_create_missing_configured_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        monkeypatch.setattr(
            storage_backend, "settings", FakeSettings(data_dir, thumb_dir)
        )

        LocalStorageBackend().ensure_setup()

        assert not data_dir.exists()
        assert not thumb_dir.exists()


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

        assert {Path(p).name for p in listed} == {"one.stl", "two.stl"}

    def test_returns_nothing_for_a_prefix_that_does_not_exist(
        self, tmp_path: Path
    ) -> None:
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
        root = tmp_path / "vault"
        root.mkdir()
        (root / "one.stl").write_bytes(b"12345")
        (root / "two.stl").write_bytes(b"12")

        result = LocalStorageBackend().usage(str(root))

        assert result["object_count"] == 2
        assert result["total_size_bytes"] == 7
        assert result["backend"] == "local"

    def test_usage_missing_root_returns_zero(self, tmp_path: Path) -> None:
        result = LocalStorageBackend().usage(str(tmp_path / "nowhere"))
        assert result == {
            "backend": "local",
            "prefix": str(tmp_path / "nowhere"),
            "object_count": 0,
            "total_size_bytes": 0,
        }


class TestPresignedDownloadUrl:
    def test_presigned_download_url_is_unsupported_locally(self) -> None:
        assert (
            LocalStorageBackend().presigned_download_url("any-key", "file.stl") is None
        )


class TestHealthProbe:
    def test_health_probe_reports_ok_when_both_dirs_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "files"
        thumb_dir = tmp_path / "thumbs"
        data_dir.mkdir()
        thumb_dir.mkdir()
        for role, root in (("data", data_dir), ("thumb", thumb_dir)):
            (root / ".printstash-storage-root.json").write_text(
                '{"format":1,"installation":"%s","role":"%s"}' % ("a" * 64, role),
                encoding="utf-8",
            )
        monkeypatch.setattr(
            storage_backend, "settings", FakeSettings(data_dir, thumb_dir)
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
            storage_backend, "settings", FakeSettings(data_dir, thumb_dir)
        )

        result = LocalStorageBackend().health_probe()

        assert result["ok"] is False
