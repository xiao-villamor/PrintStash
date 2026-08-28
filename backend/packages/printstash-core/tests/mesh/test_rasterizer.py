"""The software mesh rasterizer: what it must render, and what it must survive.

Every model thumbnail in a PrintStash library comes out of this module, and it is
deliberately pure NumPy and Pillow — no GL, no display, no Rust toolchain — so a
source install and a Docker build need nothing extra. The dependency-boundary
test at the bottom of this file is the enforcement of that: an accidental
`import trimesh` or `import cascadio` here would make the thumbnail path
un-installable for the profiles this module is documented to support.

Two properties matter more than the pixels.

**It must never raise.** A thumbnail is a nicety; an upload is not. Every failure
path — a mesh with no faces, a mesh whose triangles are all degenerate, NumPy
missing entirely, the rasterizer itself blowing up — has to return `None` and log,
so the caller falls back to the embedded preview and the upload still succeeds.

**Memory has to stay bounded.** A million-triangle mesh is an ordinary thing for
a library to contain, and the per-face arrays are the largest allocation in the
process. Faces are therefore processed one chunk at a time, and candidate-pixel
expansion is capped per chunk. The tests here pin the *observable* consequences:
chunk size must not change a single pixel, and no chunk may exceed its bound.

View selection is the third concern and a purely aesthetic one, except that
getting it wrong makes a flat model (a badge, a sign, a lithophane) render as an
unrecognisable edge-on sliver. Flat meshes are framed face-on; solid ones get the
3/4 hero angle the interactive viewer opens with.
"""

from __future__ import annotations

import ast
import builtins
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image

from printstash_core.mesh import rasterizer, render_mesh_thumbnail
from printstash_core.mesh.rasterizer import RasterBudget

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# 25°, the tilt a flat mesh is viewed at so recesses read.
FLAT_TILT = np.radians(25.0)


def box_mesh() -> SimpleNamespace:
    """A closed unit cube — the simplest mesh with a genuine interior."""

    vertices = np.array(
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
    faces = np.array(
        [
            [0, 2, 1],
            [0, 3, 2],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [1, 2, 6],
            [1, 6, 5],
            [2, 3, 7],
            [2, 7, 6],
            [3, 0, 4],
            [3, 4, 7],
        ],
        dtype=np.int64,
    )
    return SimpleNamespace(vertices=vertices, faces=faces)


def flat_mesh(thin_axis: int) -> np.ndarray:
    """A plate 20 units across and 0.4 thick along `thin_axis`."""

    corners = np.array(
        [[-10.0, -10.0], [10.0, -10.0], [10.0, 10.0], [-10.0, 10.0]],
        dtype=np.float64,
    )
    thickness = np.array([-0.2, -0.2, 0.2, 0.2], dtype=np.float64)
    columns = [corners[:, 0], corners[:, 1]]
    columns.insert(thin_axis, thickness)
    return np.stack(columns, axis=1)


def inverted_plate() -> SimpleNamespace:
    """A flat plate wound the wrong way round — every face points away."""

    vertices = np.array(
        [
            [-10.0, -10.0, 0.0],
            [10.0, -10.0, 0.0],
            [10.0, 10.0, 0.0],
            [-10.0, 10.0, 0.0],
        ],
        dtype=np.float64,
    )
    return SimpleNamespace(
        vertices=vertices, faces=np.array([[2, 1, 0], [3, 2, 0]], dtype=np.int64)
    )


def pixels(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


class RecordingLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: object, *args: object) -> None:
        self.errors.append(str(msg) % args if args else str(msg))

    def warning(self, msg: object, *args: object, exc_info: bool = False) -> None:
        self.warnings.append(str(msg) % args if args else str(msg))


class TestRenderMeshThumbnail:
    def test_renders_a_png_at_the_requested_size(self) -> None:
        png = render_mesh_thumbnail(box_mesh(), "box.stl", width=80, height=60)

        assert png is not None and png.startswith(PNG_MAGIC)
        assert Image.open(io.BytesIO(png)).size == (80, 60)

    def test_accepts_a_mesh_through_the_structural_interface(self) -> None:
        # `SimpleNamespace` with `vertices`/`faces` — not a Trimesh object.
        # Trimesh is not importable from this module by design, so callers that
        # already loaded geometry another way still get a thumbnail.
        png = render_mesh_thumbnail(box_mesh(), "box.stl", width=48, height=48)

        assert png is not None

    def test_paints_the_model_opaque(self) -> None:
        png = render_mesh_thumbnail(box_mesh(), "box.stl", width=48, height=48)

        assert png is not None
        assert pixels(png)[:, :, 3].max() == 255

    def test_leaves_the_background_transparent(self) -> None:
        png = render_mesh_thumbnail(box_mesh(), "box.stl", width=64, height=64)

        assert png is not None
        # The thumbnail sits on the library's own background, which is a
        # different colour in light and dark themes.
        assert pixels(png)[0, 0, 3] == 0

    def test_renders_the_same_pixels_at_every_face_chunk_size(self) -> None:
        mesh = box_mesh()

        one_chunk = render_mesh_thumbnail(mesh, "box.stl", face_chunk_size=10_000)
        many_chunks = render_mesh_thumbnail(mesh, "box.stl", face_chunk_size=1)

        # Chunking exists purely to bound memory. If it changed the image, the
        # memory limit would be trading correctness for RSS.
        assert one_chunk is not None and many_chunks is not None
        np.testing.assert_array_equal(pixels(one_chunk), pixels(many_chunks))

    def test_never_hands_the_rasterizer_more_faces_than_the_chunk_size(self) -> None:
        seen: list[int] = []

        def spy(*args: Any) -> None:
            seen.append(int(args[2].shape[0]))
            rasterizer._rasterise_triangles(*args)

        png = render_mesh_thumbnail(
            box_mesh(),
            "box.stl",
            width=48,
            height=48,
            face_chunk_size=2,
            rasterise_triangles=spy,
        )

        # The per-face arrays are the largest allocation in the process; the
        # chunk size is what keeps a million-triangle mesh from materialising
        # them whole.
        assert png is not None
        assert seen and max(seen) <= 2

    def test_renders_a_single_triangle(self) -> None:
        mesh = SimpleNamespace(
            vertices=np.array(
                [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64
            ),
            faces=np.array([[0, 1, 2]], dtype=np.int64),
        )

        png = render_mesh_thumbnail(mesh, "tri.stl", width=32, height=32)

        assert png is not None and png.startswith(PNG_MAGIC)

    def test_returns_nothing_for_a_missing_mesh(self) -> None:
        log = RecordingLogger()

        assert render_mesh_thumbnail(None, "gone.stl", logger=log) is None
        assert log.warnings == ["mesh_render: empty mesh for gone.stl"]

    def test_returns_nothing_for_a_mesh_with_no_vertices(self) -> None:
        log = RecordingLogger()
        mesh = SimpleNamespace(
            vertices=np.empty((0, 3), dtype=np.float64),
            faces=np.empty((0, 3), dtype=np.int64),
        )

        assert render_mesh_thumbnail(mesh, "empty.stl", logger=log) is None
        assert log.errors == []

    def test_returns_nothing_for_a_mesh_with_no_faces(self) -> None:
        log = RecordingLogger()
        mesh = SimpleNamespace(
            vertices=np.zeros((3, 3), dtype=np.float64),
            faces=np.empty((0, 3), dtype=np.int64),
        )

        assert render_mesh_thumbnail(mesh, "points.stl", logger=log) is None
        assert log.warnings == ["mesh_render: empty mesh for points.stl"]

    def test_returns_nothing_for_a_mesh_whose_faces_are_none(self) -> None:
        mesh = SimpleNamespace(vertices=np.zeros((3, 3), dtype=np.float64), faces=None)

        assert render_mesh_thumbnail(mesh, "broken.stl") is None

    def test_returns_nothing_without_a_logger_rather_than_raising(self) -> None:
        # The logger is optional, and a caller that omits it still must not have
        # an upload fail because a thumbnail could not be produced.
        assert render_mesh_thumbnail(None, "gone.stl") is None

    def test_reports_a_missing_numpy_or_pillow_as_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = RecordingLogger()
        real_import = builtins.__import__

        def without_numpy(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "numpy":
                raise ImportError("no numpy in this environment")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", without_numpy)

        assert render_mesh_thumbnail(box_mesh(), "box.stl", logger=log) is None
        assert log.errors == [
            "mesh_render: numpy/Pillow unavailable; cannot render thumbnail"
        ]

    def test_returns_nothing_when_the_rasterizer_raises(self) -> None:
        log = RecordingLogger()

        def explode(*_args: Any, **_kwargs: Any) -> None:
            raise MemoryError("candidate-pixel expansion failed")

        result = render_mesh_thumbnail(
            box_mesh(),
            "box.stl",
            width=32,
            height=32,
            rasterise_triangles=explode,
            logger=log,
        )

        # An OOM or an arithmetic surprise inside the render must not propagate
        # into the request that uploaded the file.
        assert result is None
        assert log.warnings == ["mesh_render: render_thumbnail failed for box.stl"]

    def test_still_paints_a_mesh_whose_winding_is_inverted(self) -> None:
        png = render_mesh_thumbnail(
            inverted_plate(), "inverted.stl", width=48, height=48
        )

        # An STL exported with reversed winding is common in the wild, and every
        # one of its faces is back-facing. Painting the silhouette flat beats
        # returning no thumbnail at all.
        assert png is not None and png.startswith(PNG_MAGIC)
        assert pixels(png)[:, :, 3].max() == 255

    def test_names_the_file_when_it_falls_back_to_a_silhouette(self) -> None:
        log = RecordingLogger()

        render_mesh_thumbnail(
            inverted_plate(), "inverted.stl", width=48, height=48, logger=log
        )

        assert log.warnings == [
            "mesh_render: no visible triangles for inverted.stl — using silhouette"
        ]

    def test_still_produces_a_png_for_a_mesh_of_zero_area_triangles(self) -> None:
        log = RecordingLogger()
        collinear = SimpleNamespace(
            vertices=np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 0.0], [2.0, 2.0, 0.0]]),
            faces=np.array([[0, 1, 2]], dtype=np.int64),
        )

        png = render_mesh_thumbnail(
            collinear, "collinear.stl", width=32, height=32, logger=log
        )

        # Nothing can be painted, but the caller gets a valid (empty) PNG rather
        # than an exception out of the barycentric divide.
        assert png is not None and png.startswith(PNG_MAGIC)
        assert pixels(png)[:, :, 3].max() == 0


class TestSelectViewRotation:
    def test_frames_a_solid_mesh_with_z_up_on_screen(self) -> None:
        rotation = rasterizer._select_view_rotation(box_mesh().vertices)

        # 3D-print models are Z-up because they sit flat on the bed. A view that
        # stared down the Z axis showed the top of an upright model instead of
        # its face.
        z_on_screen = rotation @ np.array([0.0, 0.0, 1.0])
        assert z_on_screen[1] > 0.8
        assert abs(z_on_screen[0]) < 0.2

    def test_returns_a_proper_rotation_for_a_solid_mesh(self) -> None:
        rotation = rasterizer._select_view_rotation(box_mesh().vertices)

        # Orthonormal with positive determinant: no reflection, no scaling, so
        # the model is not mirrored or stretched in the thumbnail.
        np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-9)
        assert np.linalg.det(rotation) > 0.99

    def test_views_a_flat_mesh_face_on(self) -> None:
        rotation = rasterizer._select_view_rotation(flat_mesh(thin_axis=2))

        cosine, sine = np.cos(FLAT_TILT), np.sin(FLAT_TILT)
        expected = np.array(
            [[1, 0, 0], [0, cosine, -sine], [0, sine, cosine]], dtype=np.float64
        ) @ np.diag([1.0, 1.0, -1.0])
        # A badge or a sign viewed at the hero angle renders as an
        # unrecognisable edge-on sliver.
        np.testing.assert_allclose(rotation, expected, atol=1e-12)

    @pytest.mark.parametrize("thin_axis", [0, 1, 2])
    def test_looks_along_whichever_axis_is_thin(self, thin_axis: int) -> None:
        rotation = rasterizer._select_view_rotation(flat_mesh(thin_axis))

        # The thin axis has to end up pointing at the camera (screen Z) whether
        # the model was exported lying down, standing up, or on its side.
        thin_direction = np.zeros(3)
        thin_direction[thin_axis] = 1.0
        assert abs((rotation @ thin_direction)[2]) > 0.9

    def test_uses_the_hero_view_for_a_mesh_with_no_extent(self) -> None:
        degenerate = np.zeros((3, 3), dtype=np.float64)

        rotation = rasterizer._select_view_rotation(degenerate)

        # A single point has no broad face to frame, so the flat-mesh branch
        # must not divide by its zero extent.
        assert np.linalg.det(rotation) > 0.99


class TestFrontRotationForThinAxis:
    @pytest.mark.parametrize("thin_axis", [0, 1, 2])
    def test_returns_a_proper_rotation_for_every_axis(self, thin_axis: int) -> None:
        rotation = rasterizer._front_rotation_for_thin_axis(thin_axis)

        np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)

    @pytest.mark.parametrize("thin_axis", [0, 1])
    def test_keeps_the_model_upright_for_a_standing_plate(self, thin_axis: int) -> None:
        rotation = rasterizer._front_rotation_for_thin_axis(thin_axis)

        # A plate standing on the bed is thin in X or Y; object Z must stay
        # screen-up or the thumbnail is sideways.
        assert (rotation @ np.array([0.0, 0.0, 1.0]))[1] > 0.8


class TestRasteriseTriangles:
    def paint(
        self,
        tri: np.ndarray,
        *,
        size: int = 16,
        budget: RasterBudget | None = None,
    ) -> tuple[int, np.ndarray]:
        img = np.zeros((size, size, 3), dtype=np.uint8)
        zbuf = np.full((size, size), np.inf, dtype=np.float64)
        normals = np.tile(np.array([0.0, 0.0, -1.0]), (tri.shape[0], 3, 1))

        def shade(n: np.ndarray) -> np.ndarray:
            return np.ones_like(n)

        painted = rasterizer._rasterise_triangles(
            img,
            zbuf,
            tri,
            normals,
            shade,
            # White on the 8-bit scale: `shade` returns absolute colour in
            # [0, 1] and the rasterizer's multiply only scales it up.
            np.array([255.0, 255.0, 255.0]),
            size,
            size,
            budget=budget,
        )
        return int(painted or 0), img

    def test_paints_the_pixels_a_triangle_covers(self) -> None:
        tri = np.array([[[2.0, 2.0, 0.0], [12.0, 2.0, 0.0], [2.0, 12.0, 0.0]]])

        painted, img = self.paint(tri)

        assert painted > 0
        assert img[4, 4].tolist() == [255, 255, 255]

    def test_leaves_pixels_outside_the_triangle_alone(self) -> None:
        tri = np.array([[[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]]])

        _painted, img = self.paint(tri)

        assert img[15, 15].tolist() == [0, 0, 0]

    def test_paints_nothing_for_an_empty_batch(self) -> None:
        painted, img = self.paint(np.empty((0, 3, 3), dtype=np.float64))

        assert painted == 0
        assert img.max() == 0

    def test_skips_a_triangle_with_no_area(self) -> None:
        # Three collinear points. Tessellated meshes contain these, and the
        # barycentric denominator is zero for them — dividing would produce
        # NaN coordinates and paint garbage across the frame.
        tri = np.array([[[1.0, 1.0, 0.0], [5.0, 5.0, 0.0], [9.0, 9.0, 0.0]]])

        painted, img = self.paint(tri)

        assert painted == 0
        assert img.max() == 0

    def test_keeps_the_nearer_of_two_overlapping_triangles(self) -> None:
        near = [[2.0, 2.0, -1.0], [12.0, 2.0, -1.0], [2.0, 12.0, -1.0]]
        far = [[2.0, 2.0, 5.0], [12.0, 2.0, 5.0], [2.0, 12.0, 5.0]]
        img = np.zeros((16, 16, 3), dtype=np.uint8)
        zbuf = np.full((16, 16), np.inf, dtype=np.float64)
        tri = np.array([near, far])
        normals = np.tile(np.array([0.0, 0.0, -1.0]), (2, 3, 1))

        rasterizer._rasterise_triangles(
            img,
            zbuf,
            tri,
            normals,
            lambda n: np.ones_like(n),
            np.array([255.0, 255.0, 255.0]),
            16,
            16,
        )

        # Painted back-to-front in array order, so only a working z-buffer
        # keeps the near surface visible.
        assert zbuf[4, 4] < 0

    def test_stops_when_a_shared_budget_is_exhausted(self) -> None:
        tri = np.array([[[2.0, 2.0, 0.0], [12.0, 2.0, 0.0], [2.0, 12.0, 0.0]]])

        painted, img = self.paint(tri, budget=RasterBudget(limit=0, used=0))

        # The budget is cumulative across every rasterizer call in one render,
        # so an exhausted one has to stop rather than allocate anyway.
        assert painted == 0
        assert img.max() == 0

    def test_charges_the_pixels_it_paints_to_the_shared_budget(self) -> None:
        tri = np.array([[[2.0, 2.0, 0.0], [12.0, 2.0, 0.0], [2.0, 12.0, 0.0]]])
        budget = RasterBudget(limit=10_000)

        self.paint(tri, budget=budget)

        assert budget.used > 0

    def test_renders_a_giant_triangle_as_a_centered_tile(self) -> None:
        # One triangle covering the whole frame, with a budget too small for it.
        # Dropping the face outright would leave a hole; a centered tile of the
        # affordable size keeps the silhouette readable within the cap.
        tri = np.array([[[32.0, 0.0, 0.0], [0.0, 63.0, 0.0], [63.0, 63.0, 0.0]]])

        painted, img = self.paint(tri, size=64, budget=RasterBudget(limit=64))

        assert 0 < painted <= 64
        assert img.max() > 0


class TestRasterBudget:
    def test_starts_unspent_at_the_chunk_cap(self) -> None:
        budget = RasterBudget()

        assert budget.used == 0
        assert budget.limit == rasterizer._CHUNK_PIXEL_BUDGET


class TestModuleDependencies:
    def test_imports_no_framework_storage_or_tessellation_package(self) -> None:
        tree = ast.parse(Path(rasterizer.__file__).read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])

        # This module is documented as installable with nothing but NumPy and
        # Pillow, across two dependency profiles, and as movable wholesale into
        # a separate thumbnail worker. Any of these imports breaks both claims.
        assert roots.isdisjoint(
            {
                "app",
                "cascadio",
                "fastapi",
                "sqlalchemy",
                "sqlmodel",
                "storage",
                "trimesh",
            }
        )
