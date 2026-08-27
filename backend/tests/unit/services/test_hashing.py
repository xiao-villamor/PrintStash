"""Hashing consumes every byte without hiding filesystem or stream failures.

Artifact identity and upload de-duplication depend on these compatibility exports
remaining byte-for-byte equivalent to standard SHA-256.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from app.services.hashing import sha256_file, sha256_stream

_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_HELLO_SHA256 = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


class _FailingStream(io.BytesIO):
    def __init__(self, initial_bytes: bytes) -> None:
        super().__init__(initial_bytes)
        self._reads = 0

    def read(self, size: int = -1) -> bytes:
        self._reads += 1
        if self._reads == 2:
            raise OSError("stream failed")
        return super().read(size)


class TestSha256File:
    def test_hashes_file_bytes_with_sha256(self, tmp_path: Path) -> None:
        path = tmp_path / "test.bin"
        path.write_bytes(b"hello world")

        digest = sha256_file(path)

        assert digest == _HELLO_SHA256

    def test_hashes_an_empty_file_deterministically(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.bin"
        path.write_bytes(b"")

        digest = sha256_file(path)

        assert digest == _EMPTY_SHA256

    def test_hashes_input_larger_than_one_read_chunk(self, tmp_path: Path) -> None:
        content = b"x" * 1_048_577
        path = tmp_path / "large.bin"
        path.write_bytes(content)

        digest = sha256_file(path)

        assert digest == hashlib.sha256(content).hexdigest()

    def test_propagates_a_file_read_failure(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.bin"

        with pytest.raises(FileNotFoundError):
            sha256_file(missing)


class TestSha256Stream:
    def test_hashes_a_stream_with_sha256(self) -> None:
        stream = io.BytesIO(b"hello world")

        digest = sha256_stream(stream)

        assert digest == _HELLO_SHA256

    def test_consumes_the_stream(self) -> None:
        stream = io.BytesIO(b"hello world")

        sha256_stream(stream)

        assert stream.read() == b""

    def test_hashes_an_empty_stream_deterministically(self) -> None:
        stream = io.BytesIO()

        digest = sha256_stream(stream)

        assert digest == _EMPTY_SHA256

    def test_propagates_a_stream_read_failure(self) -> None:
        stream = _FailingStream(b"x" * 1_048_577)

        with pytest.raises(OSError, match="stream failed"):
            sha256_stream(stream)
