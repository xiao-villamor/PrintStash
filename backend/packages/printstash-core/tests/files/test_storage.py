"""Naming and writing files on the host's disk from names the host did not choose.

Every value these helpers take comes from outside: an uploaded filename, a model
title typed by a user, bytes arriving over HTTP. What comes out is a path on the
operator's filesystem and a row in the library. So this module is a boundary, and
the tests are mostly refusals.

**`validate_leaf_name` is the traversal guard.** It takes a single filename and
must reject anything that is not one — a separator either way round, a drive
prefix, a dot segment, a control character, a byte sequence long enough to
overflow a filesystem's limit. Any of those reaching a path join is a write
outside the storage root.

**`stream_to_path` never overwrites.** The private temp is written and synced
before publication. Hard links publish atomically; unsupported filesystems use
an exclusive copy and return only after syncing. Both reject collisions and
symlinks. Failed reads and size limits publish nothing. A failed fallback copy
preserves the uncertain destination rather than risk deleting a replacement;
the private temp is still cleaned up.

**Slugs must not collide.** A slug is a URL, so `ensure_unique_slug` walks past
every taken name rather than picking the first candidate.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest

from printstash_core.files import (
    PublicationStrategy,
    UnsafeStorageComponent,
    UploadTooLarge,
    ensure_unique_slug,
    publish_staged_file,
    sha256_file,
    sha256_stream,
    slugify,
    stream_to_path,
    validate_leaf_name,
)

HELLO_SHA256 = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
STAGING_GLOB = ".printstash-stage-*"


class TestSha256File:
    def test_hashes_a_file_on_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "payload.bin"
        path.write_bytes(b"hello world")

        # The content hash is the library's dedupe key, so it has to match the
        # canonical SHA-256 of the bytes exactly.
        assert sha256_file(path) == HELLO_SHA256

    def test_hashes_a_file_larger_than_one_read(self, tmp_path: Path) -> None:
        payload = b"chunk" * 200_000
        path = tmp_path / "large.bin"
        path.write_bytes(payload)

        # A G-code file is routinely hundreds of megabytes; the hash is streamed
        # rather than read whole, and the chunking must not change the digest.
        assert sha256_file(path) == hashlib.sha256(payload).hexdigest()


class TestSha256Stream:
    def test_hashes_a_stream(self) -> None:
        assert sha256_stream(BytesIO(b"hello world")) == HELLO_SHA256

    def test_leaves_the_stream_consumed(self) -> None:
        stream = BytesIO(b"hello world")

        sha256_stream(stream)

        # Callers hash then stage from the same stream, so the position after
        # hashing is part of the contract rather than an accident.
        assert stream.read() == b""

    def test_hashes_an_empty_stream(self) -> None:
        assert sha256_stream(BytesIO(b"")) == hashlib.sha256(b"").hexdigest()


class TestValidateLeafName:
    def test_accepts_an_ordinary_filename(self) -> None:
        assert validate_leaf_name("bracket.stl") == "bracket.stl"

    @pytest.mark.parametrize(
        "unsafe_name",
        [
            "",
            ".",
            "..",
            "/absolute.stl",
            "nested/file.stl",
            "nested\\file.stl",
            "C:drive-relative.stl",
            "line\nbreak.stl",
            "null\0byte.stl",
            "delete\x7f.stl",
        ],
    )
    def test_refuses_a_name_that_is_not_a_single_filename(
        self, unsafe_name: str
    ) -> None:
        # Each of these reaching a path join is a write outside the storage
        # root, or a filename a different filesystem would read differently.
        with pytest.raises(UnsafeStorageComponent, match="^unsafe_storage_component$"):
            validate_leaf_name(unsafe_name)

    def test_normalizes_a_decomposed_unicode_name(self) -> None:
        # macOS hands over NFD, Linux NFC. Storing both spellings would give one
        # file two names and defeat the dedupe.
        assert validate_leaf_name("cafe\N{COMBINING ACUTE ACCENT}.stl") == "café.stl"

    def test_accepts_a_name_at_the_byte_limit(self) -> None:
        # The limit is on *bytes*, not characters: 127 two-byte characters plus
        # one ASCII is exactly 255.
        name = "é" * 127 + "a"

        assert validate_leaf_name(name) == name

    def test_refuses_a_name_over_the_byte_limit(self) -> None:
        # 128 two-byte characters is 256, which most filesystems truncate — and
        # a truncated name can collide with a different file.
        with pytest.raises(UnsafeStorageComponent):
            validate_leaf_name("é" * 128)

    def test_honours_a_caller_supplied_byte_limit(self) -> None:
        with pytest.raises(UnsafeStorageComponent):
            validate_leaf_name("bracket.stl", max_bytes=4)


class TestSlugify:
    def test_reduces_a_title_to_a_url_safe_slug(self) -> None:
        # Accents folded, punctuation dropped, spaces joined: a slug appears in
        # a URL, so it has to survive being typed and copied.
        assert slugify("  Café Racer — Bracket v2!! ") == "cafe-racer-bracket-v2"

    def test_falls_back_to_a_generic_slug_when_nothing_survives(self) -> None:
        # A title of only emoji or punctuation still needs a routable URL.
        assert slugify("🎉") == "model"


class TestEnsureUniqueSlug:
    def test_keeps_a_slug_nobody_has_taken(self) -> None:
        assert ensure_unique_slug("gear", lambda _slug: False) == "gear"

    def test_walks_past_every_taken_variant(self) -> None:
        existing = {"gear", "gear-2", "gear-3"}

        # Not just "is the base taken": a slug is a URL, and stopping at the
        # first collision would keep producing one that is already in use.
        assert ensure_unique_slug("gear", existing.__contains__) == "gear-4"


class TestStreamToPath:
    def test_writes_the_bytes_it_was_given(self, tmp_path: Path) -> None:
        payload = b"one-pass-staging" * 100_000
        destination = tmp_path / "nested" / "artifact.bin"

        written = stream_to_path(BytesIO(payload), destination)

        assert written == len(payload)
        assert destination.read_bytes() == payload

    def test_creates_the_parent_directory(self, tmp_path: Path) -> None:
        destination = tmp_path / "a" / "b" / "artifact.bin"

        stream_to_path(BytesIO(b"payload"), destination)

        assert destination.exists()

    def test_hashes_while_it_writes(self, tmp_path: Path) -> None:
        payload = b"one-pass-staging" * 1000
        digest = hashlib.sha256()

        stream_to_path(BytesIO(payload), tmp_path / "artifact.bin", digest=digest)

        # One pass over the bytes, not two: the alternative is reading a
        # multi-hundred-megabyte artifact twice in the request path.
        assert digest.hexdigest() == hashlib.sha256(payload).hexdigest()

    def test_leaves_no_staging_file_behind(self, tmp_path: Path) -> None:
        stream_to_path(BytesIO(b"payload"), tmp_path / "artifact.bin")

        # An orphaned staging file is invisible to the library and to garbage
        # collection, so it accumulates until the disk fills.
        assert list(tmp_path.glob(STAGING_GLOB)) == []

    def test_refuses_to_replace_an_existing_file(self, tmp_path: Path) -> None:
        destination = tmp_path / "artifact.bin"
        destination.write_bytes(b"existing")

        with pytest.raises(FileExistsError):
            stream_to_path(BytesIO(b"replacement"), destination)

        # Storage keys are content-addressed, so a collision means two callers
        # raced for one key — not that the new bytes are better.
        assert destination.read_bytes() == b"existing"

    def test_leaves_no_staging_file_behind_after_a_collision(
        self, tmp_path: Path
    ) -> None:
        destination = tmp_path / "artifact.bin"
        destination.write_bytes(b"existing")

        with pytest.raises(FileExistsError):
            stream_to_path(BytesIO(b"replacement"), destination)

        assert list(tmp_path.glob(STAGING_GLOB)) == []

    def test_does_not_write_through_a_symlink_at_the_destination(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "target.bin"
        target.write_bytes(b"existing")
        destination = tmp_path / "artifact.bin"
        destination.symlink_to(target)

        with pytest.raises(FileExistsError):
            stream_to_path(BytesIO(b"replacement"), destination)

        # A symlink planted at a predictable storage key would otherwise
        # redirect the write anywhere the process can reach.
        assert destination.is_symlink()
        assert target.read_bytes() == b"existing"

    def test_refuses_a_stream_longer_than_the_limit(self, tmp_path: Path) -> None:
        destination = tmp_path / "too-large.bin"

        with pytest.raises(UploadTooLarge):
            stream_to_path(BytesIO(b"1234"), destination, max_bytes=3)

        # Enforced while streaming, so an oversized upload costs the limit in
        # disk rather than its full size.
        assert not destination.exists()

    def test_accepts_a_stream_exactly_at_the_limit(self, tmp_path: Path) -> None:
        destination = tmp_path / "exact.bin"

        assert stream_to_path(BytesIO(b"1234"), destination, max_bytes=4) == 4

    def test_publishes_nothing_when_the_stream_fails_midway(
        self, tmp_path: Path
    ) -> None:
        class FailingStream(BytesIO):
            calls = 0

            def read(self, size: int = -1) -> bytes:
                self.calls += 1
                if self.calls == 1:
                    return b"partial"
                raise OSError("stream failed")

        destination = tmp_path / "failed.bin"

        with pytest.raises(OSError, match="stream failed"):
            stream_to_path(FailingStream(), destination)

        # A dropped upload must not leave a truncated artifact the library
        # believes is complete.
        assert not destination.exists()

    def test_publishes_the_file_even_if_the_staging_file_cannot_be_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_unlink = Path.unlink

        def refuse_staging_unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self.name.startswith(".printstash-stage-"):
                raise OSError("read-only filesystem")
            real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "unlink", refuse_staging_unlink)
        destination = tmp_path / "artifact.bin"

        # Cleanup runs in a `finally`. Raising there would turn a successfully
        # published artifact into a reported upload failure, and the caller
        # would retry a write that already landed.
        assert stream_to_path(BytesIO(b"payload"), destination) == 7
        assert destination.read_bytes() == b"payload"

    def test_leaves_no_staging_file_behind_after_a_failed_stream(
        self, tmp_path: Path
    ) -> None:
        class FailingStream(BytesIO):
            def read(self, size: int = -1) -> bytes:
                raise OSError("stream failed")

        with pytest.raises(OSError, match="stream failed"):
            stream_to_path(FailingStream(), tmp_path / "failed.bin")

        assert list(tmp_path.glob(STAGING_GLOB)) == []

    @pytest.mark.parametrize(
        "link_errno",
        [errno.EPERM, errno.EOPNOTSUPP, errno.EXDEV, errno.EMLINK],
        ids=["unraid", "unsupported", "cross-device", "link-limit"],
    )
    def test_stages_without_hardlinks(self, tmp_path, monkeypatch, link_errno):
        def unavailable(*args, **kwargs):
            raise OSError(link_errno, "hard links unavailable")

        monkeypatch.setattr(os, "link", unavailable)
        destination = tmp_path / "upload.stl"
        payload = b"model bytes" * 100_000
        digest = hashlib.sha256()

        written = stream_to_path(BytesIO(payload), destination, digest=digest)

        assert written == len(payload)
        assert destination.read_bytes() == payload
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        assert digest.hexdigest() == hashlib.sha256(payload).hexdigest()
        assert list(tmp_path.glob(STAGING_GLOB)) == []

    @pytest.mark.parametrize("kind", ["file", "symlink", "dangling"], ids=str)
    def test_fallback_refuses_existing_destination(self, tmp_path, monkeypatch, kind):
        destination = tmp_path / "upload.stl"
        target = tmp_path / "original.stl"
        target.write_bytes(b"original")
        creators = {
            "file": lambda: destination.write_bytes(b"original"),
            "symlink": lambda: destination.symlink_to(target),
            "dangling": lambda: destination.symlink_to(tmp_path / "absent"),
        }
        creators[kind]()
        monkeypatch.setattr(
            os, "link", Mock(side_effect=PermissionError(errno.EPERM, "no links"))
        )

        with pytest.raises(FileExistsError):
            stream_to_path(BytesIO(b"replacement"), destination)

        assert target.read_bytes() == b"original"
        assert destination.is_symlink() or destination.read_bytes() == b"original"
        assert not (tmp_path / "absent").exists()
        assert list(tmp_path.glob(STAGING_GLOB)) == []

    def test_fallback_keeps_one_concurrent_winner(self, tmp_path, monkeypatch):
        destination = tmp_path / "upload.stl"
        barrier = Barrier(2)

        def unavailable(*args, **kwargs):
            barrier.wait(timeout=5)
            raise PermissionError(errno.EPERM, "no links")

        def write(payload):
            try:
                stream_to_path(BytesIO(payload), destination)
                return payload
            except FileExistsError:
                return None

        monkeypatch.setattr(os, "link", unavailable)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, [b"first" * 100_000, b"second" * 100_000]))

        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        assert destination.read_bytes() == winners[0]
        assert list(tmp_path.glob(STAGING_GLOB)) == []

    @pytest.mark.parametrize(
        "link_errno",
        [errno.EIO, errno.EACCES, errno.ENOSPC],
        ids=["io-error", "permissions", "disk-full"],
    )
    def test_propagates_unexpected_link_failure(
        self, tmp_path, monkeypatch, link_errno
    ):
        destination = tmp_path / "upload.stl"
        monkeypatch.setattr(os, "link", Mock(side_effect=OSError(link_errno, "failed")))

        with pytest.raises(OSError) as caught:
            stream_to_path(BytesIO(b"payload"), destination)

        assert caught.value.errno == link_errno
        assert not destination.exists()
        assert list(tmp_path.glob(STAGING_GLOB)) == []

    def test_fallback_propagates_copy_failure(self, tmp_path, monkeypatch):
        destination = tmp_path / "upload.stl"
        monkeypatch.setattr(
            os, "link", Mock(side_effect=PermissionError(errno.EPERM, "no links"))
        )

        def fail_copy(source, output, *args, **kwargs):
            output.write(b"partial")
            raise OSError(errno.ENOSPC, "disk full")

        monkeypatch.setattr("shutil.copyfileobj", fail_copy)

        with pytest.raises(OSError, match="disk full"):
            stream_to_path(BytesIO(b"payload"), destination)

        assert destination.read_bytes() == b"partial"
        assert list(tmp_path.glob(STAGING_GLOB)) == []

    def test_fallback_preserves_raced_replacement_on_failure(
        self, tmp_path, monkeypatch
    ):
        destination = tmp_path / "upload.stl"
        monkeypatch.setattr(
            os, "link", Mock(side_effect=PermissionError(errno.EPERM, "no links"))
        )

        def replace_then_fail(source, output, *args, **kwargs):
            destination.unlink()
            destination.write_bytes(b"another writer")
            raise OSError(errno.EIO, "copy failed")

        monkeypatch.setattr("shutil.copyfileobj", replace_then_fail)

        with pytest.raises(OSError, match="copy failed"):
            stream_to_path(BytesIO(b"payload"), destination)

        assert destination.read_bytes() == b"another writer"
        assert list(tmp_path.glob(STAGING_GLOB)) == []

    def test_fallback_propagates_destination_open_failure(self, tmp_path, monkeypatch):
        destination = tmp_path / "upload.stl"
        real_open = os.open

        def deny_destination(path, *args, **kwargs):
            if path == destination:
                raise PermissionError(errno.EACCES, "access denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(
            os, "link", Mock(side_effect=PermissionError(errno.EPERM, "no links"))
        )
        monkeypatch.setattr(os, "open", deny_destination)

        with pytest.raises(PermissionError, match="access denied"):
            stream_to_path(BytesIO(b"payload"), destination)

        assert not destination.exists()
        assert list(tmp_path.glob(STAGING_GLOB)) == []

    def test_fallback_propagates_sync_failure(self, tmp_path, monkeypatch):
        destination = tmp_path / "upload.stl"
        real_fsync = os.fsync
        calls = 0

        def fail_destination_sync(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.EIO, "sync failed")
            real_fsync(fd)

        monkeypatch.setattr(
            os, "link", Mock(side_effect=PermissionError(errno.EPERM, "no links"))
        )
        monkeypatch.setattr(os, "fsync", fail_destination_sync)

        with pytest.raises(OSError, match="sync failed"):
            stream_to_path(BytesIO(b"payload"), destination)

        assert destination.read_bytes() == b"payload"
        assert list(tmp_path.glob(STAGING_GLOB)) == []


class TestPublishStagedFile:
    def test_copy_stays_in_pinned_directories(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()
        (source / "upload").write_bytes(b"verified bytes")
        source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
        target_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)

        def unsupported(*args, **kwargs):
            raise OSError(errno.EPERM, "no hard links")

        monkeypatch.setattr(os, "link", unsupported)
        moved = tmp_path / "pinned"
        target.rename(moved)
        target.mkdir()
        try:
            publish_staged_file(
                Path("upload"),
                Path("result"),
                src_dir_fd=source_fd,
                dst_dir_fd=target_fd,
            )
        finally:
            os.close(source_fd)
            os.close(target_fd)
        assert (moved / "result").read_bytes() == b"verified bytes"
        assert not (target / "result").exists()

    def test_uses_known_copy_capability(self, tmp_path, monkeypatch):
        source = tmp_path / "source"
        source.write_bytes(b"private bytes")
        monkeypatch.setattr(
            os, "link", Mock(side_effect=AssertionError("known unsupported"))
        )
        destination = tmp_path / "destination"
        assert (
            publish_staged_file(source, destination, strategy=PublicationStrategy.COPY)
            is PublicationStrategy.COPY
        )
        assert destination.read_bytes() == b"private bytes"
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600

    def test_identity_only_publication_never_substitutes_copy(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "source"
        source.write_bytes(b"owned inode")
        monkeypatch.setattr(
            os, "link", Mock(side_effect=OSError(errno.EPERM, "no links"))
        )
        destination = tmp_path / "destination"
        with pytest.raises(OSError) as caught:
            publish_staged_file(source, destination, strategy=PublicationStrategy.LINK)
        assert caught.value.errno == errno.EPERM
        assert not destination.exists()

    def test_copy_rejects_a_symlink_source(self, tmp_path):
        source = tmp_path / "source"
        source.symlink_to(tmp_path / "elsewhere")
        (tmp_path / "elsewhere").write_bytes(b"unowned")
        destination = tmp_path / "destination"
        with pytest.raises(OSError):
            publish_staged_file(source, destination, strategy=PublicationStrategy.COPY)
        assert not destination.exists()

    def test_copy_rejects_a_directory_source(self, tmp_path):
        with pytest.raises(OSError):
            publish_staged_file(
                tmp_path, tmp_path / "destination", strategy=PublicationStrategy.COPY
            )
        assert not (tmp_path / "destination").exists()

    def test_rejects_source_changes_during_copy(self, tmp_path, monkeypatch):
        import shutil

        source = tmp_path / "source"
        source.write_bytes(b"first version")
        destination = tmp_path / "destination"
        original = shutil.copyfileobj

        def changing_copy(src, dst, length):
            original(src, dst, length)
            source.write_bytes(b"different version")

        monkeypatch.setattr(shutil, "copyfileobj", changing_copy)
        with pytest.raises(OSError, match="source changed"):
            publish_staged_file(source, destination, strategy=PublicationStrategy.COPY)
        assert destination.read_bytes() == b"first version"

    def test_copy_rejects_a_fifo_source(self, tmp_path):
        source = tmp_path / "pipe"
        os.mkfifo(source)
        with pytest.raises(OSError, match="not a regular file"):
            publish_staged_file(
                source, tmp_path / "destination", strategy=PublicationStrategy.COPY
            )
        assert not (tmp_path / "destination").exists()

    def test_rejects_an_incomplete_copy(self, tmp_path, monkeypatch):
        import shutil

        source = tmp_path / "source"
        source.write_bytes(b"complete source")

        def short_copy(src, dst, length):
            dst.write(src.read(3))

        monkeypatch.setattr(shutil, "copyfileobj", short_copy)
        with pytest.raises(OSError, match="source changed"):
            publish_staged_file(
                source, tmp_path / "destination", strategy=PublicationStrategy.COPY
            )
