"""Cosine retrieval is bounded, deterministic and native-dimensional across restarts."""

import struct

import pytest

from printstash_core.inference import EmbeddingError
from printstash_core.inference.vectors import VectorEntry, cosine_neighbors, normalize


class TestNormalize:
    def test_encodes_little_endian_float32(self):
        assert normalize([3, 4], 2) == struct.pack("<ff", 0.6, 0.8)

    @pytest.mark.parametrize(
        "values,dimension,code",
        [
            ([1], 0, "dimension_invalid"),
            ([0, 0], 2, "vector_invalid"),
            ([float("nan"), 1], 2, "vector_invalid"),
            ([1], 2, "vector_invalid"),
            ([1, 2, 3], 2, "vector_invalid"),
            ([1e308, 1e308], 2, "vector_invalid"),
        ],
    )
    def test_rejects_invalid_vector(self, values, dimension, code):
        with pytest.raises(EmbeddingError, match=code):
            normalize(values, dimension)


class TestCosineNeighbors:
    def test_orders_by_score_then_subject(self):
        result = cosine_neighbors(
            normalize([1, 0], 2),
            [
                VectorEntry(1, 2, normalize([1, 0], 2)),
                VectorEntry(2, 1, normalize([1, 0], 2)),
                VectorEntry(3, 3, normalize([0, 1], 2)),
            ],
            dimension=2,
        )
        assert [item.subject_id for item in result.items] == [1, 2, 3]
        assert result.scanned == 3
        assert result.truncated is False

    def test_keeps_best_unit_per_subject(self):
        result = cosine_neighbors(
            normalize([1, 0], 2),
            [
                VectorEntry(1, 1, normalize([0, 1], 2)),
                VectorEntry(2, 1, normalize([1, 0], 2)),
                VectorEntry(3, 2, normalize([0, 1], 2)),
            ],
            dimension=2,
            limit=1,
            block_size=1,
        )
        assert [
            (item.subject_id, item.unit_id, item.score) for item in result.items
        ] == [(1, 2, 1)]

    def test_keeps_deterministic_unit_on_tie(self):
        result = cosine_neighbors(
            normalize([1], 1),
            [
                VectorEntry(3, 1, normalize([1], 1)),
                VectorEntry(1, 1, normalize([1], 1)),
                VectorEntry(2, 1, normalize([1], 1)),
            ],
            dimension=1,
        )
        assert result.items[0].unit_id == 1

    def test_reports_budget_truncation(self):
        result = cosine_neighbors(
            normalize([1], 1),
            (VectorEntry(i, i, normalize([1], 1)) for i in range(10)),
            dimension=1,
            max_scan=2,
        )
        assert result.scanned == 2
        assert result.truncated is True

    def test_accepts_empty_generation(self):
        result = cosine_neighbors(normalize([1], 1), (), dimension=1)
        assert result.items == ()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"dimension": 0},
            {"dimension": 1, "limit": 0},
            {"dimension": 1, "max_scan": 0},
            {"dimension": 1, "block_size": 513},
        ],
    )
    def test_rejects_unbounded_query(self, kwargs):
        with pytest.raises(EmbeddingError, match="budget_invalid"):
            cosine_neighbors(b"", (), **kwargs)

    def test_rejects_wrong_query_dimension(self):
        with pytest.raises(EmbeddingError, match="dimension_mismatch"):
            cosine_neighbors(b"", (), dimension=1)

    @pytest.mark.parametrize(
        "blob,code",
        [
            (b"", "dimension_mismatch"),
            (struct.pack("<f", float("nan")), "vector_invalid"),
            (struct.pack("<f", 0), "vector_invalid"),
        ],
    )
    def test_rejects_corrupt_stored_vector(self, blob, code):
        with pytest.raises(EmbeddingError, match=code):
            cosine_neighbors(normalize([1], 1), (VectorEntry(1, 1, blob),), dimension=1)
