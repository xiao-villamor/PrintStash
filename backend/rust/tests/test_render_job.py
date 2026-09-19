"""Complete native render jobs preserve the established preview recipe."""

import numpy as np
import printstash_mesh_native as native
import pytest
import reference_native_adapter as stages
import reference_rasterizer as rasterizer  # noqa: E402
import trimesh
from printstash_core.mesh import native_rasterizer
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE as p  # noqa: E402


@pytest.mark.parametrize(
    "extents", [[20, 30, 40], [0.1, 20, 30], [20, 0.1, 30], [20, 30, 0.1]]
)
@pytest.mark.parametrize("output_format", ["PNG", "WEBP"])
def test_complete_job_preserves_preview(extents, output_format):
    mesh = trimesh.creation.box(extents=extents)
    expected = rasterizer.render_mesh_thumbnail(
        mesh,
        "reference",
        width=80,
        height=60,
        output_format=output_format,
        mesh_preparer=stages.prepare_mesh,
        image_encoder=stages.encode_preview,
    )
    actual = native_rasterizer.render_preview(
        mesh, width=80, height=60, output_format=output_format
    )
    assert actual == expected


@pytest.mark.parametrize("matte", [True, False])
def test_complete_job_preserves_explicit_view(matte):
    mesh = trimesh.creation.icosphere(subdivisions=2)
    options = dict(width=80, height=60, view_rotation=np.eye(3), matte=matte)
    expected = rasterizer.render_mesh_thumbnail(
        mesh,
        "reference",
        mesh_preparer=stages.prepare_mesh,
        image_encoder=stages.encode_preview,
        **options,
    )
    assert native_rasterizer.render_preview(mesh, **options) == expected


@pytest.mark.parametrize(
    "width,height,factor,margin",
    [
        (0, 80, 2, 0.1),
        (2049, 60, 2, 0.1),
        (80, 0, 2, 0.1),
        (80, 60, 0, 0.1),
        (80, 60, 2, float("nan")),
    ],
)
def test_complete_job_rejects_invalid_config(width, height, factor, margin):
    mesh = trimesh.creation.box()
    with pytest.raises(ValueError):
        native.render_preview(
            np.asarray(mesh.vertices, dtype=np.float32).tobytes(),
            np.asarray(mesh.faces, dtype=np.int64).tobytes(),
            width,
            height,
            64,
            "PNG",
            (
                margin,
                p.hero_azimuth_degrees,
                p.hero_elevation_degrees,
                p.flat_tilt_degrees,
                p.flat_thickness_ratio,
                *p.material_albedo,
            ),
            (4096, factor, factor),
        )


def test_rgb_job_matches_white_composite():
    import io

    from PIL import Image

    mesh = trimesh.creation.icosphere(subdivisions=2)
    png = native_rasterizer.render_preview(mesh, width=80, height=60, matte=True)
    rgba = Image.open(io.BytesIO(png)).convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    background.alpha_composite(rgba)
    expected = background.convert("RGB").tobytes()
    assert (
        native_rasterizer.render_preview(
            mesh, width=80, height=60, matte=True, output_format="RGB"
        )
        == expected
    )


def test_shared_views_match_individual_jobs():
    mesh = trimesh.creation.icosphere(subdivisions=2)
    frames = [None, np.eye(3).tolist(), np.diag([-1.0, 1.0, -1.0]).tolist()]
    actual = native_rasterizer.render_views(mesh, 80, 60, frames)
    expected = [
        native_rasterizer.render_preview(
            mesh,
            width=80,
            height=60,
            view_rotation=frame,
            matte=True,
            output_format="RGB",
        )
        for frame in frames
    ]
    assert actual == expected


def test_shared_views_reject_unbounded_output():
    mesh = trimesh.creation.box()
    with pytest.raises(ValueError, match="multiview budget"):
        native_rasterizer.render_views(mesh, 80, 60, [None] * 7)
