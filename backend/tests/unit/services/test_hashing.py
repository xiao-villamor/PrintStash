"""Unit tests for streaming sha256 helpers."""

from __future__ import annotations

import io
from pathlib import Path

from app.services.hashing import sha256_file, sha256_stream


class TestSha256File:
    def test_sha256_file_known_content(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        digest = sha256_file(f)
        assert (
            digest == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_sha256_file_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        digest = sha256_file(f)
        assert (
            digest == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_sha256_file_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * 100000)
        d1 = sha256_file(f)
        d2 = sha256_file(f)
        assert d1 == d2


class TestSha256Stream:
    def test_sha256_stream_known_content(self) -> None:
        fh = io.BytesIO(b"hello world")
        digest = sha256_stream(fh)
        assert (
            digest == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )
        assert fh.read() == b""  # consumed
