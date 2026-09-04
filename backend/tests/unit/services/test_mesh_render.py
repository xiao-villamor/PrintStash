"""Choosing a camera and a shading model so a preview looks like the object.

The software rasteriser produces the thumbnail a user scans a library by, so
"correct" here means recognisable rather than geometrically defensible. Two
decisions carry that, and both have a wrong answer that still renders.

**The view.** A flat plate photographed from above is a rectangle; a solid part
from the front is a silhouette. So the camera is chosen from the geometry — front
for a Z-flat mesh, a broad-face view for an X-flat one, a Z-up hero angle for a
solid — and a front-facing flat mesh must *not* fall back to the silhouette path,
which is the case that produced featureless grey squares.

**The shading.** A smooth surface has to render as a gradient, or a curved part
looks faceted; hard edges have to stay flat, or a mechanical part looks melted.
Those two pull in opposite directions, which is why both are asserted rather than
just one.

These are pixel-level assertions on real geometry because there is no other way
to catch "it renders, and it looks wrong".
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.services import mesh_render


def _tilt_matrix() -> np.ndarray:
    # Flat meshes are viewed face-on but tipped 25° around screen-X
    # (see mesh_render._front_rotation_for_thin_axis).
    tilt = np.radians(25.0)
    ct, st = np.cos(tilt), np.sin(tilt)
    return np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]], dtype=np.float64)


class TestSelectViewRotation:
    def test_flat_z_mesh_uses_front_view_like_stl_viewer(self) -> None:
        verts = np.array(
            [
                [-5.0, -5.0, -0.2],
                [5.0, -5.0, -0.2],
                [5.0, 5.0, 0.2],
                [-5.0, 5.0, 0.2],
            ],
            dtype=np.float64,
        )

        rotation = mesh_render._select_view_rotation(verts, np)
        view = verts @ rotation.T

        expected = _tilt_matrix() @ np.diag([1.0, 1.0, -1.0])
        np.testing.assert_allclose(rotation, expected, atol=1e-12)
        # Screen-X is untouched by the tilt: the broad face still spans the view.
        np.testing.assert_allclose(view[:, 0], verts[:, 0])

    def test_solid_mesh_uses_z_up_hero_view(self) -> None:
        # A chunky/solid mesh gets the 3/4 "hero" view, not the flat front view.
        # Print models are Z-up, so object +Z must map to (mostly) screen-up — the
        # old view looked down the Z axis and showed the top of upright models (e.g.
        # the gathered top of a dumpling) instead of their front.
        verts = np.array(
            [
                [-1.0, -1.0, -1.0],
                [1.0, -1.0, -1.0],
                [1.0, 1.0, -1.0],
                [-1.0, 1.0, -1.0],
                [-1.0, -1.0, 1.0],
                [1.0, -1.0, 1.0],
                [1.0, 1.0, 1.0],
                [-1.0, 1.0, 1.0],
            ],
            dtype=np.float64,
        )

        rotation = mesh_render._select_view_rotation(verts, np)

        # A proper rotation, and not the flat front-view fallback.
        np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-9)
        assert np.linalg.det(rotation) > 0.99
        assert not np.allclose(rotation, np.diag([1.0, 1.0, -1.0]))

        # Object +Z lands (mostly) on screen-up, not pointing into the screen.
        z_on_screen = rotation @ np.array([0.0, 0.0, 1.0])
        assert z_on_screen[1] > 0.8  # up
        assert abs(z_on_screen[0]) < 0.2  # not tipped sideways

    def test_flat_x_mesh_uses_broad_face_view(self) -> None:
        verts = np.array(
            [
                [-0.1, -2.0, -3.0],
                [0.1, -2.0, -3.0],
                [0.1, 2.0, 3.0],
                [-0.1, 2.0, 3.0],
            ],
            dtype=np.float64,
        )

        rotation = mesh_render._select_view_rotation(verts, np)

        expected = _tilt_matrix() @ np.array(
            [
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
                [-1.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
        np.testing.assert_allclose(rotation, expected, atol=1e-12)

    def test_front_facing_flat_mesh_does_not_fall_back_to_silhouette(
        self, monkeypatch
    ) -> None:
        mesh = SimpleNamespace(
            vertices=np.array(
                [
                    [-1.0, -1.0, 0.0],
                    [1.0, -1.0, 0.0],
                    [1.0, 1.0, 0.0],
                    [-1.0, 1.0, 0.0],
                ],
                dtype=np.float64,
            ),
            faces=np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64),
        )
        warnings: list[str] = []

        def capture_warning(message: str, *args, **kwargs) -> None:
            warnings.append(message % args if args else message)

        monkeypatch.setattr(mesh_render.logger, "warning", capture_warning)

        png = mesh_render.render_thumbnail(
            lambda _path: mesh, Path("front-facing.stl"), width=80, height=80
        )

        assert png is not None
        assert png.startswith(b"\x89PNG")
        assert not any("no visible triangles" in message for message in warnings)


def _unique_opaque_colours(png: bytes) -> int:
    """Number of distinct RGB shades among the opaque pixels of a thumbnail."""
    import io

    from PIL import Image

    arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
    opaque = arr[arr[:, :, 3] > 200][:, :3]
    return len(np.unique(opaque.reshape(-1, 3), axis=0))


class TestRenderThumbnail:
    def test_smooth_surface_renders_a_gradient_not_facets(self) -> None:
        # Crease-aware Gouraud shading: a sphere must shade as a smooth gradient
        # (thousands of shades), not a handful of flat facets. Guards the original
        # faceted-thumbnail regression.
        import trimesh

        png = mesh_render.render_mesh_thumbnail(
            trimesh.creation.uv_sphere(radius=5.0, count=[48, 48]), "sphere"
        )
        assert png is not None
        assert _unique_opaque_colours(png) > 2000

    def test_hard_edges_stay_flat_not_melted(self) -> None:
        # The flip side: a cube must keep flat, distinct faces (a crease at each
        # edge), so it has far fewer shades than a smooth body of similar size.
        # Guards against smoothing rounding mechanical parts into a blob.
        import trimesh

        box = mesh_render.render_mesh_thumbnail(
            trimesh.creation.box(extents=[10.0, 12.0, 10.0]), "box"
        )
        sphere = mesh_render.render_mesh_thumbnail(
            trimesh.creation.uv_sphere(radius=6.0, count=[48, 48]), "sphere"
        )
        assert box is not None and sphere is not None
        assert _unique_opaque_colours(box) * 3 < _unique_opaque_colours(sphere)

    def test_chunked_render_produces_valid_png(self, monkeypatch) -> None:
        import io

        import trimesh
        from PIL import Image

        mesh = trimesh.creation.icosphere(subdivisions=4, radius=10.0)  # 5120 faces
        _set_chunk_size(monkeypatch, 500)  # forces ~11 chunks
        png = mesh_render.render_mesh_thumbnail(
            mesh, "sphere.stl", width=120, height=90
        )

        assert png is not None and png.startswith(_PNG_MAGIC)
        img = Image.open(io.BytesIO(png))
        assert img.size == (120, 90)
        alpha = np.array(img.convert("RGBA"))[:, :, 3]
        assert alpha.max() == 255  # the model actually painted pixels

    def test_chunk_size_does_not_change_output(self, monkeypatch) -> None:
        # A single-pass render and a many-chunk render of the same mesh must be
        # visually identical — the shared z-buffer makes chunk order irrelevant and
        # the smooth-normal table is welded globally before shading.
        import io

        import trimesh
        from PIL import Image

        mesh = trimesh.creation.icosphere(subdivisions=4, radius=10.0)

        _set_chunk_size(monkeypatch, 10_000_000)  # one chunk
        one = mesh_render.render_mesh_thumbnail(mesh, "sphere.stl")
        _set_chunk_size(monkeypatch, 137)  # many small chunks
        many = mesh_render.render_mesh_thumbnail(mesh, "sphere.stl")

        assert one is not None and many is not None
        a = np.array(Image.open(io.BytesIO(one)).convert("RGBA"), dtype=np.int16)
        b = np.array(Image.open(io.BytesIO(many)).convert("RGBA"), dtype=np.int16)
        # Allow a hair of tolerance for float-summation-order differences in the
        # chunked weld; in practice this is bit-identical.
        assert np.abs(a - b).max() <= 1

    def test_small_mesh_renders_in_a_single_chunk(self) -> None:
        import io

        import trimesh
        from PIL import Image

        mesh = trimesh.creation.box(extents=(10, 20, 30))
        png = mesh_render.render_mesh_thumbnail(mesh, "box.stl", width=64, height=64)
        assert png is not None and png.startswith(_PNG_MAGIC)
        assert Image.open(io.BytesIO(png)).size == (64, 64)

    def test_raster_budget_is_cumulative_across_calls(self) -> None:
        img = np.zeros((16, 16, 3), dtype=np.uint8)
        zbuf = np.full((16, 16), np.inf, dtype=np.float64)
        triangle = np.array([[[0.0, 0.0, 0.0], [9.0, 0.0, 0.0], [0.0, 9.0, 0.0]]])
        normals = np.zeros((1, 3, 3), dtype=np.float64)
        budget = mesh_render.RasterBudget(limit=16)

        mesh_render._rasterise_triangles(
            img,
            zbuf,
            triangle,
            normals,
            lambda n: np.ones((n.shape[0], 3)),
            np.ones(3),
            16,
            16,
            budget=budget,
        )
        mesh_render._rasterise_triangles(
            img,
            zbuf,
            triangle,
            normals,
            lambda n: np.ones((n.shape[0], 3)),
            np.ones(3),
            16,
            16,
            budget=budget,
        )

        assert budget.used == 16
        assert np.isfinite(zbuf).any()

    def test_normal_renderer_keeps_large_face_at_1280(self) -> None:
        from PIL import Image

        mesh = SimpleNamespace(
            vertices=np.array(
                [[-100.0, -100.0, 0.0], [100.0, -100.0, 0.0], [0.0, 100.0, 0.0]]
            ),
            faces=np.array([[0, 1, 2]], dtype=np.int64),
        )

        png = mesh_render.render_mesh_thumbnail(mesh, "large-face.stl", 1280, 1280)

        assert png is not None
        alpha = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))[:, :, 3]
        assert alpha.max() == 255
        assert (alpha > 200).mean() > 0.10

    def test_huge_mesh_never_allocates_full_face_arrays(self, monkeypatch) -> None:
        # Spy on the rasteriser: every chunk it receives must be bounded by the
        # configured chunk size, proving per-face arrays are built per-chunk and a
        # full (F, 3, 3) array is never materialised.
        import trimesh

        mesh = trimesh.creation.icosphere(subdivisions=5, radius=10.0)  # 20480 faces
        chunk = 1000
        _set_chunk_size(monkeypatch, chunk)

        seen_max = {"n": 0}
        real = mesh_render._rasterise_triangles

        def _spy(img, zbuf, tri, vert_nrm, shade, base_color, width, height):
            seen_max["n"] = max(seen_max["n"], int(tri.shape[0]))
            return real(img, zbuf, tri, vert_nrm, shade, base_color, width, height)

        monkeypatch.setattr(mesh_render, "_rasterise_triangles", _spy)
        png = mesh_render.render_mesh_thumbnail(mesh, "big.stl", width=64, height=64)

        assert png is not None
        assert len(mesh.faces) > chunk  # the mesh really needed more than one chunk
        assert 0 < seen_max["n"] <= chunk


# ---------------------------------------------------------------------------
# Face-chunked rendering: peak memory is O(chunk_size), not O(total_faces),
# and the chunking is visually transparent (issue #29).
# ---------------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _set_chunk_size(monkeypatch, n: int) -> None:
    from app.core.config import _overlay

    monkeypatch.setitem(_overlay, "mesh_render_face_chunk_size", n)
