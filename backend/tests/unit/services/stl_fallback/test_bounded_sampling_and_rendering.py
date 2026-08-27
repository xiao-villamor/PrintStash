"""Bounded STL sampling preserves useful geometry without trusting hostile input.

A regression could exceed mesh budgets or publish an incomplete render.
"""

from __future__ import annotations

from ..mesh_processing._mesh_limits_shared import (
    Path,
    _largest_component_fraction,
    _overlay,
    _write_annular_binary_stl,
    _write_binary_stl,
    _write_large_projected_binary_stl,
    _write_microfaceted_annular_stl,
    _write_microfaceted_surface_stl,
    _write_renderable_binary_stl,
    io,
    mesh_processing,
    np,
    pytest,
    struct,
    zipfile,
)


def test_binary_stl_triangle_count_is_exact(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _write_binary_stl(p, 1234)
    assert mesh_processing._estimate_triangle_count(p) == 1234


def test_3mf_triangle_count_from_uncompressed_xml(tmp_path: Path) -> None:
    p = tmp_path / "dense.3mf"
    model_xml = b"<triangle/>" * 10_000  # 110_000 bytes of "mesh"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", model_xml)
    # ~70 bytes per triangle proxy.
    assert mesh_processing._estimate_triangle_count(p) == len(model_xml) // 70


def test_over_cap_mesh_is_never_loaded(tmp_path: Path, monkeypatch) -> None:
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
    tmp_path: Path, monkeypatch
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


def test_stl_fallback_uniformly_caps_sample_to_100k(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "sampled.stl"
    _write_renderable_binary_stl(path, 101)
    result = stl_fallback.render_stl_thumbnail(path, max_triangles=10)

    assert result is not None
    assert result.triangle_count == 101
    assert result.sampled_triangles == 10
    assert result.parsed_triangles == 10
    assert result.scanned_bytes <= 84 + (10 * 50)


def test_stl_fallback_dense_fixture_has_a_coherent_silhouette(
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
    from PIL import Image

    from app.services import stl_fallback

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
    assert float(shaded.std()) > 10.0  # lighting still provides useful contrast


def test_stl_fallback_microfacets_keep_connected_surface_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    """Sub-pixel sampled facets must still produce a connected preview."""
    from PIL import Image

    from app.services import stl_fallback

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


def test_stl_fallback_work_budget_is_observable(tmp_path: Path, monkeypatch) -> None:
    from app.services import mesh_render, stl_fallback

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
    assert result.parsed_triangles == 32
    assert result.scanned_bytes <= 84 + (32 * 50)
    # Incomplete samples retain all source triangles and add one centroid-splat
    # triangle per source facet. Both paths stay bounded by the sample cap.
    assert calls and 32 <= sum(calls) <= 2 * 32


def test_stl_fallback_global_candidate_budget_for_large_facets(tmp_path: Path) -> None:
    from app.services import stl_fallback

    path = tmp_path / "large-projected.stl"
    _write_large_projected_binary_stl(path, 100_001)

    result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

    assert result is not None
    assert result.sampled_triangles == 100_000
    assert result.raster_candidates == stl_fallback._MAX_COVERAGE_CANDIDATES


def test_stl_fallback_rasterises_real_area_and_preserves_hole(tmp_path: Path) -> None:
    from PIL import Image

    from app.services import stl_fallback

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


def test_incomplete_annular_sample_still_preserves_hole(
    tmp_path: Path, monkeypatch
) -> None:
    from PIL import Image

    from app.services import stl_fallback

    monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 32)
    path = tmp_path / "incomplete-annular-hole.stl"
    _write_annular_binary_stl(path)
    result = stl_fallback.render_stl_thumbnail(path, width=160, height=120)

    assert result is not None
    assert result.complete is False
    pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
    assert pixels[pixels.shape[0] // 2, pixels.shape[1] // 2, 3] < 32


def test_dense_microfaceted_annulus_keeps_hole_and_connected_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    from PIL import Image

    from app.services import stl_fallback

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


def test_ascii_fallback_discards_hostile_line_and_recovers(
    tmp_path: Path,
) -> None:
    from app.services import stl_fallback

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
        + ("x" * (stl_fallback._MAX_ASCII_LINE_BYTES + 10_000))
        + "\n"
        + valid
        + "endsolid hostile\n",
        encoding="ascii",
    )

    result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

    assert result is not None
    assert result.triangle_count == 1
    assert result.parsed_triangles == 1
    assert result.scanned_bytes <= stl_fallback._MAX_ASCII_BYTES
    assert result.complete is False


def test_ascii_fallback_caps_total_facets_and_bytes(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 4)
    path = tmp_path / "ascii-budget.stl"
    facet = (
        "facet normal 0 0 1\n"
        "outer loop\n"
        "vertex 0 0 0\n"
        "vertex 1 0 0\n"
        "vertex 0 1 0\n"
        "endloop\n"
        "endfacet\n"
    )
    path.write_text("solid budget\n" + facet * 10 + "endsolid budget\n")

    result = stl_fallback.render_stl_thumbnail(
        path, width=64, height=48, max_triangles=1_000
    )

    assert result is not None
    assert result.triangle_count == 4
    assert result.sampled_triangles == 4
    assert result.parsed_triangles == 4
    assert result.scanned_bytes <= stl_fallback._MAX_ASCII_BYTES


@pytest.mark.parametrize(
    "pending_vertices",
    ["vertex 2 2 2\n", "vertex 2 2 2\nvertex 3 3 3\n"],
    ids=["one-pending-vertex", "two-pending-vertices"],
)
def test_ascii_pending_vertices_at_eof_are_incomplete(
    tmp_path: Path, monkeypatch, pending_vertices: str
) -> None:
    from app.services import stl_fallback

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

    assert result is not None
    assert result.parsed_triangles == 1
    assert result.complete is False

    monkeypatch.setattr(mesh_processing, "_estimate_triangle_count", lambda _p: None)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: None)
    geometry, thumbnail = mesh_processing.analyze_mesh(path)
    assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
    assert thumbnail.complete is False
    assert geometry["triangle_count"] is None
    assert geometry["bbox_x_mm"] is None


def test_ascii_fallback_marks_truncated_metadata_incomplete(
    tmp_path: Path, monkeypatch
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

    result = stl_fallback.render_stl_thumbnail(path, width=64, height=48)

    assert result is not None
    assert result.complete is False
    assert result.scanned_bytes <= 500

    monkeypatch.setattr(mesh_processing, "_estimate_triangle_count", lambda _p: None)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: None)
    geometry, thumbnail = mesh_processing.analyze_mesh(path)
    assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
    assert thumbnail.complete is False
    assert geometry["triangle_count"] is None
    assert geometry["bbox_x_mm"] is None


def test_ascii_fallback_rejects_float32_overflow(tmp_path: Path) -> None:
    from app.services import stl_fallback

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


def test_stl_fallback_skips_nonfinite_facets(tmp_path: Path) -> None:
    from app.services import stl_fallback

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

    assert result is not None
    assert result.triangle_count == 2
    assert result.sampled_triangles == 1


def test_stl_fallback_binary_helpers_bound_reads_and_reject_truncation(
    tmp_path: Path,
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "helpers.stl"
    _write_renderable_binary_stl(path, 2)

    assert stl_fallback._binary_stl_info(path) == (2, path.stat().st_size)
    assert stl_fallback._is_binary_stl(path)
    records = list(stl_fallback._iter_binary_triangles(path, max_triangles=1))
    assert len(records) == 1
    assert len(records[0]) == 9

    short_header = tmp_path / "short-header.stl"
    short_header.write_bytes(b"short")
    assert stl_fallback._binary_stl_info(short_header) is None
    assert list(stl_fallback._iter_binary_triangles(short_header)) == []

    truncated = tmp_path / "truncated.stl"
    truncated.write_bytes(b"x" * 80 + struct.pack("<I", 1) + b"x")
    assert list(stl_fallback._iter_binary_triangles(truncated)) == []
    assert stl_fallback._read_binary_samples(truncated, 1) is None


def test_stl_fallback_binary_helpers_fail_closed_on_io_errors(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "io-error.stl"
    _write_renderable_binary_stl(path, 1)
    original_open = Path.open

    def fail_open(_path: Path, *args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "open", fail_open)
    assert stl_fallback._binary_stl_info(path) is None
    assert list(stl_fallback._iter_binary_triangles(path)) == []
    assert stl_fallback._read_binary_samples(path, 1) is None
    monkeypatch.setattr(Path, "open", original_open)

    def fail_stat(_path: Path):
        raise OSError("missing")

    monkeypatch.setattr(Path, "stat", fail_stat)
    assert stl_fallback._read_samples(path, 1) is None


def test_stl_fallback_binary_sampler_handles_short_records_and_io_failures(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "sampler-short-record.stl"
    _write_renderable_binary_stl(path, 1)
    info = (1, path.stat().st_size)
    short = tmp_path / "sampler-truncated-record.stl"
    short.write_bytes(b"x" * 84 + b"x")
    assert stl_fallback._read_binary_samples(short, 1, info=(1, 85)) is None

    def fail_open(_path: Path, *args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "open", fail_open)
    assert stl_fallback._read_binary_samples(path, 1, info=info) is None
