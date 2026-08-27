"""Defends ``test_sha256_helpers_stream_known_content`` behavior for the ``files`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from printstash_core.files import (
    UnsafeStorageComponent,
    UploadTooLarge,
    ensure_unique_slug,
    sha256_file,
    sha256_stream,
    slugify,
    stream_to_path,
    validate_leaf_name,
)

HELLO_SHA256 = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_sha256_helpers_stream_known_content(tmp_path: Path) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"hello world")
    stream = BytesIO(b"hello world")

    assert sha256_file(path) == HELLO_SHA256
    assert sha256_stream(stream) == HELLO_SHA256
    assert stream.read() == b""


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
def test_validate_leaf_name_rejects_portably_unsafe_names(
    unsafe_name: str,
) -> None:
    with pytest.raises(UnsafeStorageComponent, match="^unsafe_storage_component$"):
        validate_leaf_name(unsafe_name)


def test_validate_leaf_name_normalizes_nfc_and_enforces_utf8_byte_limit() -> None:
    assert validate_leaf_name("cafe\N{COMBINING ACUTE ACCENT}.stl") == "café.stl"
    assert validate_leaf_name("é" * 127 + "a") == "é" * 127 + "a"

    with pytest.raises(UnsafeStorageComponent):
        validate_leaf_name("é" * 128)


def test_slug_helpers_preserve_first_available_name() -> None:
    assert slugify("  Café Racer — Bracket v2!! ") == "cafe-racer-bracket-v2"
    assert slugify("🎉") == "model"

    existing = {"gear", "gear-2", "gear-3"}
    assert ensure_unique_slug("gear", existing.__contains__) == "gear-4"


def test_stream_to_path_publishes_one_pass_with_digest(tmp_path: Path) -> None:
    payload = b"one-pass-staging" * 100_000
    source = BytesIO(payload)
    digest = hashlib.sha256()
    destination = tmp_path / "nested" / "artifact.bin"

    written = stream_to_path(source, destination, digest=digest)

    assert written == len(payload)
    assert destination.read_bytes() == payload
    assert digest.hexdigest() == hashlib.sha256(payload).hexdigest()
    assert list(destination.parent.glob(".printstash-stage-*")) == []


def test_stream_to_path_never_replaces_a_collision(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.bin"
    destination.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        stream_to_path(BytesIO(b"replacement"), destination)

    assert destination.read_bytes() == b"existing"
    assert list(tmp_path.glob(".printstash-stage-*")) == []


def test_stream_to_path_does_not_follow_an_existing_leaf_symlink(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"existing")
    destination = tmp_path / "artifact.bin"
    destination.symlink_to(target)

    with pytest.raises(FileExistsError):
        stream_to_path(BytesIO(b"replacement"), destination)

    assert destination.is_symlink()
    assert target.read_bytes() == b"existing"


def test_stream_to_path_cleans_up_after_limit_or_read_failure(tmp_path: Path) -> None:
    too_large = tmp_path / "too-large.bin"
    with pytest.raises(UploadTooLarge):
        stream_to_path(BytesIO(b"1234"), too_large, max_bytes=3)
    assert not too_large.exists()

    class FailingStream(BytesIO):
        calls = 0

        def read(self, size: int = -1) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("stream failed")

    failed = tmp_path / "failed.bin"
    with pytest.raises(OSError, match="stream failed"):
        stream_to_path(FailingStream(), failed)
    assert not failed.exists()
    assert list(tmp_path.glob(".printstash-stage-*")) == []
