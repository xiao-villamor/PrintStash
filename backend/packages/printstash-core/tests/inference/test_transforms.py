"""Compressed representations preserve immutable native vectors and declared numeric semantics."""

import json
import struct

import pytest

from printstash_core.inference import EmbeddingError
from printstash_core.inference.transforms import IndexTransform, shortlist_codes


class TestIndexTransform:
    @pytest.mark.parametrize(
        "metadata", ["[" * 1500 + "]" * 1500, " " * 4097 + "{}"], ids=["depth", "size"]
    )
    def test_bounds_transform_metadata(self, metadata):
        with pytest.raises(EmbeddingError, match="embedding_transform_invalid"):
            IndexTransform.restore(
                metadata, native_dimension=2, index_dimension=2, quantization="float32"
            )

    def test_rejects_overflowing_code_norms(self):
        transform = IndexTransform(2, 2)
        with pytest.raises(EmbeddingError, match="embedding_code_invalid"):
            transform.distances(
                struct.pack("<2f", 1, 0), (struct.pack("<2f", 3e38, 3e38),)
            )

    def test_rejects_unapproved_truncation(self):
        with pytest.raises(EmbeddingError, match="embedding_mrl_unavailable"):
            IndexTransform.approved(1024, 128, "float32")

    def test_normalizes_an_approved_prefix(self):
        native = struct.pack("<4f", 3, 4, 5, 6)
        transform = IndexTransform.approved(4, 2, "float32", mrl_dimensions=(2,))

        code = transform.encode(native)

        assert struct.unpack("<2f", code) == pytest.approx((0.6, 0.8))
        assert struct.unpack("<4f", native) == (3, 4, 5, 6)

    def test_roundtrips_a_versioned_int8_recipe(self):
        transform = IndexTransform.approved(2, 2, "int8")
        native = struct.pack("<2f", 3, 4)

        restored = IndexTransform.restore(
            transform.metadata(),
            native_dimension=2,
            index_dimension=2,
            quantization="int8",
        )

        assert restored == transform
        assert restored.encode(native) == bytes((76, 102))
        assert restored.code_bytes == 2

    def test_packs_binary_signs_in_declared_order(self):
        transform = IndexTransform.approved(8, 8, "binary")
        native = struct.pack("<8f", -1, -1, 1, -1, 1, 0, 1, 1)

        code = transform.encode(native)

        assert code == b"\xd4"
        assert transform.distances(code, (b"\xc4", b"\xd4", b"\x00")).tolist() == [
            1,
            0,
            4,
        ]

    @pytest.mark.parametrize(
        "native",
        [
            b"bad",
            struct.pack("<4f", float("nan"), 1, 1, 1),
            struct.pack("<4f", 0, 0, 1, 1),
        ],
        ids=["length", "nonfinite", "zero-prefix"],
    )
    def test_rejects_corrupt_native_vectors(self, native):
        transform = IndexTransform.approved(4, 2, "float32", mrl_dimensions=(2,))

        with pytest.raises(EmbeddingError):
            transform.encode(native)

    @pytest.mark.parametrize(
        "changed",
        [
            {"index_dimension": 1},
            {"quantization": "binary"},
            {"version": "unknown"},
            {"int8_scale": 2},
            {"bit_order": "big"},
        ],
        ids=["dimension", "quantization", "version", "scale", "bit-order"],
    )
    def test_rejects_incompatible_transform_metadata(self, changed):
        metadata = json.loads(IndexTransform(2, 2, "int8").metadata()) | changed

        with pytest.raises(EmbeddingError, match="embedding_transform_invalid"):
            IndexTransform.restore(
                json.dumps(metadata),
                native_dimension=2,
                index_dimension=2,
                quantization="int8",
            )

    def test_retains_legacy_float_generation_compatibility(self):
        assert IndexTransform.restore(
            "{}", native_dimension=3, index_dimension=3, quantization="float32"
        ) == IndexTransform(3, 3)


class TestShortlistCodes:
    def test_bounds_compressed_shortlists(self):
        transform = IndexTransform(8, 8, "binary")
        entries = ((1, b"\x07"), (2, b"\x01"), (3, b"\x00"))

        result = shortlist_codes(transform, b"\x00", iter(entries), limit=1, max_scan=2)

        assert result.ids == (2,)
        assert result.scanned == 2
        assert result.truncated is True

    @pytest.mark.parametrize(
        "quantization", ["float32", "int8", "binary"], ids=["float", "int8", "binary"]
    )
    def test_ranks_compatible_codes(self, quantization):
        transform = IndexTransform(2, 2, quantization)
        first = transform.encode(struct.pack("<2f", 1, 0))
        second = transform.encode(struct.pack("<2f", 0, 1))

        result = shortlist_codes(
            transform, first, [(2, second), (1, first)], limit=2, max_scan=2
        )

        assert result.ids == (1, 2)
        assert result.truncated is False

    @pytest.mark.parametrize(
        "limit,max_scan", [(0, 10), (2049, 10), (1, 0), (1, 1000001)]
    )
    def test_rejects_invalid_shortlist_budgets(self, limit, max_scan):
        with pytest.raises(EmbeddingError, match="embedding_query_budget_invalid"):
            shortlist_codes(
                IndexTransform(8, 8, "binary"),
                b"\x00",
                [],
                limit=limit,
                max_scan=max_scan,
            )

    @pytest.mark.parametrize(
        "entries,expected",
        [([], ()), ([(1, b"\x00"), (2, b"\x01"), (3, b"\x07")], (1,))],
    )
    def test_finishes_exhausted_candidate_streams(self, entries, expected):
        result = shortlist_codes(
            IndexTransform(8, 8, "binary"), b"\x00", entries, limit=1, max_scan=10
        )
        assert result.ids == expected
        assert result.scanned == len(entries)
        assert result.truncated is False


class TestCodeBoundaries:
    def test_reports_float_code_width(self):
        transform = IndexTransform(4, 4, "float32")
        assert transform.code_bytes == 16
        assert transform.storage_dimension == 4

    @pytest.mark.parametrize(
        "count,query_bytes,code_bytes", [(0, 8, 8), (513, 8, 8), (1, 7, 8), (1, 8, 7)]
    )
    def test_rejects_invalid_distance_blocks(self, count, query_bytes, code_bytes):
        with pytest.raises(EmbeddingError, match="embedding_code_invalid"):
            IndexTransform(2, 2).distances(
                b"x" * query_bytes, (b"x" * code_bytes,) * count
            )
