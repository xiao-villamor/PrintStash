"""Defend bounded strategy selection in the thumbnail orchestration seam.

These tests keep dense or invalid meshes from bypassing resource limits while
preserving the isolated renderer that fixed issue #67.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from printstash_core.mesh.measurements import (
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
)
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import (
    mesh_loading,
    mesh_measurements,
    mesh_policy,
    stl_fallback,
    stl_streaming,
)
from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryReady,
    MeshMeasurements,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailStrategy,
)
from app.modules.media.mesh_facts import FingerprintFailureCode, FingerprintResultState
from app.modules.media.stl_reader import InvalidSTL, STLBudgetExceeded, STLSourceChanged
from app.modules.media.thumbnail_engine import ThumbnailEngine
from tests.factories.content import binary_stl_facets


def _geometry() -> dict[str, float | int | None]:
    return {
        "bbox_x_mm": 10.0,
        "bbox_y_mm": 20.0,
        "bbox_z_mm": 30.0,
        "volume_mm3": None,
        "triangle_count": 12,
    }


def _measurements() -> MeshMeasurements:
    return MeshMeasurements(
        _geometry(),
        VolumeNotCalculated(VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED),
    )


class _Mesh:
    faces = list(range(12))


class TestThumbnailEngine:
    @pytest.mark.parametrize("cap", [99, 2_000_001, True, 100.0])
    def test_rejects_invalid_analysis_budgets(self, tmp_path, cap):
        with pytest.raises(ValueError, match="invalid_triangle_cap"):
            ThumbnailEngine().generate(
                ThumbnailRequest(tmp_path / "unused.stl", triangle_cap=cap)
            )

    @staticmethod
    def test_full_renderer_is_reported_as_the_selected_strategy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "part.stl"
        source.write_bytes(b"solid part\nendsolid part\n")
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *_a, **_k: False)
        monkeypatch.setattr(mesh_loading, "load_mesh", lambda *_a, **_k: _Mesh())
        monkeypatch.setattr(
            mesh_measurements, "geometry_from_mesh", lambda _mesh: _measurements()
        )
        monkeypatch.setattr(
            "app.modules.media.mesh_render.render_mesh_thumbnail",
            lambda *_a, **_k: b"png",
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image == b"png"
        assert result.strategy is ThumbnailStrategy.FULL
        assert result.failure_reason is None
        assert result.geometry == _geometry()

    @staticmethod
    def test_large_stl_uses_the_existing_isolated_streamer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "dense.stl"
        facet = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 2.0, 3.0))
        source.write_bytes(binary_stl_facets([facet] * 999))
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *_a, **_k: True)

        def no_load(*_args, **_kwargs):
            raise AssertionError("over-cap STL must not materialize through Trimesh")

        monkeypatch.setattr(mesh_loading, "load_mesh", no_load)
        streamed = stl_streaming.STLStreamingResult(
            png=b"streamed",
            bounds_min=(0.0, 0.0, 0.0),
            bounds_max=(1.0, 2.0, 3.0),
            triangle_count=999,
            parsed_triangles=999,
            scanned_bytes=source.stat().st_size,
            raster_candidates=1,
        )
        calls = []

        def isolated_preview(path, *, width, height, **_kwargs):
            calls.append(path)
            return streamed

        monkeypatch.setattr(
            stl_streaming, "render_stl_preview_isolated", isolated_preview
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert calls == [source]
        assert result.geometry_outcome == GeometryReady()
        assert result.geometry == {
            "bbox_x_mm": 1.0,
            "bbox_y_mm": 2.0,
            "bbox_z_mm": 3.0,
            "volume_mm3": None,
            "triangle_count": 999,
        }
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
        )
        assert result.image == b"streamed"
        assert result.strategy is ThumbnailStrategy.STREAMING
        assert result.coverage.preview is PreviewCoverage.COMPLETE
        assert result.geometry["triangle_count"] == 999
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)

    @staticmethod
    def test_missing_geometry_returns_a_typed_failure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "broken.obj"
        source.write_bytes(b"broken")
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *_a, **_k: False)
        monkeypatch.setattr(mesh_loading, "load_mesh", lambda *_a, **_k: None)

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image is None
        assert result.strategy is ThumbnailStrategy.NONE
        assert result.failure_reason is ThumbnailFailureReason.NO_GEOMETRY

    @staticmethod
    def test_post_load_memory_budget_is_enforced(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "oversized.obj"
        source.write_bytes(b"v 0 0 0\n")
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *_a, **_k: False)
        monkeypatch.setattr(mesh_loading, "load_mesh", lambda *_a, **_k: _Mesh())
        monkeypatch.setattr(
            mesh_measurements, "geometry_from_mesh", lambda _mesh: _measurements()
        )
        monkeypatch.setattr(mesh_policy, "ram_triangle_cap", lambda _suffix: 5)
        monkeypatch.setattr(
            "app.modules.media.mesh_render.render_mesh_thumbnail",
            lambda *_a, **_k: pytest.fail(
                "over-budget meshes must not reach the renderer"
            ),
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image is None
        assert result.strategy is ThumbnailStrategy.NONE
        assert result.failure_reason is ThumbnailFailureReason.RESOURCE_LIMIT


class TestPreviewCoverage:
    def test_bounded_fallback_cannot_certify_materialized_geometry(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "bounded.stl"
        source.write_bytes(b"source")
        fallback = stl_fallback.STLThumbnailResult(
            png=b"preview",
            bounds_min=(0.0, 0.0, 0.0),
            bounds_max=(10.0, 20.0, 30.0),
            triangle_count=12,
            sampled_triangles=4,
            source_complete=True,
            scanned_bytes=684,
            parsed_triangles=12,
            complete=False,
            raster_candidates=4,
        )
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *args, **kwargs: True)
        monkeypatch.setattr(
            stl_streaming, "render_stl_preview_isolated", lambda *args, **kwargs: None
        )
        monkeypatch.setattr(
            stl_fallback, "render_stl_thumbnail", lambda *args, **kwargs: fallback
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(source))

        assert result.image == b"preview"
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.coverage.preview is PreviewCoverage.PARTIAL


class TestSampledFingerprintRefusals:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (InvalidSTL(), FingerprintFailureCode.INVALID_SOURCE),
            (STLBudgetExceeded(), FingerprintFailureCode.RESOURCE_LIMIT),
            (STLSourceChanged(), FingerprintFailureCode.SOURCE_CHANGED),
            (OSError(), FingerprintFailureCode.SOURCE_UNAVAILABLE),
        ],
    )
    def test_preserves_the_shared_reader_refusal(
        self, tmp_path, monkeypatch, error, expected
    ):
        source = tmp_path / "sampled.stl"
        source.write_bytes(b"source")
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *a, **kw: True)

        def refused(*args, **kwargs):
            raise error

        monkeypatch.setattr(stl_fallback, "read_stl_sample", refused)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                source,
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )

        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert result.fingerprint_result.failure_code is expected
        assert result.fingerprint_result.records == ()
        assert result.image is None
        assert result.coverage.source_scan is SourceScanState.NOT_SCANNED
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.NOT_REQUESTED
        )


class TestSharedReaderLoadRefusals:
    @pytest.mark.parametrize(
        "cause",
        [
            FingerprintFailureCode.INVALID_SOURCE,
            FingerprintFailureCode.RESOURCE_LIMIT,
            FingerprintFailureCode.SOURCE_CHANGED,
            FingerprintFailureCode.SOURCE_UNAVAILABLE,
        ],
    )
    def test_preserves_the_shared_loader_refusal(self, tmp_path, monkeypatch, cause):
        source = tmp_path / "refused.stl"
        source.write_bytes(b"source")
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *a, **kw: False)

        def refused(*args, **kwargs):
            raise GeometryError(cause.value)

        monkeypatch.setattr(mesh_loading, "load_mesh", refused)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                source,
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )

        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert result.fingerprint_result.failure_code is cause
        assert result.fingerprint_result.records == ()
        assert result.image is None
        assert result.coverage.source_scan is SourceScanState.NOT_SCANNED
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.NOT_REQUESTED
        )
