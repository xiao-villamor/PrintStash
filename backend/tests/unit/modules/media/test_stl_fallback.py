"""Drawing a recognisable thumbnail of a mesh far too big to load.

When full mesh loading refuses a mesh — a 40-million-triangle lattice, a gyroid,
a scan — the alternative to "no thumbnail" is this: stream the file, sample a
bounded number of facets, and rasterise those. It never builds a mesh object, so
its memory cost is a function of the sample size rather than the file size, and
it is the only reason an over-cap model shows anything at all.

What makes it hard is that a *wrong* thumbnail is worse than none. Three
properties carry that, and each has a failure mode that still produces a
plausible-looking image:

**A hole must stay a hole.** A ring sampled badly renders as a filled disc, and a
filled disc is a perfectly convincing thumbnail of a different object. The
annular tests assert the centre pixel stays transparent — that is the single most
sensitive assertion in this file.

**A surface must stay connected.** Facets on a densely tessellated surface each
project to less than a pixel, so naive sampling paints scattered dots. The
microfaceted tests assert both that enough of the frame is covered *and* that the
covered pixels form one component, because coverage alone is satisfied by noise.

**Partial is reported as partial.** Retained facets and candidate pixels are
bounded; exhausting those budgets produces `complete=False`. Source validation
also has finite byte, facet and line budgets, but exhausting those refuses the
source instead of accepting a partially validated preview.

The input is untrusted and often malformed: ASCII STLs from hand-written
exporters, a facet with a 10 MB "comment" line, vertices at `1e308`, a truncated
binary record. None of it may raise, and none of it may be silently accepted as
geometry — an overflow that reaches the rasteriser paints garbage across the
frame.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.modules.media import stl_fallback
from app.modules.media.stl_reader import STLReadLimits

from ._meshes import (
    _largest_component_fraction,
    _write_annular_binary_stl,
    _write_large_projected_binary_stl,
    _write_microfaceted_annular_stl,
    _write_microfaceted_surface_stl,
    _write_renderable_binary_stl,
)


def _sampled_stl(
    *,
    coordinates: list[float] | None = None,
    bounds_min: tuple[float, float, float] = (0.0, 0.0, 0.0),
    bounds_max: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> "stl_fallback.STLSample":
    """A complete one-facet sample, so a test can vary the one field it is about."""
    from array import array

    return stl_fallback.STLSample(
        coordinates=array("d", coordinates if coordinates is not None else [0.0] * 9),
        triangle_count=1,
        sampled_triangles=1,
        bounds_min=bounds_min,
        bounds_max=bounds_max,
        scanned_bytes=1,
        parsed_triangles=1,
        complete=True,
        source_complete=True,
    )


def _write_hostile_ascii(path: Path) -> Path:
    """An ASCII STL whose one good facet is preceded by everything that can go wrong.

    An unparseable vertex, a NaN vertex, and an oversized line precede valid
    geometry. The canonical reader must refuse the source rather than skip the
    malformed content and certify the later facet.
    """
    valid = b"vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
    path.write_bytes(
        b"solid iterator\n"
        + b"vertex not-a-number 0 0\n"
        + b"vertex nan 0 0\n"
        + b"comment "
        + b"x" * (STLReadLimits().max_line_bytes + 10)
        + b"\n"
        + valid
        + b"endsolid iterator\n"
    )
    return path


class TestRenderStlThumbnail:
    def test_uses_the_canonical_material_colour(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "canonical-material.stl"
        _write_renderable_binary_stl(path, 12)
        profile = type("PreviewProfile", (), {"material_albedo": (1.0, 0.0, 0.0)})()
        monkeypatch.setattr(stl_fallback, "PREVIEW_PROFILE", profile)

        result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

        assert result is not None
        pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
        shaded = pixels[:, :, :3][pixels[:, :, 3] > 20]
        assert shaded.size > 0
        assert float(shaded[:, 0].mean()) > float(shaded[:, 1].mean()) + 40.0
        assert float(shaded[:, 0].mean()) > float(shaded[:, 2].mean()) + 40.0

    def test_stl_fallback_uniformly_caps_sample_to_100k(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        path = tmp_path / "sampled.stl"
        _write_renderable_binary_stl(path, 101)
        result = stl_fallback.render_stl_thumbnail(path, max_triangles=10)

        assert result is not None
        assert result.triangle_count == 101
        assert result.sampled_triangles == 10
        assert result.parsed_triangles == 101
        assert result.scanned_bytes == path.stat().st_size

    def test_stl_fallback_dense_fixture_has_a_coherent_silhouette(
        self,
        tmp_path: Path,
    ) -> None:
        """The production fallback must cover a dense mesh instead of drawing points.

        An icosphere with 327,680 deterministic facets is deliberately above the
        100k work budget.  Aggregating its bounded sample into a coarse coverage
        grid should fill the projected silhouette, retain contrast, and leave a safe
        margin around the object.  The assertions are image properties rather than
        a pixel snapshot, so they tolerate renderer/library updates.
        """
        import trimesh

        from app.modules.media import stl_fallback

        path = tmp_path / "issue-67-dense-figure.stl"
        mesh = trimesh.creation.icosphere(subdivisions=7, radius=10.0)
        path.write_bytes(mesh.export(file_type="stl"))

        result = stl_fallback.render_stl_thumbnail(path, width=96, height=72)

        assert result is not None
        assert result.triangle_count == len(mesh.faces)
        assert result.sampled_triangles == stl_fallback._MAX_SAMPLED_TRIANGLES

        pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
        visible = pixels[:, :, 3] > 20
        ys, xs = np.where(visible)
        assert visible.mean() > 0.10  # visible silhouette, not a sparse point cloud
        assert np.ptp(xs) + 1 > pixels.shape[1] * 0.25
        assert np.ptp(ys) + 1 > pixels.shape[0] * 0.25
        assert xs.min() > 2 and xs.max() < pixels.shape[1] - 3
        assert ys.min() > 2 and ys.max() < pixels.shape[0] - 3
        bbox_area = (np.ptp(xs) + 1) * (np.ptp(ys) + 1)
        assert float(visible.sum() / bbox_area) > 0.45

        shaded = pixels[:, :, :3][visible]
        assert float(shaded.std()) > 10.0

    def test_stl_fallback_microfacets_keep_connected_surface_coverage(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Sub-pixel sampled facets must still produce a connected preview."""

        from app.modules.media import stl_fallback

        monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 10_000)
        path = tmp_path / "issue-67-connected-microfacets.stl"
        triangle_count = _write_microfaceted_surface_stl(path)
        result = stl_fallback.render_stl_thumbnail(path, width=96, height=72)

        assert result is not None
        assert result.triangle_count == triangle_count
        assert result.sampled_triangles == stl_fallback._MAX_SAMPLED_TRIANGLES
        pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
        visible = pixels[:, :, 3] > 20
        ys, xs = np.where(visible)
        assert visible.mean() >= 0.08
        assert np.ptp(xs) + 1 > pixels.shape[1] * 0.5
        assert np.ptp(ys) + 1 > pixels.shape[0] * 0.5
        bbox_area = (np.ptp(xs) + 1) * (np.ptp(ys) + 1)
        assert float(visible.sum() / bbox_area) >= 0.65

    def test_stl_fallback_work_budget_is_observable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from app.modules.media import mesh_render

        monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 32)
        original_rasterise = mesh_render._rasterise_triangles
        calls: list[int] = []

        def bounded_rasterise(*args, **kwargs):
            calls.append(int(args[2].shape[0]))
            return original_rasterise(*args, **kwargs)

        monkeypatch.setattr(mesh_render, "_rasterise_triangles", bounded_rasterise)
        path = tmp_path / "budget.stl"
        _write_annular_binary_stl(path)

        result = stl_fallback.render_stl_thumbnail(
            path, width=64, height=48, max_triangles=1_000
        )

        assert result is not None
        assert result.triangle_count == 768
        assert result.sampled_triangles == 32
        assert result.parsed_triangles == 768
        assert result.scanned_bytes == path.stat().st_size
        # Incomplete samples retain all source triangles and add one centroid-splat
        # triangle per source facet. Both paths stay bounded by the sample cap.
        assert calls and 32 <= sum(calls) <= 2 * 32

    def test_stl_fallback_global_candidate_budget_for_large_facets(
        self, tmp_path: Path
    ) -> None:

        path = tmp_path / "large-projected.stl"
        _write_large_projected_binary_stl(path, 100_001)

        result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

        assert result is not None
        assert result.sampled_triangles == 100_000
        assert result.raster_candidates == stl_fallback._MAX_COVERAGE_CANDIDATES
        assert result.source_complete is True
        assert result.complete is False

    def test_all_retained_facets_are_partial_when_raster_budget_is_exhausted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        path = tmp_path / "complete-sample-limited-raster.stl"
        _write_large_projected_binary_stl(path, 2)
        monkeypatch.setattr(stl_fallback, "_MAX_COVERAGE_CANDIDATES", 32)

        result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

        assert result is not None
        assert result.parsed_triangles == result.sampled_triangles == 2
        assert result.source_complete is True
        assert result.raster_candidates == 32
        assert result.complete is False

    def test_keeps_the_hole_open_when_rasterising_an_annulus(
        self, tmp_path: Path
    ) -> None:

        from app.modules.media import stl_fallback

        path = tmp_path / "annular-hole.stl"
        _write_annular_binary_stl(path)
        result = stl_fallback.render_stl_thumbnail(path, width=160, height=120)

        assert result is not None
        pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
        alpha = pixels[:, :, 3]
        center = alpha[alpha.shape[0] // 2, alpha.shape[1] // 2]
        assert center < 32
        assert float((alpha > 200).mean()) > 0.15
        assert float(pixels[:, :, :3][alpha > 200].std()) > 5.0

    def test_stl_fallback_refuses_nonfinite_facets(self, tmp_path: Path) -> None:

        path = tmp_path / "malformed-coordinates.stl"
        record = struct.Struct("<12fH")
        with path.open("wb") as fh:
            fh.write(b"malformed".ljust(80, b"\x00"))
            fh.write(struct.pack("<I", 2))
            fh.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    float("nan"),
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0,
                )
            )
            fh.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0,
                )
            )

        result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

        assert result is None

    def test_sampler_validates_more_records_than_it_retains(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "helpers.stl"
        _write_renderable_binary_stl(path, 2)

        sampled = stl_fallback.sample_stl_geometry(path, max_triangles=1)
        assert sampled is not None
        assert sampled.sampled_triangles == 1
        assert sampled.parsed_triangles == 2
        assert sampled.scanned_bytes == path.stat().st_size

    def test_binary_helpers_reject_a_file_shorter_than_the_header(
        self, tmp_path: Path
    ) -> None:
        short_header = tmp_path / "short-header.stl"
        short_header.write_bytes(b"short")

        assert stl_fallback.sample_stl_geometry(short_header) is None

    def test_binary_helpers_reject_a_truncated_facet_record(
        self, tmp_path: Path
    ) -> None:
        truncated = tmp_path / "truncated.stl"
        truncated.write_bytes(b"x" * 80 + struct.pack("<I", 1) + b"x")

        assert stl_fallback.sample_stl_geometry(truncated) is None

    def test_stl_fallback_binary_helpers_fail_closed_on_io_errors(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        path = tmp_path / "io-error.stl"
        _write_renderable_binary_stl(path, 1)
        original_open = Path.open

        def fail_open(_path: Path, *args, **kwargs):
            raise OSError("unreadable")

        monkeypatch.setattr(Path, "open", fail_open)
        assert stl_fallback.sample_stl_geometry(path) is None
        monkeypatch.setattr(Path, "open", original_open)

        def fail_stat(_path: Path):
            raise OSError("missing")

        monkeypatch.setattr(Path, "stat", fail_stat)
        assert stl_fallback._read_samples(path, 1) is None

    def test_binary_sampler_is_nothing_when_a_record_is_short(
        self, tmp_path: Path
    ) -> None:
        short = tmp_path / "sampler-truncated-record.stl"
        short.write_bytes(b"x" * 84 + b"x")

        assert stl_fallback.sample_stl_geometry(short) is None

    def test_binary_sampler_is_nothing_when_the_file_cannot_be_opened(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        path = tmp_path / "sampler-unreadable.stl"
        _write_renderable_binary_stl(path, 1)

        def fail_open(_path: Path, *args, **kwargs):
            raise OSError("unreadable")

        monkeypatch.setattr(Path, "open", fail_open)

        assert stl_fallback.sample_stl_geometry(path) is None

    def test_stl_fallback_ascii_helpers_fail_closed_on_io_errors(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        path = tmp_path / "ascii-io-error.stl"
        path.write_text("solid empty\n", encoding="ascii")

        def fail_open(_path: Path, *args, **kwargs):
            raise OSError("unreadable")

        monkeypatch.setattr(Path, "open", fail_open)
        assert stl_fallback.sample_stl_geometry(path) is None

    def test_ascii_samples_from_a_partly_invalid_source_are_refused(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "ascii-invalid-source.stl"
        path.write_text(
            "solid invalid\nvertex broken 0 0\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n",
            encoding="ascii",
        )

        assert stl_fallback.sample_stl_geometry(path, max_triangles=4) is None

    def test_ascii_samples_are_nothing_when_no_facet_parses(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "ascii-empty.stl"
        path.write_text("solid empty\nvertex nan 0 0\n", encoding="ascii")

        assert stl_fallback.sample_stl_geometry(path) is None

    def test_sample_budget_must_be_positive(self, tmp_path: Path) -> None:
        path = tmp_path / "zero-budget.stl"
        _write_renderable_binary_stl(path, 1)

        with pytest.raises(ValueError):
            stl_fallback.sample_stl_geometry(path, max_triangles=0)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"width": 0},
            {"height": stl_fallback._MAX_RENDER_DIMENSION + 1},
        ],
        ids=["zero-width", "height-over-cap"],
    )
    def test_refuses_a_frame_size_outside_the_supported_range(
        self, tmp_path: Path, kwargs: dict[str, int]
    ) -> None:
        path = tmp_path / "render-dimensions.stl"
        _write_renderable_binary_stl(path, 1)

        assert stl_fallback.render_stl_thumbnail(path, **kwargs) is None

    def test_refuses_a_sample_whose_coordinates_are_not_finite(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        path = tmp_path / "render-nan-coordinates.stl"
        _write_renderable_binary_stl(path, 1)
        sampled = _sampled_stl(coordinates=[float("nan")] * 9)

        monkeypatch.setattr(stl_fallback, "_read_samples", lambda *_args: sampled)

        assert stl_fallback.render_stl_thumbnail(path) is None

    def test_refuses_a_sample_whose_bounds_are_a_single_extreme_point(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        path = tmp_path / "render-degenerate-bounds.stl"
        _write_renderable_binary_stl(path, 1)
        sampled = _sampled_stl(
            bounds_min=(1e308, 1e308, 1e308), bounds_max=(1e308, 1e308, 1e308)
        )

        monkeypatch.setattr(stl_fallback, "_read_samples", lambda *_args: sampled)

        assert stl_fallback.render_stl_thumbnail(path) is None

    def test_refuses_a_sample_whose_bounds_overflow_float32(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from app.modules.media import mesh_render

        path = tmp_path / "render-overflowing-bounds.stl"
        _write_renderable_binary_stl(path, 1)
        sampled = _sampled_stl(
            bounds_min=(-1e308, -1e308, -1e308), bounds_max=(1e308, 1e308, 1e308)
        )

        monkeypatch.setattr(stl_fallback, "_read_samples", lambda *_args: sampled)
        monkeypatch.setattr(
            mesh_render, "_select_view_rotation", lambda *_args: np.eye(3)
        )

        assert stl_fallback.render_stl_thumbnail(path) is None

    @pytest.mark.parametrize(
        "rotation",
        [
            lambda *_args: (_ for _ in ()).throw(ValueError("rotation")),
            lambda *_args: np.full((3, 3), np.nan),
        ],
        ids=["raises", "returns-nan"],
    )
    def test_refuses_to_render_when_view_selection_fails(
        self, tmp_path: Path, monkeypatch, rotation
    ) -> None:
        from app.modules.media import mesh_render

        path = tmp_path / "render-rotation.stl"
        _write_renderable_binary_stl(path, 1)
        sampled = _sampled_stl()

        monkeypatch.setattr(stl_fallback, "_read_samples", lambda *_args: sampled)
        monkeypatch.setattr(mesh_render, "_select_view_rotation", rotation)

        assert stl_fallback.render_stl_thumbnail(path) is None

    def test_stl_fallback_returns_none_when_optional_render_dependencies_are_missing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import builtins

        path = tmp_path / "missing-dependency.stl"
        _write_renderable_binary_stl(path, 1)
        original_import = builtins.__import__

        def missing_numpy(name, *args, **kwargs):
            if name == "numpy":
                raise ImportError("numpy unavailable")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", missing_numpy)
        assert stl_fallback.render_stl_thumbnail(path) is None

    def test_incomplete_annular_sample_still_preserves_hole(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        from app.modules.media import stl_fallback

        monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 32)
        path = tmp_path / "incomplete-annular-hole.stl"
        _write_annular_binary_stl(path)
        result = stl_fallback.render_stl_thumbnail(path, width=160, height=120)

        assert result is not None
        assert result.complete is False
        pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
        assert pixels[pixels.shape[0] // 2, pixels.shape[1] // 2, 3] < 32

    def test_renders_a_dense_microfaceted_annulus_as_one_connected_ring(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        from app.modules.media import stl_fallback

        monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 768)
        path = tmp_path / "dense-microfaceted-annulus.stl"
        triangle_count = _write_microfaceted_annular_stl(path)
        result = stl_fallback.render_stl_thumbnail(path, width=160, height=120)

        assert result is not None
        assert result.triangle_count == triangle_count
        assert result.sampled_triangles == 768
        assert result.raster_candidates <= stl_fallback._MAX_COVERAGE_CANDIDATES
        pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
        alpha = pixels[:, :, 3]
        assert alpha[alpha.shape[0] // 2, alpha.shape[1] // 2] < 32
        visible = alpha > 20
        assert visible.mean() >= 0.08
        assert _largest_component_fraction(visible) >= 0.70

    def test_refuses_an_oversized_ascii_line(
        self,
        tmp_path: Path,
    ) -> None:

        path = tmp_path / "hostile-line.stl"
        valid = """facet normal 0 0 1
    outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 0 1 0
    endloop
    endfacet
    """
        path.write_text(
            "solid hostile\n"
            + "comment "
            + ("x" * (STLReadLimits().max_line_bytes + 10_000))
            + "\n"
            + valid
            + "endsolid hostile\n",
            encoding="ascii",
        )

        result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

        assert result is None

    def test_caps_retained_ascii_facets_without_stopping_validation(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 4)
        path = tmp_path / "ascii-budget.stl"
        facets = []
        for index in range(10):
            facets.append(
                "facet normal 0 0 1\n"
                "outer loop\n"
                f"vertex {index} 0 0\n"
                f"vertex {index + 1} 0 0\n"
                f"vertex {index} 1 0\n"
                "endloop\n"
                "endfacet\n"
            )
        path.write_text("solid budget\n" + "".join(facets) + "endsolid budget\n")

        result = stl_fallback.render_stl_thumbnail(
            path, width=64, height=48, max_triangles=1_000
        )

        assert result is not None
        assert result.triangle_count == 10
        assert result.sampled_triangles == 4
        assert result.parsed_triangles == 10
        assert result.scanned_bytes == path.stat().st_size

    def test_ascii_fallback_refuses_source_above_byte_budget(
        self, tmp_path: Path, monkeypatch
    ) -> None:

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

        result = stl_fallback.sample_stl_geometry(
            path, limits=STLReadLimits(max_source_bytes=500)
        )

        assert result is None

    @pytest.mark.parametrize(
        "pending_vertices",
        ["vertex 2 2 2\n", "vertex 2 2 2\nvertex 3 3 3\n"],
    )
    def test_ascii_pending_vertices_at_eof_are_refused(
        self, tmp_path: Path, pending_vertices: str
    ) -> None:

        path = tmp_path / "ascii-pending-eof.stl"
        path.write_text(
            "solid pending\n"
            "facet normal 0 0 1\n"
            "outer loop\n"
            "vertex 0 0 0\n"
            "vertex 1 0 0\n"
            "vertex 0 1 0\n"
            "endloop\n"
            "endfacet\n" + pending_vertices
        )

        result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

        assert result is None

    def test_ascii_fallback_rejects_float32_overflow(self, tmp_path: Path) -> None:

        path = tmp_path / "float-overflow.stl"
        path.write_text(
            "solid overflow\n"
            "facet normal 0 0 1\n"
            "outer loop\n"
            "vertex 1e308 0 0\n"
            "vertex 0 1e308 0\n"
            "vertex 0 0 1e308\n"
            "endloop\n"
            "endfacet\n"
            "endsolid overflow\n",
            encoding="ascii",
        )

        assert stl_fallback.render_stl_thumbnail(path, width=64, height=48) is None


class TestPartialSampling:
    def test_hostile_ascii_is_refused(self, tmp_path):
        source = _write_hostile_ascii(tmp_path / "hostile.stl")
        result = stl_fallback.sample_stl_geometry(source, max_triangles=10)
        assert result is None


class TestSampleWorkingSet:
    def test_bounds_sampling_working_set(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "working-set.stl"
        _write_renderable_binary_stl(path, 120)
        original = np.concatenate
        records = []

        def observed(arrays, *args, **kwargs):
            parts = tuple(arrays)
            if parts and all(
                array.ndim == 3 and array.shape[1:] == (3, 3) for array in parts
            ):
                records.append(sum(len(array) for array in parts))
            return original(parts, *args, **kwargs)

        monkeypatch.setattr(np, "concatenate", observed)
        sampled = stl_fallback.read_stl_sample(
            path, max_triangles=17, limits=STLReadLimits(chunk_triangles=31)
        )

        assert sampled.sampled_triangles == 17
        assert sampled.parsed_triangles == 120
        assert records and max(records) <= 17 + 31
