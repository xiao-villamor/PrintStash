"""Path handling for the two operations that cannot be taken back.

Deleting a file and choosing where to write one are the two places a path bug
becomes data loss, so both are checked against symlinks — the mechanism by which
a path inside the vault resolves to something outside it.

Both directions are covered, because they fail differently: a symlinked **leaf**
must be refused *without touching its target* (following it deletes somebody
else's file), and a symlinked **intermediate directory** must be refused too,
since a safe-looking filename underneath it lands wherever the link points.

The slug rows are a smaller matter with a user-visible edge: a slug is part of a
URL and a storage key, so it has to be stable, unique, and non-empty even when the
model name is entirely punctuation or non-Latin — a name that slugifies to nothing
would otherwise collide with every other such name.

`stream_to_path` hashes the same single pass it writes. Hashing separately means
reading the file twice and asserting on bytes that may have changed in between,
which is exactly the window a content-addressed store cannot afford.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.storage import (
    UploadTooLarge,
    ensure_unique_slug,
    slugify,
    stream_to_path,
)
from app.services.storage_backend import LocalStorageBackend
from app.services.storage_paths import StoragePathOverlapError, unlink_managed_file


class TestManagedUnlink:
    def test_managed_unlink_rejects_leaf_symlink_without_touching_target(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "staging"
        root.mkdir()
        target = root / "other-user-file.bin"
        target.write_bytes(b"preserve")
        link = root / "operation-temp.bin"
        link.symlink_to(target)

        with pytest.raises(StoragePathOverlapError, match="symlink"):
            unlink_managed_file(link, root)

        assert link.is_symlink()
        assert target.read_bytes() == b"preserve"

    def test_managed_unlink_rejects_intermediate_symlink_escape(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "staging"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        target = outside / "keep.bin"
        target.write_bytes(b"preserve")
        (root / "redirect").symlink_to(outside, target_is_directory=True)

        with pytest.raises(StoragePathOverlapError, match="outside_root"):
            unlink_managed_file(root / "redirect" / "keep.bin", root)

        assert target.read_bytes() == b"preserve"


class TestEnsureUniqueSlug:
    def test_ensure_unique_slug_keeps_first_free_candidate(self) -> None:
        existing = {"gear", "gear-2", "gear-3"}

        slug = ensure_unique_slug("gear", existing.__contains__)

        assert slug == "gear-4"


class TestSlugify:
    def test_slugify_normalises_unicode_punctuation(self) -> None:
        assert slugify("  Café Racer — Bracket v2!! ") == "cafe-racer-bracket-v2"

    def test_slugify_falls_back_for_a_name_with_nothing_sluggable(self) -> None:
        # The fallback matters more than it looks: an empty slug would make the
        # model unaddressable by URL.
        assert slugify("___") == "model"


class TestStreamToPath:
    def test_stream_to_path_writes_through_missing_parent_directories(
        self,
        tmp_path: Path,
    ) -> None:
        dest = tmp_path / "nested" / "upload.gcode"
        payload = b"G1 X0\n" * 3

        written = stream_to_path(BytesIO(payload), dest)

        assert written == len(payload)
        assert dest.read_bytes() == payload

    def test_stream_to_path_hashes_the_same_single_pass_it_publishes(
        self,
        tmp_path: Path,
    ) -> None:
        payload = b"one-pass-staging" * 1024
        digest = hashlib.sha256()
        dest = tmp_path / "staged.bin"

        written = stream_to_path(BytesIO(payload), dest, digest=digest)

        assert written == len(payload)
        assert digest.hexdigest() == hashlib.sha256(payload).hexdigest()

    def test_stream_to_path_leaves_no_partial_file_when_it_aborts(
        self, tmp_path: Path
    ) -> None:
        class CountingStream(BytesIO):
            bytes_read = 0

            def read(self, size: int = -1) -> bytes:
                chunk = super().read(size)
                self.bytes_read += len(chunk)
                return chunk

        source = CountingStream(b"x" * (3 * 1024 * 1024))
        dest = tmp_path / "oversized.bin"

        with pytest.raises(UploadTooLarge):
            stream_to_path(source, dest, max_bytes=1024 * 1024)

        assert source.bytes_read <= 2 * 1024 * 1024
        assert not dest.exists()

    def test_stream_to_path_collision_preserves_existing_bytes(
        self, tmp_path: Path
    ) -> None:
        dest = tmp_path / "occupied.bin"
        dest.write_bytes(b"user-owned")

        with pytest.raises(FileExistsError):
            stream_to_path(BytesIO(b"replacement"), dest)

        assert dest.read_bytes() == b"user-owned"

    def test_stream_to_path_read_failure_never_publishes_partial_file(
        self,
        tmp_path: Path,
    ) -> None:
        class FailingStream(BytesIO):
            calls = 0

            def read(self, size: int = -1) -> bytes:
                self.calls += 1
                if self.calls == 1:
                    return b"partial"
                raise OSError("upload stream failed")

        dest = tmp_path / "failed-upload.bin"

        with pytest.raises(OSError, match="upload stream failed"):
            stream_to_path(FailingStream(), dest)

        assert not dest.exists()


class TestCaptureUploadSlotKey:
    def test_local_capture_upload_slot_key_is_in_managed_data_namespace(self) -> None:
        assert LocalStorageBackend().capture_upload_slot_key("slot-1") == str(
            settings.data_dir / "capture-slots" / "slot-1"
        )
