"""The engine bounds materialization before rendering and admits concurrent work under one shared policy. Embedded and streaming previews remain useful when a source cannot load."""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
from printstash_core.mesh.measurements import (
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
)

from app.core.config import _overlay
from app.modules.media import (
    mesh_loading,
    mesh_policy,
    mesh_previews,
    mesh_render,
)
from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryReady,
    PreviewCoverage,
    SourceScanState,
)
from tests.fixtures.mesh_analysis import analyze, is_partial_render

from .._meshes import (
    _fake_mesh,
    _over_cap_3mf_with_preview,
    _real_binary_stl_cube,
    _valid_preview_png,
    _write_binary_stl,
    _write_obj,
    _write_renderable_binary_stl,
)


class TestAnalyzeMesh:
    def test_under_cap_mesh_renders_normally(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
        p = tmp_path / "ok.stl"
        _write_binary_stl(p, 500)

        monkeypatch.setattr(
            mesh_loading, "load_mesh", lambda _p: _fake_mesh(num_faces=500)
        )
        monkeypatch.setattr(
            mesh_render,
            "render_mesh_thumbnail",
            lambda *a, **k: b"PNGDATA",
        )

        result = analyze(p)
        geometry, thumb = result.geometry, result.image

        assert geometry["triangle_count"] == 500
        assert thumb == b"PNGDATA"

    def test_analyze_mesh_reports_progress_labels(self, tmp_path: Path) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        labels: list[str] = []
        result = analyze(p, report=labels.append)
        geometry, thumb = result.geometry, result.image
        assert labels == ["loading_mesh", "extracting_geometry", "rendering_thumbnail"]
        assert geometry["triangle_count"] is not None
        assert thumb is not None

    def test_valid_embedded_3mf_preview_precedes_mesh_render(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A valid slicer preview is preferred even when mesh loading is safe."""
        png = _valid_preview_png((220, 40, 120))
        p = tmp_path / "preview-first.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", b"<mesh/>")
            zf.writestr("Metadata/thumbnail.png", png)

        monkeypatch.setattr(mesh_loading, "load_mesh", lambda _p: _fake_mesh(10))
        monkeypatch.setattr(
            mesh_render,
            "render_mesh_thumbnail",
            lambda *args, **kwargs: b"RENDERED-MESH",
        )

        result = analyze(p)
        _geometry, thumb = result.geometry, result.image
        assert thumb == png

    def test_refuses_thumbnail_when_source_validation_exceeds_budget(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from app.modules.media import stl_fallback
        from app.modules.media.stl_reader import STLReadLimits

        monkeypatch.setattr(
            stl_fallback, "STLReadLimits", lambda: STLReadLimits(max_source_bytes=500)
        )
        path = tmp_path / "ascii-truncated.stl"
        facet = (
            "facet normal 0 0 1\n"
            "outer loop\n"
            "vertex 0 0 0\n"
            "vertex 1 0 0\n"
            "vertex 0 1 0\n"
            "endloop\n"
            "endfacet\n"
        )
        path.write_text("solid truncated\n" + (facet * 20) + "endsolid truncated\n")
        monkeypatch.setattr(mesh_policy, "estimate_triangle_count", lambda _p: None)
        monkeypatch.setattr(mesh_loading, "load_mesh", lambda _p: None)

        result = analyze(path)

        # Source validation must finish before a retained preview is published.
        assert result.image is None
        assert result.coverage.preview is PreviewCoverage.NOT_PRODUCED
        assert result.coverage.source_scan is SourceScanState.NOT_SCANNED

    def test_measures_no_geometry_from_a_file_it_could_not_load(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from app.modules.media import stl_fallback
        from app.modules.media.stl_reader import STLReadLimits

        monkeypatch.setattr(
            stl_fallback, "STLReadLimits", lambda: STLReadLimits(max_source_bytes=500)
        )
        path = tmp_path / "ascii-truncated.stl"
        facet = (
            "facet normal 0 0 1\n"
            "outer loop\n"
            "vertex 0 0 0\n"
            "vertex 1 0 0\n"
            "vertex 0 1 0\n"
            "endloop\n"
            "endfacet\n"
        )
        path.write_text("solid truncated\n" + (facet * 20) + "endsolid truncated\n")
        monkeypatch.setattr(mesh_policy, "estimate_triangle_count", lambda _p: None)
        monkeypatch.setattr(mesh_loading, "load_mesh", lambda _p: None)

        result = analyze(path)
        geometry, _thumbnail = result.geometry, result.image

        # A thumbnail sampled from part of a file says nothing about the model's
        # real dimensions, so no measurement is reported rather than one derived
        # from the sample.
        assert geometry["triangle_count"] is None
        assert geometry["bbox_x_mm"] is None

    def test_over_cap_mesh_is_never_loaded(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        p = tmp_path / "huge.stl"
        _write_binary_stl(p, 50_000)  # well over the cap

        def _boom(_path):  # pragma: no cover - must never run
            raise AssertionError("over-cap mesh must not be loaded into trimesh")

        monkeypatch.setattr(mesh_loading, "load_mesh", _boom)

        assert mesh_policy.exceeds_cap(p)
        result = analyze(p)
        geometry, thumb = result.geometry, result.image

        # Exact source scanning preserves facts without admitting a full mesh.
        assert geometry["triangle_count"] == 50000
        assert (
            geometry["bbox_x_mm"]
            == geometry["bbox_y_mm"]
            == geometry["bbox_z_mm"]
            == 0.0
        )
        assert geometry["volume_mm3"] is None
        assert result.geometry_outcome == GeometryReady()
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
        )
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.coverage.preview is PreviewCoverage.NOT_PRODUCED
        assert thumb is None

    def test_over_cap_valid_stl_uses_streaming_thumbnail_fallback(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000)
        path = tmp_path / "issue-67-over-limit.stl"
        _write_renderable_binary_stl(path, 1_001)
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _path: (_ for _ in ()).throw(
                AssertionError("fallback must not load through trimesh")
            ),
        )

        result = analyze(path)
        geometry, thumb = result.geometry, result.image

        assert is_partial_render(result)
        assert thumb.startswith(mesh_previews._PNG_MAGIC)
        assert result.coverage.preview is PreviewCoverage.COMPLETE
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert geometry["triangle_count"] == 1_001
        # Bounds preserve the float32 coordinates encoded by the binary STL.
        assert geometry["bbox_x_mm"] == float(np.float32(99.8))
        assert geometry["bbox_y_mm"] == float(np.float32(10.8))

    def test_over_cap_3mf_still_gets_embedded_preview(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        png = _valid_preview_png()
        p = tmp_path / "dense.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)
            zf.writestr("Metadata/thumbnail.png", png)
        assert p.stat().st_size > 1024 * 1024

        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )

        result = analyze(p)
        geometry, thumb = result.geometry, result.image

        assert geometry["triangle_count"] is None  # mesh skipped
        assert thumb == png

    def test_large_3mf_uses_embedded_preview_when_flag_on(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", True)
        p, png = _over_cap_3mf_with_preview(tmp_path)
        assert p.stat().st_size > 1024 * 1024
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("large 3MF must not load")),
        )

        result = analyze(p)
        geometry, thumb = result.geometry, result.image
        assert geometry["triangle_count"] is None  # never loaded
        assert thumb == png

    def test_large_3mf_skips_embedded_preview_when_flag_off(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", False)
        p, _png = _over_cap_3mf_with_preview(tmp_path)
        assert p.stat().st_size > 1024 * 1024
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("large 3MF must not load")),
        )

        result = analyze(p)
        geometry, thumb = result.geometry, result.image
        assert geometry["triangle_count"] is None
        assert thumb is None

    def test_oversize_file_is_never_loaded(self, tmp_path: Path, monkeypatch) -> None:
        # Triangle cap is generous so it can't be what trips the guard; the file is
        # only ~2 MB of facets (well under it). The 1 MB *size* cap must still skip
        # the load — this is the path that protects against an estimator that comes
        # up empty on a huge file.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        p = tmp_path / "big.stl"
        _write_binary_stl(p, 42_000)  # ~2 MB on disk
        assert p.stat().st_size > 1024 * 1024

        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(
                AssertionError("oversize file must not load")
            ),
        )

        assert mesh_policy.exceeds_cap(p)
        result = analyze(p)
        geometry, thumb = result.geometry, result.image
        assert geometry["triangle_count"] == 42000
        assert (
            geometry["bbox_x_mm"]
            == geometry["bbox_y_mm"]
            == geometry["bbox_z_mm"]
            == 0.0
        )
        assert geometry["volume_mm3"] is None
        assert result.geometry_outcome == GeometryReady()
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
        )
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.coverage.preview is PreviewCoverage.NOT_PRODUCED
        assert thumb is None

    def test_oversize_3mf_still_gets_embedded_preview(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A 3MF over the byte cap is never decompressed into trimesh, but the cheap
        # embedded slicer preview (read straight from the zip) still stands in.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        png = _valid_preview_png()
        p = tmp_path / "big.3mf"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("3D/3dmodel.model", b"<triangle/>" * 200_000)  # ~2 MB stored
            zf.writestr("Metadata/thumbnail.png", png)
        assert p.stat().st_size > 1024 * 1024

        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(
                AssertionError("oversize 3MF must not load")
            ),
        )

        result = analyze(p)
        geometry, thumb = result.geometry, result.image
        assert geometry["triangle_count"] is None
        assert thumb == png

    def test_size_guard_disabled_when_zero(self, tmp_path: Path, monkeypatch) -> None:
        # mesh_max_load_mb = 0 turns the byte cap off; a big-but-sparse-triangle file
        # then loads normally (only the triangle cap still applies).
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
        p = tmp_path / "big.stl"
        _write_binary_stl(p, 42_000)

        monkeypatch.setattr(mesh_loading, "load_mesh", lambda _p: _fake_mesh(42_000))
        monkeypatch.setattr(
            mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNG"
        )

        result = analyze(p)
        geometry, thumb = result.geometry, result.image
        assert geometry["triangle_count"] == 42_000
        assert thumb == b"PNG"

    def test_post_load_backstop_skips_render_when_estimate_missed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A format the estimator can't size up (returns None) but whose loaded mesh
        # is over budget: keep the cheap geometry, skip the expensive render.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 10)
        p = tmp_path / "model.obj"
        p.write_text("# obj")

        monkeypatch.setattr(mesh_policy, "estimate_triangle_count", lambda _p: None)
        monkeypatch.setattr(
            mesh_loading, "load_mesh", lambda _p: _fake_mesh(num_faces=99)
        )
        monkeypatch.setattr(
            mesh_render,
            "render_mesh_thumbnail",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not render")),
        )

        result = analyze(p)
        geometry, thumb = result.geometry, result.image

        assert geometry["triangle_count"] == 99  # cheap geometry kept
        assert thumb is None

    def test_loaded_mesh_triggers_memory_reclaim(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
        p = tmp_path / "ok.stl"
        _write_binary_stl(p, 500)

        calls = {"n": 0}
        monkeypatch.setattr(mesh_loading, "load_mesh", lambda _p: _fake_mesh(500))
        monkeypatch.setattr(
            mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNG"
        )
        monkeypatch.setattr(
            mesh_policy,
            "reclaim_memory",
            lambda: calls.__setitem__("n", calls["n"] + 1),
        )

        analyze(p)
        assert calls["n"] == 1

    def test_skipped_mesh_does_not_reclaim(self, tmp_path: Path, monkeypatch) -> None:
        # No mesh was loaded (over cap), so there's nothing to free — and we don't pay
        # gc.collect()/malloc_trim for a file we never touched.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)
        p = tmp_path / "huge.stl"
        _write_binary_stl(p, 50_000)

        calls = {"n": 0}
        monkeypatch.setattr(
            mesh_policy,
            "reclaim_memory",
            lambda: calls.__setitem__("n", calls["n"] + 1),
        )
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )

        analyze(p)
        assert calls["n"] == 0


class TestRenderThumbnail:
    def test_render_thumbnail_falls_back_to_the_embedded_image_over_the_cap(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        png = _valid_preview_png()
        p = tmp_path / "dense.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # over cap
            zf.writestr("Metadata/thumbnail.png", png)
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )

        assert analyze(p, include_geometry=False, reason="repair").image == png

    def test_render_thumbnail_real_mesh_renders_png(self, tmp_path: Path) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        thumb = analyze(p, include_geometry=False, reason="repair").image
        assert thumb is not None
        assert thumb.startswith(mesh_previews._PNG_MAGIC)

    def test_render_thumbnail_falls_back_to_embedded_when_render_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        png = _valid_preview_png((32, 160, 240))
        p = tmp_path / "model.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", b"<mesh/>")
            zf.writestr("Metadata/thumbnail.png", png)

        monkeypatch.setattr(mesh_loading, "load_mesh", lambda _p: _fake_mesh(10))
        monkeypatch.setattr(mesh_render, "render_mesh_thumbnail", lambda *a, **k: None)
        assert analyze(p, include_geometry=False, reason="repair").image == png

    def test_render_thumbnail_is_none_when_nothing_can_be_rendered(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "cube.stl"
        _write_binary_stl(p, 10)
        monkeypatch.setattr(mesh_loading, "load_mesh", lambda _p: _fake_mesh(10))
        monkeypatch.setattr(mesh_render, "render_mesh_thumbnail", lambda *a, **k: None)
        assert analyze(p, include_geometry=False, reason="repair").image is None

    def test_render_thumbnail_over_cap_with_embedded_fallback_disabled_returns_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Over cap, and the large-file embedded-preview fallback explicitly off:
        # nothing to fall back to, so the function must return None outright.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", False)
        p = tmp_path / "dense.obj"
        _write_obj(p, tri_faces=5000)
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("over-cap must not load")),
        )
        assert analyze(p, include_geometry=False, reason="repair").image is None
