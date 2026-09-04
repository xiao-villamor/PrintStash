"""The four public entry points, and the guarantee they share.

`analyze_mesh`, `extract_geometry`, `render_thumbnail` and `to_stl_bytes` are
what the ingestion pipeline calls. Every one of them can be handed a file that
would kill the process to load, so every one of them enforces the caps — not
just `analyze_mesh`, which is the mistake this file exists to prevent. A cap
checked in one entry point and skipped in another is worse than no cap: it makes
the crash depend on which feature the user touched first.

The behaviour under a cap is deliberately not "fail". A skipped mesh is still
**indexed** — the file appears in the library, it just has no measured geometry
and no rendered thumbnail — and three cheaper substitutes are tried in order:

1. A 3MF's **embedded slicer preview**, read straight out of the zip without
   decompressing the mesh. This is preferred even when loading *would* be safe,
   because the slicer's own render is both free and more representative.
2. A **streaming fallback thumbnail** for an over-cap STL, which samples facets
   without ever building a mesh object. It reports `complete=False` so the UI
   can say the preview is partial rather than imply it is the whole model.
3. Nothing, honestly reported as `None`.

The other property here is the **post-load backstop**. Some formats cannot be
estimated (`_estimate_triangle_count` returns `None`), so they are loaded behind
the byte-size guard and re-checked once the real face count is known. The cheap
geometry is kept; only the expensive render is skipped. Reading this the other
way round — discarding the geometry too — is how a model ends up with no
dimensions for no reason.

Every "must not load" assertion below patches `_load_mesh` to raise. That is the
point: the test fails loudly if the guard ever stops guarding, rather than
quietly taking 700 MB longer.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import trimesh

from app.core.config import _overlay
from app.services import mesh_processing
from tests.fixtures.three_mf_projects import build_3d_builder_component_project

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
            mesh_processing, "_load_mesh", lambda _p: _fake_mesh(num_faces=500)
        )
        monkeypatch.setattr(
            mesh_processing.mesh_render,
            "render_mesh_thumbnail",
            lambda *a, **k: b"PNGDATA",
        )

        geometry, thumb = mesh_processing.analyze_mesh(p)

        assert geometry["triangle_count"] == 500
        assert thumb == b"PNGDATA"

    def test_analyze_mesh_reports_progress_labels(self, tmp_path: Path) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        labels: list[str] = []
        geometry, thumb = mesh_processing.analyze_mesh(p, report=labels.append)
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

        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
        monkeypatch.setattr(
            mesh_processing.mesh_render,
            "render_mesh_thumbnail",
            lambda *args, **kwargs: b"RENDERED-MESH",
        )

        _geometry, thumb = mesh_processing.analyze_mesh(p)
        assert thumb == png

    def test_reports_an_incomplete_fallback_thumbnail_as_incomplete(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from app.services import stl_fallback

        monkeypatch.setattr(stl_fallback, "_MAX_ASCII_BYTES", 500)
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
        monkeypatch.setattr(
            mesh_processing, "_estimate_triangle_count", lambda _p: None
        )
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: None)

        geometry, thumbnail = mesh_processing.analyze_mesh(path)

        # The fallback ran out of scan budget, so the thumbnail shows part of the
        # model. Passing `complete=False` up is what lets the UI say so instead
        # of presenting a partial render as the whole thing.
        assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
        assert thumbnail.complete is False

    def test_measures_no_geometry_from_a_file_it_could_not_load(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from app.services import stl_fallback

        monkeypatch.setattr(stl_fallback, "_MAX_ASCII_BYTES", 500)
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
        monkeypatch.setattr(
            mesh_processing, "_estimate_triangle_count", lambda _p: None
        )
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: None)

        geometry, _thumbnail = mesh_processing.analyze_mesh(path)

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

        monkeypatch.setattr(mesh_processing, "_load_mesh", _boom)

        geometry, thumb = mesh_processing.analyze_mesh(p)

        # Indexed, but with no geometry/thumbnail — and crucially, no load attempt.
        assert geometry["triangle_count"] is None
        assert thumb is None

    def test_over_cap_valid_stl_uses_streaming_thumbnail_fallback(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000)
        path = tmp_path / "issue-67-over-limit.stl"
        _write_renderable_binary_stl(path, 1_001)
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _path: (_ for _ in ()).throw(
                AssertionError("fallback must not load through trimesh")
            ),
        )

        geometry, thumb = mesh_processing.analyze_mesh(path)

        assert isinstance(thumb, mesh_processing.FallbackThumbnail)
        assert thumb.startswith(mesh_processing._PNG_MAGIC)
        assert thumb.complete is True
        assert geometry["triangle_count"] == 1_001
        assert geometry["bbox_x_mm"] == 99.8
        assert geometry["bbox_y_mm"] == 10.8

    def test_over_cap_3mf_still_gets_embedded_preview(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        png = _valid_preview_png()
        p = tmp_path / "dense.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr(
                "3D/3dmodel.model", b"<triangle/>" * 100_000
            )  # ~157k tris, over cap
            zf.writestr("Metadata/thumbnail.png", png)

        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )

        geometry, thumb = mesh_processing.analyze_mesh(p)

        assert geometry["triangle_count"] is None  # mesh skipped
        assert thumb == png

    def test_large_3mf_uses_embedded_preview_when_flag_on(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(
            _overlay, "mesh_max_render_triangles", 1000
        )  # 3MF is over cap
        monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", True)
        p, png = _over_cap_3mf_with_preview(tmp_path)
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("large 3MF must not load")),
        )

        geometry, thumb = mesh_processing.analyze_mesh(p)
        assert geometry["triangle_count"] is None  # never loaded
        assert thumb == png

    def test_large_3mf_skips_embedded_preview_when_flag_off(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", False)
        p, _png = _over_cap_3mf_with_preview(tmp_path)
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("large 3MF must not load")),
        )

        geometry, thumb = mesh_processing.analyze_mesh(p)
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
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(
                AssertionError("oversize file must not load")
            ),
        )

        geometry, thumb = mesh_processing.analyze_mesh(p)
        assert geometry["triangle_count"] is None
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
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(
                AssertionError("oversize 3MF must not load")
            ),
        )

        geometry, thumb = mesh_processing.analyze_mesh(p)
        assert geometry["triangle_count"] is None
        assert thumb == png

    def test_size_guard_disabled_when_zero(self, tmp_path: Path, monkeypatch) -> None:
        # mesh_max_load_mb = 0 turns the byte cap off; a big-but-sparse-triangle file
        # then loads normally (only the triangle cap still applies).
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
        p = tmp_path / "big.stl"
        _write_binary_stl(p, 42_000)

        monkeypatch.setattr(
            mesh_processing, "_load_mesh", lambda _p: _fake_mesh(42_000)
        )
        monkeypatch.setattr(
            mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNG"
        )

        geometry, thumb = mesh_processing.analyze_mesh(p)
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

        monkeypatch.setattr(
            mesh_processing, "_estimate_triangle_count", lambda _p: None
        )
        monkeypatch.setattr(
            mesh_processing, "_load_mesh", lambda _p: _fake_mesh(num_faces=99)
        )
        monkeypatch.setattr(
            mesh_processing.mesh_render,
            "render_mesh_thumbnail",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not render")),
        )

        geometry, thumb = mesh_processing.analyze_mesh(p)

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
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(500))
        monkeypatch.setattr(
            mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNG"
        )
        monkeypatch.setattr(
            mesh_processing,
            "_reclaim_memory",
            lambda: calls.__setitem__("n", calls["n"] + 1),
        )

        mesh_processing.analyze_mesh(p)
        assert calls["n"] == 1

    def test_skipped_mesh_does_not_reclaim(self, tmp_path: Path, monkeypatch) -> None:
        # No mesh was loaded (over cap), so there's nothing to free — and we don't pay
        # gc.collect()/malloc_trim for a file we never touched.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)
        p = tmp_path / "huge.stl"
        _write_binary_stl(p, 50_000)

        calls = {"n": 0}
        monkeypatch.setattr(
            mesh_processing,
            "_reclaim_memory",
            lambda: calls.__setitem__("n", calls["n"] + 1),
        )
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )

        mesh_processing.analyze_mesh(p)
        assert calls["n"] == 0


class TestExtractGeometry:
    def test_extract_geometry_loads_the_mesh_exactly_once(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        calls = {"n": 0}
        monkeypatch.setattr(
            mesh_processing,
            "_reclaim_memory",
            lambda: calls.__setitem__("n", calls["n"] + 1),
        )
        geometry = mesh_processing.extract_geometry(p)
        assert geometry["triangle_count"] is not None
        assert calls["n"] == 1

    def test_extract_geometry_respects_cap(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        p = tmp_path / "huge.stl"
        _write_binary_stl(p, 50_000)
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )
        assert mesh_processing.extract_geometry(p)["triangle_count"] is None

    def test_over_cap_ply_skips_load(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        p = tmp_path / "dense.ply"
        p.write_bytes(
            b"ply\nformat binary_little_endian 1.0\nelement face 999999\nend_header\n"
        )
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(
                AssertionError("over-cap PLY must not load")
            ),
        )

        geometry = mesh_processing.extract_geometry(p)
        assert geometry["triangle_count"] is None

    def test_ram_cap_skips_mesh_a_big_host_would_render(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Static ceiling is generous (5M), but a 2 GB host can't afford this mesh.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 5_000_000)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 2 * 1024**3)
        p = tmp_path / "mid.stl"
        # ~700k triangles: under the 5M static cap, but over the ~480k RAM cap @ 2 GB.
        _write_binary_stl(p, 700_000)
        assert mesh_processing._ram_triangle_cap(".stl") < 700_000

        def _boom(_path):  # pragma: no cover
            raise AssertionError("RAM-capped mesh must not load")

        monkeypatch.setattr(mesh_processing, "_load_mesh", _boom)
        assert mesh_processing.extract_geometry(p)["triangle_count"] is None

    def test_static_cap_still_applies_on_a_huge_ram_host(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A 256 GB host: the RAM cap is enormous, so the static ceiling is what binds.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 256 * 1024**3)
        p = tmp_path / "huge.stl"
        _write_binary_stl(p, 50_000)
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(
                AssertionError("over static cap must not load")
            ),
        )
        assert mesh_processing.extract_geometry(p)["triangle_count"] is None


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
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )

        assert mesh_processing.render_thumbnail(p) == png

    def test_render_thumbnail_real_mesh_renders_png(self, tmp_path: Path) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        thumb = mesh_processing.render_thumbnail(p)
        assert thumb is not None
        assert thumb.startswith(mesh_processing._PNG_MAGIC)

    def test_render_thumbnail_falls_back_to_embedded_when_render_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        png = _valid_preview_png((32, 160, 240))
        p = tmp_path / "model.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", b"<mesh/>")
            zf.writestr("Metadata/thumbnail.png", png)

        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
        monkeypatch.setattr(
            mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: None
        )
        assert mesh_processing.render_thumbnail(p) == png

    def test_render_thumbnail_is_none_when_nothing_can_be_rendered(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "cube.stl"
        _write_binary_stl(p, 10)
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
        monkeypatch.setattr(
            mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: None
        )
        assert mesh_processing.render_thumbnail(p) is None

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
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("over-cap must not load")),
        )
        assert mesh_processing.render_thumbnail(p) is None


class TestToStlBytes:
    def test_to_stl_bytes_refuses_over_cap_mesh(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A download-as-STL click on a monster 3MF/OBJ must not run an unbounded
        # trimesh.load_mesh (which would OOM the process for every user). Refuse cleanly.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        p = tmp_path / "dense.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # over cap
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )

        assert mesh_processing.to_stl_bytes(p) is None

    def test_to_stl_bytes_passes_through_raw_stl(self, tmp_path: Path) -> None:
        # An STL is returned byte-for-byte without any load, so the cap never applies
        # (no conversion, no memory blow-up) even for a large file.
        p = tmp_path / "raw.stl"
        _write_binary_stl(p, 10)
        assert mesh_processing.to_stl_bytes(p) == p.read_bytes()

    def test_to_stl_bytes_bakes_the_scene_transforms_into_the_geometry(
        self, tmp_path: Path
    ) -> None:
        """A 3MF places its parts with transforms; STL has nowhere to put them.

        3D Builder and most CAD exporters write one mesh and position it through
        a nested build/component graph, so the vertices in the file are at the
        origin and the placement lives in the matrices above them. Converting the
        mesh without flattening that graph produces an STL of a part sitting at
        0,0,0 — the preview and the download both show something that is not what
        the user modelled, with no error to explain it.
        """
        path = tmp_path / "3d-builder-component.3mf"
        path.write_bytes(build_3d_builder_component_project())

        converted = mesh_processing.to_stl_bytes(path)

        assert converted is not None and len(converted) > 84
        mesh = trimesh.load_mesh(io.BytesIO(converted), file_type="stl", process=False)
        np.testing.assert_allclose(
            mesh.bounds,
            np.asarray([[110.0, 220.0, 330.0], [112.0, 223.0, 334.0]]),
            atol=1e-5,
        )

    def test_to_stl_bytes_fails_closed_on_a_3mf_it_cannot_open(
        self, tmp_path: Path
    ) -> None:
        """A 3MF is a zip; something that is not one has to answer None.

        The route turns `None` into a 422 the user can read. Letting the zip error
        escape turns a corrupt upload — or a file renamed to `.3mf` — into a 500
        on a download link.
        """
        path = tmp_path / "malformed.3mf"
        path.write_bytes(b"not a zip archive")

        assert mesh_processing.to_stl_bytes(path) is None

    def test_to_stl_bytes_read_failure_returns_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "cube.stl"
        _write_binary_stl(p, 10)

        def fake_read_bytes(self):
            raise OSError("disk gone")

        monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
        assert mesh_processing.to_stl_bytes(p) is None

    def test_to_stl_bytes_converts_non_stl_mesh(self, tmp_path: Path) -> None:
        import trimesh

        p = tmp_path / "cube.obj"
        trimesh.creation.box(extents=[4, 4, 4]).export(p, file_type="obj")
        out = mesh_processing.to_stl_bytes(p)
        assert out is not None
        assert out[80:84] != b""

    def test_to_stl_bytes_returns_none_when_mesh_fails_to_load(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "garbage.foobar"
        p.write_bytes(b"not a mesh file \x00\x01")
        assert mesh_processing.to_stl_bytes(p) is None

    def test_to_stl_bytes_returns_none_on_export_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        # Force the "already an STL" fast-path to miss by faking a different suffix
        # so we exercise the load+export branch, then make export blow up.
        obj_path = tmp_path / "cube.obj"
        import trimesh

        trimesh.creation.box(extents=[4, 4, 4]).export(obj_path, file_type="obj")

        class _Boom:
            faces = np.zeros((1, 3))

            def export(self, *_a, **_k):
                raise RuntimeError("export boom")

        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _Boom())
        assert mesh_processing.to_stl_bytes(obj_path) is None


class TestExtractEmbedded3mfThumbnail:
    def test_extract_embedded_3mf_thumbnail_no_candidates_returns_none(
        self,
        tmp_path: Path,
    ) -> None:
        p = tmp_path / "no-preview.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", b"<mesh/>")
        assert mesh_processing.extract_embedded_3mf_thumbnail(p) is None

    def test_extract_embedded_3mf_thumbnail_survives_corrupt_archive(
        self,
        tmp_path: Path,
    ) -> None:
        p = tmp_path / "corrupt.3mf"
        p.write_bytes(b"not a zip archive")
        assert mesh_processing.extract_embedded_3mf_thumbnail(p) is None
