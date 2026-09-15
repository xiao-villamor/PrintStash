"""Rust recovery jobs own file reads, framing, coverage, and encoded output."""

import io

import numpy as np
import printstash_mesh_native as native
import pytest
import trimesh
from PIL import Image
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE as p  # noqa: E402

RECIPE = (
    p.margin_fraction,
    p.hero_azimuth_degrees,
    p.hero_elevation_degrees,
    p.flat_tilt_degrees,
    p.flat_thickness_ratio,
    *p.material_albedo,
)


def stream(path, **changes):
    options = dict(
        width=80,
        height=60,
        max_triangles=100_000,
        max_source_bytes=1 << 30,
        max_candidates=20_000_000,
        chunk=64,
        timeout=45.0,
        recipe=RECIPE,
        max_lines=10_000_000,
        max_line_bytes=65536,
    )
    options.update(changes)
    return native.render_stl_streaming(path, **options)


@pytest.mark.parametrize("file_type", ["stl", "stl_ascii"])
def test_streaming_job_publishes_complete_source(tmp_path, file_type):
    mesh = trimesh.creation.box(extents=[10, 20, 30])
    path = tmp_path / "box.stl"
    data = mesh.export(file_type=file_type)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    image, count, parsed, lower, upper, scanned, complete, used, seconds = stream(path)
    pixels = np.asarray(Image.open(io.BytesIO(image)).convert("RGBA"))
    assert pixels.shape == (60, 80, 4)
    assert np.count_nonzero(pixels[:, :, 3]) > 100
    assert pixels[0, 0, 3] == 0
    assert count == parsed == len(mesh.faces)
    assert complete is True
    assert tuple(lower) == (-5.0, -10.0, -15.0)
    assert tuple(upper) == (5.0, 10.0, 15.0)
    assert scanned == path.stat().st_size
    assert 0 < used <= 20_000_000
    assert all(t >= 0 for t in seconds)


@pytest.mark.parametrize(
    "changes",
    [dict(max_candidates=1), dict(max_triangles=1), dict(max_source_bytes=10)],
)
def test_streaming_job_rejects_exhausted_budget(tmp_path, changes):
    path = tmp_path / "box.stl"
    trimesh.creation.box().export(path)
    with pytest.raises(ValueError, match="budget"):
        stream(path, **changes)


@pytest.mark.parametrize(
    "data",
    [
        b"solid x\nfacet normal 0 0 1\n",
        b"solid x\nvertex 0 0 0\n",
        b"solid x\n" + b"x" * 65537,
    ],
)
def test_streaming_job_rejects_malformed_ascii(tmp_path, data):
    path = tmp_path / "bad.stl"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        stream(path)


def test_streaming_job_respects_ascii_line_limit(tmp_path):
    path = tmp_path / "box.stl"
    path.write_text(trimesh.creation.box().export(file_type="stl_ascii"))
    with pytest.raises(ValueError, match="line budget"):
        stream(path, max_lines=2)


def test_sampled_job_marks_partial_source(tmp_path):
    path = tmp_path / "sphere.stl"
    mesh = trimesh.creation.icosphere(subdivisions=3)
    mesh.export(path)
    image, count, parsed, _lo, _hi, scanned, complete, used, _seconds = (
        native.render_stl_fallback(path, 160, 120, 32, RECIPE)
    )
    assert count == len(mesh.faces)
    assert parsed == 32
    assert complete is False
    assert scanned == 84 + 32 * 50
    assert 0 < used <= 2_000_000
    assert Image.open(io.BytesIO(image)).size == (160, 120)


def test_sampled_ascii_marks_truncated_source(tmp_path):
    path = tmp_path / "sphere.stl"
    path.write_text(
        trimesh.creation.icosphere(subdivisions=2).export(file_type="stl_ascii")
    )
    result = native.render_stl_fallback(path, 160, 120, 32, RECIPE)
    assert result[2] == 32
    assert result[6] is False


def test_sampled_complete_job_preserves_hole(tmp_path):
    path = tmp_path / "ring.stl"
    mesh = trimesh.creation.annulus(r_min=5, r_max=10, height=1, sections=64)
    mesh.export(path)
    result = native.render_stl_fallback(path, 160, 120, 100_000, RECIPE)
    pixels = np.asarray(Image.open(io.BytesIO(result[0])).convert("RGBA"))
    assert result[6] is True
    assert pixels[60, 80, 3] == 0
    assert np.count_nonzero(pixels[:, :, 3]) > 500
