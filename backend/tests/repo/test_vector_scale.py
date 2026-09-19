"""The scale harness compares real exact indexes and extension-free durable floats."""

import sqlite3

import pytest

from tests.fakes.vector_scale import digest, fallback, measure


@pytest.fixture
def measurement(tmp_path):
    directory = tmp_path / "measured"
    return directory, measure(directory, count=256, dimension=16, queries=2)


class TestVectorScale:
    def test_compares_identical_authorized_neighbors(self, measurement):
        _, result = measurement

        assert result["count"] == 256
        assert result["dimension"] == 16
        assert result["durable_float_payload_bytes"] == 256 * 16 * 4
        for divisor in (1, 10):
            scope = result["observations"][str(divisor)]
            assert scope["eligible_vectors"] == 256 // divisor
            assert scope["mean_recall_at_10"] == 1
            assert len(scope["native_seconds"]) == len(scope["fallback_seconds"]) == 2
            assert scope["native_p95_seconds"] > 0
        assert result["source_file_bytes"] > result["durable_float_payload_bytes"]

    def test_recovers_floats_without_a_vector_extension(self, measurement):
        directory, result = measurement
        with sqlite3.connect(directory / "restored.sqlite") as restored:
            assert restored.execute("SELECT count(*) FROM vectors").fetchone()[0] == 256
            assert digest(restored) == result["durable_sha256"]
            with pytest.raises(sqlite3.OperationalError, match="no such function"):
                restored.execute("SELECT vec_version()")
            query = restored.execute(
                "SELECT vector FROM vectors WHERE id=1"
            ).fetchone()[0]
            assert fallback(restored, query, dimension=16, count=256, divisor=1)[0] == 1
