"""Cosine retrieval is bounded, deterministic and native-dimensional across restarts."""

import math
import random
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
    def test_keeps_subject_types_distinct(self):
        blob = normalize([1, 0], 2)
        result = cosine_neighbors(
            blob,
            [VectorEntry(1, 1, blob, "model"), VectorEntry(2, 1, blob, "document")],
            dimension=2,
        )
        assert [(item.subject_type, item.subject_id) for item in result.items] == [
            ("document", 1),
            ("model", 1),
        ]

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


class TestCompetitiveRanking:
    @pytest.mark.parametrize("limit", [1, 20, 100])
    @pytest.mark.parametrize("block_size", [1, 7, 256])
    def test_preserves_exact_subject_ranking(self, limit, block_size):
        rng = random.Random(166)
        entries = [
            VectorEntry(
                index + 1,
                index % 137,
                normalize([rng.uniform(-1, 1) for _ in range(8)], 8),
                "model" if index % 2 else "document",
            )
            for index in range(2048)
        ]
        # Identical vectors exercise late unit ties without equal-score estimates.
        entries += [
            VectorEntry(4096 + i, row.subject_id, row.blob, row.subject_type)
            for i, row in enumerate(entries[:256])
        ]
        rng.shuffle(entries)
        query = normalize([1, 0, 0, 0, 0, 0, 0, 0], 8)
        oracle = {}
        for row in entries:
            values = struct.unpack("<8f", row.blob)
            score = values[0] / math.sqrt(math.fsum(value * value for value in values))
            order = (-score, row.subject_type, row.subject_id, row.unit_id)
            identity = (row.subject_type, row.subject_id)
            if identity not in oracle or order < oracle[identity]:
                oracle[identity] = order
        expected = sorted(oracle.values())[:limit]
        result = cosine_neighbors(
            query, iter(entries), dimension=8, limit=limit, block_size=block_size
        )
        assert [
            (item.subject_type, item.subject_id, item.unit_id) for item in result.items
        ] == [row[1:] for row in expected]
        assert [item.score for item in result.items] == pytest.approx(
            [-row[0] for row in expected], abs=1e-6
        )
        assert result.scanned == len(entries)
        assert not result.truncated

    @pytest.mark.parametrize(
        "blob,code",
        [
            (b"", "dimension_mismatch"),
            (struct.pack("<ff", float("nan"), 0), "vector_invalid"),
            (struct.pack("<ff", 0, 0), "vector_invalid"),
        ],
    )
    def test_rejects_corruption_beyond_a_full_cutoff(self, blob, code):
        query = normalize([1, 0], 2)
        rows = [VectorEntry(1, 1, query), VectorEntry(2, 2, blob)]
        with pytest.raises(EmbeddingError, match=code):
            cosine_neighbors(query, rows, dimension=2, limit=1, block_size=1)
