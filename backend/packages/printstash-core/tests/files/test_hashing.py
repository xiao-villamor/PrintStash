"""Cooperative hashing stops before consuming withdrawn source bytes."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from printstash_core.files import sha256_file, sha256_stream


class TestHashCancellation:
    @pytest.mark.parametrize(
        ("payload", "cancel_at", "consumed", "remaining"),
        [
            pytest.param(
                b"a" * (1024 * 1024 + 1), 2, 1024 * 1024, b"a", id="between-reads"
            ),
            pytest.param(b"complete", 3, len(b"complete"), b"", id="before-completion"),
        ],
    )
    def test_stops_hashing_at_cancellation_boundary(
        self, payload, cancel_at, consumed, remaining
    ):
        stream = BytesIO(payload)
        cancellation = KeyboardInterrupt("withdrawn hash")
        calls = 0

        def checkpoint():
            nonlocal calls
            calls += 1
            if calls == cancel_at:
                raise cancellation

        with pytest.raises(KeyboardInterrupt) as raised:
            sha256_stream(stream, on_chunk=checkpoint)

        assert raised.value is cancellation
        assert stream.tell() == consumed
        assert stream.read() == remaining
        assert not stream.closed

    @pytest.mark.parametrize(
        ("payload", "cancel_at", "consumed"),
        [
            pytest.param(b"a" * (1024 * 1024 + 1), 2, 1024 * 1024, id="between-reads"),
            pytest.param(b"complete", 3, len(b"complete"), id="before-completion"),
        ],
    )
    def test_closes_owned_file_after_hash_cancellation(
        self, tmp_path, monkeypatch, payload, cancel_at, consumed
    ):
        path = tmp_path / "source.stl"
        path.write_bytes(payload)
        open_file = Path.open
        opened = []
        positions = []
        cancellation = KeyboardInterrupt("withdrawn file hash")
        calls = 0

        def tracked_open(candidate, *args, **kwargs):
            stream = open_file(candidate, *args, **kwargs)
            opened.append(stream)
            return stream

        def checkpoint():
            nonlocal calls
            calls += 1
            positions.append(opened[0].tell())
            if calls == cancel_at:
                raise cancellation

        monkeypatch.setattr(Path, "open", tracked_open)

        with pytest.raises(KeyboardInterrupt) as raised:
            sha256_file(path, on_chunk=checkpoint)

        assert raised.value is cancellation
        assert len(opened) == 1
        assert opened[0].closed
        assert positions[-1] == consumed
        assert path.read_bytes() == payload

    def test_preserves_digest_with_a_cooperative_callback(self, tmp_path):
        payload = b"geometry bytes" * 100000
        path = tmp_path / "source.stl"
        path.write_bytes(payload)
        stream = BytesIO(payload)
        expected = hashlib.sha256(payload).hexdigest()

        file_digest = sha256_file(path, on_chunk=lambda: None)
        stream_digest = sha256_stream(stream, on_chunk=lambda: None)

        assert file_digest == stream_digest == expected
        assert path.read_bytes() == payload
        assert stream.read() == b""
        assert not stream.closed
