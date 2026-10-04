"""STL consumers share exact source interpretation and explicit coverage."""

from pathlib import Path

import numpy as np
import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import mesh_loading, stl_fallback, stl_streaming
from app.modules.media.stl_reader import InvalidSTL, scan_stl
from tests.factories import content

FACETS = [
    ((0.0, 0.0, 0.0), (2.0, 0.0, 1.0), (0.0, 2.0, 1.0)),
    ((100.0, 3.0, 4.0), (101.0, 3.0, 4.0), (100.0, 4.0, 5.0)),
]


class TestSTLConsumers:
    @pytest.mark.parametrize("encoding", ["binary", "solid-binary", "ascii"])
    def test_parses_stl_variants_consistently(self, tmp_path: Path, encode_stl_facets):
        source = tmp_path / "mesh.stl"
        source.write_bytes(encode_stl_facets(FACETS))

        measured = scan_stl(source)
        sampled = stl_fallback.sample_stl_geometry(source, max_triangles=2)
        loaded = mesh_loading.load_mesh(source)
        streamed = stl_streaming.render_stl_preview_isolated(
            source, width=96, height=72
        )

        assert sampled is not None
        assert loaded is not None
        assert streamed is not None
        assert (
            measured.triangle_count
            == sampled.triangle_count
            == len(loaded.faces)
            == streamed.triangle_count
            == 2
        )
        for lower, upper in (
            (sampled.bounds_min, sampled.bounds_max),
            (loaded.bounds[0], loaded.bounds[1]),
            (streamed.bounds_min, streamed.bounds_max),
        ):
            np.testing.assert_array_equal(lower, measured.bounds_min)
            np.testing.assert_array_equal(upper, measured.bounds_max)

    @pytest.mark.parametrize(
        "normal",
        [(float("nan"), 0.0, 0.0), (float("inf"), 0.0, 0.0), (17.0, -23.0, 99.0)],
    )
    def test_binary_stored_normals_do_not_change_geometry_or_preview(
        self, tmp_path: Path, normal: tuple[float, float, float]
    ):
        import struct

        facets = [
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0)),
        ]
        source = tmp_path / "stored-normal.stl"
        body = bytearray(content.binary_stl_facets(facets))
        for offset in (84, 134):
            struct.pack_into("<3f", body, offset, 0.0, 0.0, 0.0)
        source.write_bytes(body)
        baseline = stl_streaming.render_stl_preview_isolated(
            source, width=96, height=72
        )
        assert baseline is not None
        altered = bytearray(body)
        for offset in (84, 134):
            struct.pack_into("<3f", altered, offset, *normal)
        source.write_bytes(altered)

        measured = scan_stl(source)
        sampled = stl_fallback.read_stl_sample(source, max_triangles=2)
        loaded = mesh_loading.load_mesh(source)
        streamed = stl_streaming.render_stl_preview_isolated(
            source, width=96, height=72
        )

        assert loaded is not None and streamed is not None
        assert sampled.source_complete and sampled.complete
        assert (
            measured.triangle_count == sampled.triangle_count == len(loaded.faces) == 2
        )
        np.testing.assert_array_equal(
            np.asarray(sampled.coordinates).reshape(-1, 3, 3), facets
        )
        np.testing.assert_array_equal(loaded.triangles, facets)
        assert streamed.png == baseline.png
        assert streamed.bounds_min == baseline.bounds_min
        assert streamed.bounds_max == baseline.bounds_max

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_nonfinite_binary_positions_are_refused(self, tmp_path: Path, value: float):
        import struct

        source = tmp_path / "bad-position.stl"
        body = bytearray(content.binary_stl(triangles=2))
        struct.pack_into("<f", body, 146, value)
        source.write_bytes(body)

        with pytest.raises(InvalidSTL):
            scan_stl(source)
        assert stl_fallback.sample_stl_geometry(source, max_triangles=1) is None
        with pytest.raises(GeometryError, match="invalid_source"):
            mesh_loading.load_mesh(source)
        assert (
            stl_streaming.render_stl_preview_isolated(source, width=96, height=72)
            is None
        )

    @pytest.mark.parametrize("normal", [b"nan", b"inf"])
    def test_ascii_stored_normals_remain_finite_syntax(
        self, tmp_path: Path, normal: bytes
    ):
        source = tmp_path / "bad-ascii-normal.stl"
        source.write_bytes(
            content.ascii_stl_facets(FACETS).replace(
                b"normal 0 0 1", b"normal " + normal + b" 0 1"
            )
        )

        with pytest.raises(InvalidSTL):
            scan_stl(source)
        assert stl_fallback.sample_stl_geometry(source, max_triangles=1) is None
        with pytest.raises(GeometryError, match="invalid_source"):
            mesh_loading.load_mesh(source)

    @pytest.mark.parametrize(
        "damage",
        [
            "truncated",
            "count-mismatch",
            "trailing",
            "unsampled-nonfinite",
            "incomplete-ascii",
            "malformed-ascii",
            "nonascii",
        ],
    )
    def test_rejects_malformed_stl_before_materialization(self, malformed_stl_source):
        with pytest.raises(GeometryError, match="invalid_source"):
            mesh_loading.load_mesh(malformed_stl_source)

    @pytest.mark.parametrize(
        "damage",
        [
            "truncated",
            "count-mismatch",
            "trailing",
            "unsampled-nonfinite",
            "incomplete-ascii",
            "malformed-ascii",
            "nonascii",
        ],
    )
    def test_declines_malformed_stl_in_optional_samples(self, malformed_stl_source):
        with pytest.raises(InvalidSTL):
            scan_stl(malformed_stl_source)
        assert (
            stl_fallback.sample_stl_geometry(malformed_stl_source, max_triangles=1)
            is None
        )

    @pytest.mark.parametrize("encoding", ["binary", "ascii"])
    def test_certifies_source_completion_for_partial_samples(
        self, tmp_path: Path, encode_stl_facets
    ):
        source = tmp_path / "partial.stl"
        source.write_bytes(encode_stl_facets(FACETS))

        sampled = stl_fallback.sample_stl_geometry(source, max_triangles=1)

        assert sampled is not None
        assert sampled.source_complete is True
        assert sampled.complete is False
        assert sampled.sampled_triangles == 1
        assert sampled.parsed_triangles == sampled.triangle_count == 2
        assert sampled.scanned_bytes == source.stat().st_size
        assert sampled.bounds_max == (101.0, 4.0, 5.0)

    def test_preserves_ascii_materialization_precision(self, tmp_path: Path):
        facets = [
            tuple(tuple(1e9 + value / 16 for value in vertex) for vertex in facet)
            for facet in FACETS
        ]
        source = tmp_path / "precise.stl"
        source.write_bytes(content.ascii_stl_facets(facets))

        sampled = stl_fallback.sample_stl_geometry(source, max_triangles=2)
        loaded = mesh_loading.load_mesh(source)

        assert sampled is not None
        assert loaded is not None
        expected = np.asarray(facets, dtype=np.float64)
        np.testing.assert_array_equal(
            np.asarray(sampled.coordinates).reshape(-1, 3, 3), expected
        )
        np.testing.assert_array_equal(loaded.triangles, expected)


class TestSTLBudgets:
    def test_accepts_stl_ascii_at_limit(self, tmp_path: Path):
        from app.modules.media.stl_reader import STLReadLimits

        body = content.ascii_stl_facets(FACETS)
        source = tmp_path / "exact-limit.stl"
        source.write_bytes(body)
        limits = STLReadLimits(
            max_triangles=2,
            max_source_bytes=len(body),
            max_lines=len(body.splitlines()),
            max_line_bytes=max(len(line) for line in body.splitlines(keepends=True)),
            chunk_triangles=1,
        )

        measured = scan_stl(source, limits=limits)
        sampled = stl_fallback.sample_stl_geometry(
            source, max_triangles=2, limits=limits
        )

        assert sampled is not None and sampled.source_complete and sampled.complete
        assert sampled.scanned_bytes == measured.scanned_bytes == len(body)
        assert sampled.triangle_count == measured.triangle_count == 2

    @pytest.mark.parametrize(
        "budget", ["max_triangles", "max_source_bytes", "max_lines", "max_line_bytes"]
    )
    def test_rejects_stl_ascii_above_limit(self, tmp_path: Path, budget: str):
        from app.modules.media.stl_reader import STLBudgetExceeded, STLReadLimits

        body = content.ascii_stl_facets(FACETS)
        source = tmp_path / "over-limit.stl"
        source.write_bytes(body)
        limits = {
            "max_triangles": 2,
            "max_source_bytes": len(body),
            "max_lines": len(body.splitlines()),
            "max_line_bytes": max(len(line) for line in body.splitlines(keepends=True)),
            "chunk_triangles": 1,
        }
        limits[budget] -= 1
        bounded = STLReadLimits(**limits)

        with pytest.raises(STLBudgetExceeded):
            scan_stl(source, limits=bounded)
        assert (
            stl_fallback.sample_stl_geometry(source, max_triangles=2, limits=bounded)
            is None
        )

    def test_reads_every_binary_source_byte_even_with_small_sample(
        self, tmp_path: Path, count_source_reads
    ):
        source = tmp_path / "read-cost.stl"
        source.write_bytes(content.binary_stl(triangles=120))
        reads = count_source_reads(source)

        sampled = stl_fallback.sample_stl_geometry(source, max_triangles=2)

        assert sampled is not None
        assert sampled.parsed_triangles == 120
        assert sampled.sampled_triangles == 2
        assert reads == [source.stat().st_size]

    @pytest.mark.parametrize("encoding", ["binary", "ascii"])
    def test_materializes_with_bounded_complete_read_passes(
        self,
        tmp_path: Path,
        count_source_reads,
        encode_stl_facets,
        expected_materialization_reads,
    ):
        import numpy as np

        from app.modules.media.stl_reader import STLReadLimits, materialize_stl

        body = encode_stl_facets(FACETS)
        source = tmp_path / "materialize-cost.stl"
        source.write_bytes(body)
        reads = count_source_reads(source)

        materialized = materialize_stl(source, limits=STLReadLimits(chunk_triangles=1))

        np.testing.assert_array_equal(materialized.triangles, FACETS)
        assert materialized.triangles.dtype == np.float64
        assert materialized.measurements.scanned_bytes == len(body)
        assert materialized.measurements.triangle_count == 2
        # Binary count admits exact allocation directly; ASCII scans before its
        # allocation. Each parser probes84 bytes before ASCII rewind/read.
        assert reads == expected_materialization_reads(len(body))

    @pytest.mark.parametrize("encoding", ["binary", "ascii"])
    def test_keeps_sample_independent_of_chunk(self, tmp_path: Path, encode_stl_count):
        from app.modules.media.stl_reader import STLReadLimits

        source = tmp_path / "chunk-sample.stl"
        source.write_bytes(encode_stl_count(triangles=120))

        small = stl_fallback.sample_stl_geometry(
            source, max_triangles=17, limits=STLReadLimits(chunk_triangles=1)
        )
        large = stl_fallback.sample_stl_geometry(
            source, max_triangles=17, limits=STLReadLimits(chunk_triangles=8192)
        )

        assert small is not None and large is not None
        assert small == large
        assert small.sampled_triangles == 17
        assert small.parsed_triangles == 120


class TestSTLSourceSnapshots:
    @pytest.mark.parametrize("encoding", ["binary", "ascii"])
    def test_holds_source_snapshot_across_passes(
        self, tmp_path: Path, encode_stl_facets
    ):
        import os

        from app.modules.media.stl_reader import (
            STLReadLimits,
            STLSourceChanged,
            iter_stl_blocks,
        )

        source = tmp_path / "snapshot.stl"
        source.write_bytes(encode_stl_facets(FACETS))
        measured = scan_stl(source)
        before = source.stat()
        replacement = tmp_path / "replacement.stl"
        replacement.write_bytes(source.read_bytes())
        replacement.replace(source)
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))

        assert source.stat().st_size == measured.scanned_bytes
        assert source.stat().st_mtime_ns == before.st_mtime_ns
        with pytest.raises(STLSourceChanged):
            list(iter_stl_blocks(source, STLReadLimits(), snapshot=measured.snapshot))


class TestSTLCompletionSnapshots:
    @pytest.mark.parametrize("consumer", ["sample", "materialize"])
    def test_refuses_change_after_validated_eof_before_completion(
        self, tmp_path: Path, canonical_stl_consumer, replace_source_after_eof
    ):
        from app.modules.media.stl_reader import (
            STLReadLimits,
            STLSourceChanged,
        )

        source = tmp_path / "completion-snapshot.stl"
        source.write_bytes(content.binary_stl_facets(FACETS))
        observed_stats = replace_source_after_eof(source)

        with pytest.raises(STLSourceChanged):
            canonical_stl_consumer(source, limits=STLReadLimits())
        assert len(observed_stats) >= 4


class TestSTLReadFailures:
    @pytest.mark.parametrize("encoding", ["binary", "ascii"])
    @pytest.mark.parametrize("consumer", ["scan", "sample", "materialize"])
    def test_preserves_scan_bytes_on_read_failure(
        self,
        tmp_path: Path,
        count_source_reads,
        encode_stl_count,
        canonical_stl_consumer,
    ):
        from app.modules.media.stl_reader import STLReadLimits

        source = tmp_path / "interrupted.stl"
        source.write_bytes(encode_stl_count(triangles=120))
        before = source.read_bytes()
        reads = count_source_reads(source, fail_after_bytes=134)
        limits = STLReadLimits(chunk_triangles=1)

        with pytest.raises(OSError, match="injected source read failure"):
            canonical_stl_consumer(source, limits=limits)

        assert sum(reads) >= 134
        assert source.stat().st_size == len(before)


class TestCanonicalSTLRefusals:
    @pytest.mark.parametrize("consumer", ["sample", "materialize"])
    @pytest.mark.parametrize("failure", ["invalid", "budget", "changed"])
    def test_retains_canonical_refusal_category(
        self, canonical_stl_consumer, canonical_stl_refusal
    ):
        from app.modules.media import stl_reader

        source, limits, expected = canonical_stl_refusal
        with pytest.raises(stl_reader.InvalidSTL) as caught:
            canonical_stl_consumer(source, limits=limits)
        assert caught.value.reason is expected


class TestSampleFingerprintRecipe:
    def test_preserves_measured_partial_fingerprint(self, tmp_path: Path):
        import hashlib
        import json

        import trimesh

        from app.modules.media.fingerprints import extract
        from app.modules.media.mesh_contracts import SourceScanState
        from app.modules.media.mesh_facts import FingerprintResultState, SampledGeometry
        from app.modules.media.thumbnail_engine import _prepare_sampled_stl
        from tests.paths import FIXTURES_DIR

        golden = json.loads(
            (FIXTURES_DIR / "media" / "geometry-v6-stl-sample.json").read_text()
        )
        source = tmp_path / "sampled-sphere.stl"
        source.write_bytes(
            trimesh.creation.icosphere(subdivisions=3, radius=10).export(
                file_type="stl"
            )
        )
        sampled = stl_fallback.read_stl_sample(source, max_triangles=100)
        preparation = _prepare_sampled_stl(source, triangle_cap=100)
        assert preparation is not None
        prepared, source_scan = preparation

        result = extract(prepared)

        assert sampled.source_complete is True
        assert sampled.complete is False
        assert sampled.triangle_count == golden["source_faces"] == 1280
        assert sampled.sampled_triangles == golden["sample_faces"] == 100
        assert source_scan is SourceScanState.COMPLETE
        assert isinstance(prepared.geometry, SampledGeometry)
        assert result.state is FingerprintResultState.PARTIAL
        from app.modules.media.fingerprints import ALGORITHM_VERSION

        # The v6 fixture freezes sample values; eligibility receipts evolve separately.
        assert golden["algorithm_version"] == "geometry-v6-sh5f4577c4"
        assert result.algorithm_version == ALGORITHM_VERSION
        assert (
            hashlib.sha256(source.read_bytes()).hexdigest() == golden["source_sha256"]
        )
        assert (
            hashlib.sha256(
                np.asarray(prepared.whole_mesh.vertices, dtype="<f8").tobytes()
            ).hexdigest()
            == golden["retained_sha256"]
        )
        values = result.records[0].values
        assert hashlib.sha256(values["d2_blob"]).hexdigest() == golden["d2_sha256"]
        np.testing.assert_allclose(
            values["surface_eigenvalue_ratios"],
            golden["surface_eigenvalue_ratios"],
            rtol=1e-10,
            atol=1e-12,
        )
        assert values["radius"] == pytest.approx(golden["radius"], rel=1e-10)
        assert values["eigen_ratio_0"] == pytest.approx(
            golden["eigen_ratio_0"], rel=1e-10
        )
