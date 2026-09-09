"""Transfer policy bounds memory, validates bytes before publication and cooperates with pause."""

import hashlib
import io

import pytest

from app.modules.storage.migration_transfer import (
    ChunkReader,
    ThrottledReader,
    validate_destination_limits,
)


class TestValidateDestinationLimits:
    @pytest.mark.parametrize(
        "transport,key,size,code",
        [
            pytest.param(
                "local",
                "x" * 2049,
                1,
                "migration_destination_key_too_long",
                id="local-key",
            ),
            pytest.param(
                "s3",
                "ñ" * 513,
                1,
                "migration_destination_limits_exceeded",
                id="s3-utf8-key",
            ),
            pytest.param(
                "s3",
                "object",
                8 * 1024**2 * 10_000 + 1,
                "migration_destination_limits_exceeded",
                id="s3-multipart-size",
            ),
        ],
    )
    def test_refuses_provider_transfer_limits(self, transport, key, size, code):
        with pytest.raises(ValueError, match=code):
            validate_destination_limits(transport, key, size)

    @pytest.mark.parametrize(
        "transport,key,size",
        [
            pytest.param("local", "x" * 2048, 5 * 1024**4, id="local"),
            pytest.param("s3", "ñ" * 512, 8 * 1024**2 * 10_000, id="s3"),
        ],
    )
    def test_accepts_exact_provider_transfer_limits(self, transport, key, size):
        assert validate_destination_limits(transport, key, size) is None


class TestThrottledReader:
    def test_unbounded_consumer_read_remains_bounded(self):
        reader = ThrottledReader(io.BytesIO(b"x" * (2 * 1024 * 1024)), None)
        assert len(reader.read()) == 1024 * 1024
        assert len(reader.read(8 * 1024 * 1024)) == 1024 * 1024
        assert reader.read() == b""

    @pytest.mark.parametrize(
        "payload,size,digest,code",
        [
            (b"short", 10, None, "size_mismatch"),
            (b"too long", 1, None, "size_mismatch"),
            (b"changed", 7, "0" * 64, "hash_mismatch"),
        ],
    )
    def test_changed_source_fails_before_end_of_transfer(
        self, payload, size, digest, code
    ):
        reader = ThrottledReader(
            io.BytesIO(payload), None, expected_size=size, expected_sha256=digest
        )
        with pytest.raises(ValueError, match=code):
            while reader.read():
                pass

    def test_low_bandwidth_never_sleeps_for_more_than_one_quarter_second(
        self, monkeypatch
    ):
        now = [0.0]
        delays = []

        def sleep(delay):
            delays.append(delay)
            now[0] += delay

        monkeypatch.setattr(
            "app.modules.storage.migration_transfer.time.monotonic", lambda: now[0]
        )
        monkeypatch.setattr("app.modules.storage.migration_transfer.time.sleep", sleep)
        reader = ThrottledReader(io.BytesIO(b"x" * 1024), 1024)
        while reader.read():
            pass
        assert sum(delays) == pytest.approx(1.0)
        assert max(delays) <= 0.25

    def test_pause_checkpoint_stops_before_consuming_more_source_bytes(self):
        source = io.BytesIO(b"untouched")

        def pause():
            raise ValueError("migration_copy_paused")

        reader = ThrottledReader(source, None, checkpoint=pause)
        with pytest.raises(ValueError, match="migration_copy_paused"):
            reader.read()
        assert source.tell() == 0


class TestChunkReader:
    def test_chunk_adapter_preserves_every_byte(self):
        payload = b"streamed-object"
        with ChunkReader(iter([b"streamed", b"", b"-object"])) as source:
            reader = ThrottledReader(
                source,
                None,
                expected_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            output = bytearray()
            buffer = bytearray(3)
            while count := reader.readinto(buffer):
                output.extend(buffer[:count])
            assert bytes(output) == payload
            assert reader.transferred == len(payload)
