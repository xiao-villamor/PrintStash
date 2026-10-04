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
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image

from printstash_core.mesh import rasterizer, render_mesh_thumbnail
from printstash_core.mesh.rasterizer import (
    RasterBudget,
    RenderedPixels,
    RGBBackground,
    render_prepared_pixels,
    render_prepared_thumbnail,
)
from printstash_core.mesh.render_geometry import (
    prepare_mesh_render,
    prepare_scene_render,
)
from printstash_core.mesh.similarity import components
from printstash_core.mesh.similarity.components import (
    ExpandedScene,
    Instance,
    MeshResource,
)

from ..paths import FIXTURES_DIR

_FROZEN_RENDER = json.loads((FIXTURES_DIR / "render-prepared-v1.json").read_text())
_FROZEN_FRAMES = [
    pytest.param(case, view, id=f"{case['name']}-{index}")
    for case in _FROZEN_RENDER["cases"]
    for index, view in enumerate(case["views"])
]


@pytest.fixture
def frozen_frame(case, view):
    mesh = SimpleNamespace(
        vertices=np.array(case["vertices"], dtype=np.float64),
        faces=np.array(case["faces"], dtype=np.int64),
    )
    rotation = (
        None if view["frame"] is None else np.array(view["frame"], dtype=np.float64)
    )
    return mesh, dict(
        width=case["width"],
        height=case["height"],
        matte=case["matte"],
        view_rotation=rotation,
    )


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
    @pytest.mark.parametrize("unused_position", ["before", "after", "interleaved"])
    def test_ignores_unreferenced_vertices(self, unused_position: str) -> None:
        mesh = box_mesh()
        remote = np.array([[1e6, -1e6, 1e6]], dtype=np.float64)
        if unused_position == "before":
            vertices = np.concatenate([remote, mesh.vertices])
            faces = mesh.faces + 1
        elif unused_position == "after":
            vertices = np.concatenate([mesh.vertices, remote])
            faces = mesh.faces.copy()
        else:
            vertices = np.empty((16, 3), dtype=np.float64)
            vertices[::2] = mesh.vertices
            vertices[1::2] = remote
            faces = mesh.faces * 2
        padded = SimpleNamespace(vertices=vertices, faces=faces)

        expected = render_mesh_thumbnail(mesh, "cube", width=128, height=128)
        actual = render_mesh_thumbnail(padded, "cube", width=128, height=128)

        assert expected is not None and actual is not None
        assert np.count_nonzero(pixels(expected)[..., 3]) > 0
        np.testing.assert_array_equal(pixels(actual), pixels(expected))

    @pytest.mark.parametrize("thin_axis", [0, 1, 2])
    def test_ignores_unused_vertices_when_selecting_a_camera(
        self, thin_axis: int
    ) -> None:
        mesh = box_mesh()
        mesh.vertices[:, thin_axis] *= 0.01
        padded = SimpleNamespace(
            vertices=np.concatenate([mesh.vertices, [[1e6, -1e6, 1e6]]]),
            faces=mesh.faces,
        )

        expected = render_mesh_thumbnail(mesh, "plate", width=96, height=96)
        actual = render_mesh_thumbnail(padded, "plate", width=96, height=96)

        assert expected is not None and actual is not None
        np.testing.assert_array_equal(pixels(actual), pixels(expected))

    @pytest.mark.parametrize("chunk_size", [1, 7, 64_000])
    def test_remaps_referenced_vertices_in_face_chunks(self, chunk_size: int) -> None:
        mesh = box_mesh()
        padded = SimpleNamespace(
            vertices=np.concatenate([[[1e6, -1e6, 1e6]], mesh.vertices]),
            faces=mesh.faces + 1,
        )
        expected = render_mesh_thumbnail(
            mesh, "cube", width=64, height=64, view_rotation=np.eye(3), matte=True
        )

        actual = render_mesh_thumbnail(
            padded,
            "cube",
            width=64,
            height=64,
            face_chunk_size=chunk_size,
            view_rotation=np.eye(3),
            matte=True,
        )

        assert expected is not None and actual is not None
        np.testing.assert_array_equal(pixels(actual), pixels(expected))

    def test_retains_unreferenced_source_vertices(self) -> None:
        mesh = box_mesh()
        mesh.vertices = np.concatenate([[[1e6, -1e6, 1e6]], mesh.vertices])
        mesh.faces = mesh.faces + 1
        original_vertices, original_faces = mesh.vertices.copy(), mesh.faces.copy()
        mesh.vertices.flags.writeable = False
        mesh.faces.flags.writeable = False

        rendered = render_mesh_thumbnail(mesh, "cube", width=64, height=64)

        assert rendered is not None
        assert np.count_nonzero(pixels(rendered)[..., 3]) > 0
        np.testing.assert_array_equal(mesh.vertices, original_vertices)
        np.testing.assert_array_equal(mesh.faces, original_faces)

    def test_remaps_vertices_for_the_silhouette_fallback(self) -> None:
        mesh = inverted_plate()
        padded = SimpleNamespace(
            vertices=np.concatenate([[[1e6, -1e6, 1e6]], mesh.vertices]),
            faces=mesh.faces + 1,
        )
        expected = render_mesh_thumbnail(mesh, "plate", width=64, height=64)

        actual = render_mesh_thumbnail(padded, "plate", width=64, height=64)

        assert expected is not None and actual is not None
        assert np.count_nonzero(pixels(actual)[..., 3]) > 0
        np.testing.assert_array_equal(pixels(actual), pixels(expected))

    @pytest.mark.parametrize("invalid_index", [-1, 8, 100_000])
    def test_rejects_indices_outside_the_source(self, invalid_index: int) -> None:
        mesh = box_mesh()
        mesh.faces[0, 0] = invalid_index

        assert render_mesh_thumbnail(mesh, "invalid", width=64, height=64) is None

    @pytest.mark.parametrize("invalid_form", ["fractional", "rank", "corners"])
    def test_rejects_invalid_triangle_indices(self, invalid_form: str) -> None:
        mesh = box_mesh()
        if invalid_form == "fractional":
            mesh.faces = mesh.faces.astype(float)
            mesh.faces[0, 0] = 0.5
        elif invalid_form == "rank":
            mesh.faces = mesh.faces[0]
        else:
            mesh.faces = mesh.faces[:, :2]

        assert render_mesh_thumbnail(mesh, "invalid", width=64, height=64) is None

    @pytest.mark.parametrize(
        ("half_size", "offset"),
        [(5.0, 1e6), (5.0, 1e9), (0.125, 1e9), (512.0, 1e12), (1 / 2048, 1e8)],
        ids=[
            "ordinary-offset",
            "billion-offset",
            "small-cube",
            "trillion-offset",
            "tiny-cube",
        ],
    )
    def test_preserves_pixels_after_large_translations(self, half_size, offset):
        mesh = box_mesh()
        mesh.vertices *= half_size
        translated = SimpleNamespace(vertices=mesh.vertices + offset, faces=mesh.faces)

        original = render_mesh_thumbnail(mesh, "origin", width=128, height=128)
        shifted = render_mesh_thumbnail(translated, "translated", width=128, height=128)

        assert original is not None and shifted is not None
        assert np.count_nonzero(pixels(original)[..., 3]) > 0
        np.testing.assert_array_equal(pixels(shifted), pixels(original))

    @pytest.mark.parametrize(
        "camera",
        [None, np.eye(3), np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]])],
        ids=["hero", "front", "side"],
    )
    def test_preserves_camera_rendering_after_translation(self, camera):
        mesh = box_mesh()
        mesh.vertices *= [8, 4, 3]
        translated = SimpleNamespace(
            vertices=mesh.vertices + [1e9, -1e9, 1e9], faces=mesh.faces
        )

        original = render_mesh_thumbnail(
            mesh, "origin", width=64, height=64, view_rotation=camera
        )
        shifted = render_mesh_thumbnail(
            translated, "translated", width=64, height=64, view_rotation=camera
        )

        assert original is not None and shifted is not None
        np.testing.assert_array_equal(pixels(shifted), pixels(original))

    @pytest.mark.parametrize("thin_axis", [0, 1, 2], ids=["x", "y", "z"])
    def test_preserves_thin_mesh_rendering_after_translation(self, thin_axis):
        mesh = box_mesh()
        scale = np.full(3, 8.0)
        scale[thin_axis] = 0.125
        mesh.vertices *= scale
        translated = SimpleNamespace(vertices=mesh.vertices + 1e9, faces=mesh.faces)

        original = render_mesh_thumbnail(mesh, "origin", width=64, height=64)
        shifted = render_mesh_thumbnail(translated, "translated", width=64, height=64)

        assert original is not None and shifted is not None
        np.testing.assert_array_equal(pixels(shifted), pixels(original))

    def test_preserves_source_coordinates(self):
        mesh = box_mesh()
        mesh.vertices += 1e9
        original = mesh.vertices.copy()
        mesh.vertices.flags.writeable = False

        rendered = render_mesh_thumbnail(mesh, "immutable-source", width=64, height=64)

        assert rendered is not None
        np.testing.assert_array_equal(mesh.vertices, original)

    def test_does_not_invent_detail_lost_from_float32_source(self):
        mesh = box_mesh()
        mesh.vertices = (mesh.vertices * 5 + 1e9).astype(np.float32)

        rendered = render_mesh_thumbnail(mesh, "quantized-source", width=64, height=64)

        assert rendered is not None
        assert np.count_nonzero(pixels(rendered)[..., 3]) == 0

    def test_renders_an_explicit_orthographic_view(self) -> None:
        mesh = box_mesh()
        mesh.vertices[:, 0] *= 2

        png = render_mesh_thumbnail(
            mesh,
            "orthographic",
            width=64,
            height=64,
            view_rotation=np.eye(3),
            matte=True,
        )

        assert png is not None
        alpha = pixels(png)[:, :, 3]
        ys, xs = np.nonzero(alpha >= 128)
        assert 1.9 < np.ptp(xs) / np.ptp(ys) < 2.1

    def test_rejects_a_nonorthogonal_camera(self) -> None:
        png = render_mesh_thumbnail(
            box_mesh(), "bad-camera", view_rotation=np.ones((3, 3))
        )

        assert png is None

    def test_renders_a_png_at_the_requested_size(self) -> None:
        png = render_mesh_thumbnail(box_mesh(), "box.stl", width=80, height=60)

        assert png is not None and png.startswith(PNG_MAGIC)
        assert Image.open(io.BytesIO(png)).size == (80, 60)

    def test_can_encode_the_canonical_webp_without_a_png_intermediate(self) -> None:
        webp = render_mesh_thumbnail(
            box_mesh(), "box.stl", width=80, height=60, output_format="WEBP"
        )

        assert webp is not None and webp.startswith(b"RIFF")
        with Image.open(io.BytesIO(webp)) as decoded:
            assert decoded.format == "WEBP"
            assert decoded.size == (80, 60)

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

    @pytest.mark.parametrize("copies", [2, 1000])
    def test_expands_exact_duplicate_triangles_once(self, copies: int) -> None:
        tri = np.array([[[2.0, 2.0, 0.0], [12.0, 2.0, 0.0], [2.0, 12.0, 0.0]]])
        expected_work, expected = self.paint(tri)

        actual_work, actual = self.paint(np.repeat(tri, copies, axis=0))

        assert actual_work == expected_work
        np.testing.assert_array_equal(actual, expected)

    def test_preserves_duplicate_work_under_a_raster_budget(self) -> None:
        tri = np.array([[[2.0, 2.0, 0.0], [12.0, 2.0, 0.0], [2.0, 12.0, 0.0]]])
        single_work, _ = self.paint(tri)
        budget = RasterBudget(limit=1000)

        actual_work, _ = self.paint(np.repeat(tri, 3, axis=0), budget=budget)

        assert actual_work == budget.used == single_work * 3

    def test_preserves_triangle_corner_order(self) -> None:
        tri = np.array([[[2.0, 2.0, 0.0], [12.0, 2.0, 0.0], [2.0, 12.0, 0.0]]])
        single_work, _ = self.paint(tri)
        reordered = np.concatenate([tri, np.roll(tri, 1, axis=1), tri[:, ::-1]])

        actual_work, _ = self.paint(reordered)

        assert actual_work == single_work * 3

    @pytest.mark.parametrize("reverse", [False, True])
    @pytest.mark.parametrize("chunk_size", [1, 3, 8])
    def test_keeps_the_first_duplicate_normals(
        self, reverse: bool, chunk_size: int
    ) -> None:
        # Distinct overlapping triangles must keep input order too: the smaller
        # X coordinate sorts first but must not steal a depth tie from its peer.
        first = [[2.0, 2.0, 0.0], [12.0, 2.0, 0.0], [2.0, 12.0, 0.0]]
        second = [[1.0, 2.0, 0.0], [11.0, 2.0, 0.0], [1.0, 12.0, 0.0]]
        tri = np.array([first, second, first, second])
        colors = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0], [1.0, 1.0, 0]])
        normals = np.repeat(colors[:, None, :], 3, axis=1)
        if reverse:
            tri, normals = tri[::-1], normals[::-1]
        original_tri, original_normals = tri.copy(), normals.copy()
        tri.flags.writeable = False
        normals.flags.writeable = False

        expected_img = np.zeros((16, 16, 3), dtype=np.uint8)
        expected_z = np.full((16, 16), np.inf)
        actual_img, actual_z = expected_img.copy(), expected_z.copy()
        rasterizer._rasterise_triangles(
            expected_img,
            expected_z,
            tri[:2],
            normals[:2],
            lambda n: n,
            np.full(3, 255),
            16,
            16,
        )
        for start in range(0, len(tri), chunk_size):
            rasterizer._rasterise_triangles(
                actual_img,
                actual_z,
                tri[start : start + chunk_size],
                normals[start : start + chunk_size],
                lambda n: n,
                np.full(3, 255),
                16,
                16,
            )

        np.testing.assert_array_equal(actual_img, expected_img)
        np.testing.assert_array_equal(actual_z, expected_z)
        np.testing.assert_array_equal(tri, original_tri)
        np.testing.assert_array_equal(normals, original_normals)

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

    def test_preserves_full_face_when_only_allocation_chunk_is_small(self) -> None:
        tri = np.array([[[0.0, 0.0, 0.0], [511.0, 0.0, 0.0], [0.0, 511.0, 0.0]]])
        budget = RasterBudget(limit=1_000_000)

        painted, img = self.paint(tri, size=512, budget=budget)

        assert painted == budget.used == 512 * 512
        assert img[10, 10].tolist() == [255, 255, 255]


class TestRasterBudget:
    def test_preserves_default_cumulative_pixel_budget(self) -> None:
        budget = RasterBudget()

        assert budget.used == 0
        assert budget.limit == 1_000_000


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


class TestRenderSceneThumbnail:
    @pytest.mark.parametrize("spacing,chunk", [(0, 5), (3, 5), (3, 1000)])
    def test_preserves_placed_mesh_pixels_without_materialization(
        self, spacing, chunk, monkeypatch
    ):
        source = box_mesh()
        transforms = np.tile(np.eye(4), (64, 1, 1))
        transforms[1::2, 0, 0] = -2
        transforms[:, :3, 3] = np.arange(64)[:, None] * [spacing, spacing, 0]
        scene = ExpandedScene(
            (MeshResource("part", source.vertices, source.faces),),
            tuple(Instance("part", transform) for transform in transforms),
        )
        vertices, faces = components.compose_scene(scene)
        expected = render_mesh_thumbnail(
            SimpleNamespace(vertices=vertices, faces=faces),
            "placed",
            width=160,
            height=120,
            face_chunk_size=chunk,
        )
        originals = (
            source.vertices.tobytes(),
            source.faces.tobytes(),
            transforms.tobytes(),
        )

        def materialize(*_args, **_kwargs):
            raise AssertionError("scene rendering must not materialize source geometry")

        monkeypatch.setattr(components, "compose_scene", materialize)
        actual = rasterizer.render_scene_thumbnail(
            scene, "placed", width=160, height=120, face_chunk_size=chunk
        )

        assert expected is not None
        assert actual is not None
        np.testing.assert_array_equal(pixels(actual), pixels(expected))
        assert (
            source.vertices.tobytes(),
            source.faces.tobytes(),
            transforms.tobytes(),
        ) == originals

    @pytest.mark.parametrize("offset", [1e9, -1e9])
    def test_preserves_scene_pixels_after_large_translation(self, offset):
        source = box_mesh()
        transforms = np.tile(np.eye(4), (2, 1, 1))
        transforms[1, :3, 3] = [3, 4, 5]
        scene = ExpandedScene(
            (MeshResource("part", source.vertices / 100, source.faces),),
            tuple(Instance("part", transform) for transform in transforms),
        )
        translated = transforms.copy()
        translated[:, :3, 3] += offset
        distant = ExpandedScene(
            scene.resources,
            tuple(Instance("part", transform) for transform in translated),
        )

        original = rasterizer.render_scene_thumbnail(
            scene, "near", width=160, height=120
        )
        shifted = rasterizer.render_scene_thumbnail(
            distant, "far", width=160, height=120
        )

        assert original is not None
        assert shifted is not None
        np.testing.assert_array_equal(pixels(shifted), pixels(original))

    def test_ignores_unused_scene_coordinates(self):
        source = box_mesh()
        regular = ExpandedScene(
            (MeshResource("part", source.vertices, source.faces),),
            (Instance("part", np.eye(4)),),
        )
        unused = ExpandedScene(
            (
                MeshResource(
                    "part", np.vstack((source.vertices, [1e9, -1e9, 1e9])), source.faces
                ),
            ),
            regular.instances,
        )

        expected = rasterizer.render_scene_thumbnail(
            regular, "regular", width=160, height=120
        )
        actual = rasterizer.render_scene_thumbnail(
            unused, "unused", width=160, height=120
        )

        assert expected is not None
        assert actual is not None
        np.testing.assert_array_equal(pixels(actual), pixels(expected))

    def test_keeps_silhouette_fallback_for_scene(self):
        source = inverted_plate()
        scene = ExpandedScene(
            (MeshResource("part", source.vertices, source.faces),),
            (Instance("part", np.eye(4)),),
        )
        expected = render_mesh_thumbnail(source, "inverted", width=160, height=120)

        actual = rasterizer.render_scene_thumbnail(
            scene, "inverted", width=160, height=120
        )

        assert expected is not None
        assert actual is not None
        np.testing.assert_array_equal(pixels(actual), pixels(expected))

    def test_returns_nothing_for_empty_scene(self):
        assert rasterizer.render_scene_thumbnail(ExpandedScene((), ()), "empty") is None


@pytest.fixture
def prepared_box():
    prepared = prepare_mesh_render(box_mesh(), face_chunk_size=5)
    assert prepared is not None
    return prepared


class TestRenderedPixels:
    @pytest.mark.parametrize(
        "background",
        [RGBBackground.IGNORE_ALPHA, RGBBackground.WHITE],
        ids=["ignore-alpha", "white"],
    )
    def test_preserves_declared_rgb_background(self, background):
        rgba = bytes([10, 20, 30, 0, 70, 80, 90, 127, 120, 130, 140, 255])
        image = Image.frombytes("RGBA", (3, 1), rgba)
        expected = {
            RGBBackground.IGNORE_ALPHA: image.convert("RGB").tobytes(),
            RGBBackground.WHITE: Image.alpha_composite(
                Image.new("RGBA", image.size, (255, 255, 255, 255)), image
            )
            .convert("RGB")
            .tobytes(),
        }[background]
        result = RenderedPixels(width=3, height=1, rgba=rgba)
        assert result.rgb(background) == expected
        assert result.rgba == rgba

    @pytest.mark.parametrize(
        "width,height,rgba",
        [
            pytest.param(0, 1, b"", id="zero-width"),
            pytest.param(True, 1, bytes(4), id="boolean-width"),
            pytest.param(1, 1.0, bytes(4), id="float-height"),
            pytest.param(1, -1, bytes(4), id="negative-height"),
            pytest.param(1, 1, bytes(3), id="short-rgba"),
            pytest.param(1, 1, bytes(5), id="trailing-rgba"),
            pytest.param(1, 1, bytearray(4), id="mutable-rgba"),
        ],
    )
    def test_rejects_invalid_pixel_contract(self, width, height, rgba):
        with pytest.raises((ValueError, TypeError)):
            RenderedPixels(width=width, height=height, rgba=rgba)

    def test_rejects_unknown_background(self):
        result = RenderedPixels(width=1, height=1, rgba=bytes(4))
        with pytest.raises(TypeError):
            result.rgb("white")


class TestRenderPreparedPixels:
    @pytest.mark.parametrize(
        "view",
        [np.eye(3), np.eye(3)[[1, 2, 0]], np.diag([-1.0, 1, -1])],
        ids=["front", "side", "reversed"],
    )
    def test_preserves_existing_view_pixels(self, prepared_box, view):
        reference = render_mesh_thumbnail(
            box_mesh(),
            "legacy-view",
            width=80,
            height=60,
            face_chunk_size=5,
            view_rotation=view,
            matte=True,
        )
        actual = render_prepared_pixels(
            prepared_box,
            "prepared-view",
            width=80,
            height=60,
            face_chunk_size=5,
            view_rotation=view,
            matte=True,
        )
        assert reference is not None
        assert actual is not None
        assert (actual.width, actual.height) == (80, 60)
        assert actual.rgba == pixels(reference).tobytes()

    def test_preserves_cached_geometry_across_views(self, prepared_box):
        original = (
            prepared_box.vertices.tobytes(),
            prepared_box.position_ids.tobytes(),
            prepared_box.smooth_normals.tobytes(),
        )
        first = render_prepared_pixels(
            prepared_box,
            "first",
            width=80,
            height=60,
            view_rotation=np.eye(3),
            matte=True,
        )
        side = render_prepared_pixels(
            prepared_box,
            "side",
            width=80,
            height=60,
            view_rotation=np.eye(3)[[1, 2, 0]],
            matte=True,
        )
        repeated = render_prepared_pixels(
            prepared_box,
            "first-again",
            width=80,
            height=60,
            view_rotation=np.eye(3),
            matte=True,
        )
        assert first is not None
        assert side is not None
        assert repeated == first
        assert (
            prepared_box.vertices.tobytes(),
            prepared_box.position_ids.tobytes(),
            prepared_box.smooth_normals.tobytes(),
        ) == original

    def test_renders_without_image_codec_roundtrip(self, prepared_box, monkeypatch):
        reference = render_mesh_thumbnail(
            box_mesh(), "encoded", width=80, height=60, face_chunk_size=5
        )
        assert reference is not None
        expected = pixels(reference).tobytes()

        def codec(*args, **kwargs):
            raise AssertionError("direct pixels must not encode or decode an image")

        monkeypatch.setattr(Image.Image, "save", codec)
        monkeypatch.setattr(Image, "open", codec)
        actual = render_prepared_pixels(
            prepared_box, "direct", width=80, height=60, face_chunk_size=5
        )
        assert actual is not None
        assert actual.rgba == expected

    def test_reuses_normal_preparation_during_render(self, prepared_box, monkeypatch):
        reference = render_mesh_thumbnail(
            box_mesh(), "reference", width=80, height=60, face_chunk_size=5
        )
        assert reference is not None
        expected = pixels(reference).tobytes()

        def weld(*args, **kwargs):
            raise AssertionError(
                "view render must consume cached normals and position identities"
            )

        monkeypatch.setattr(np, "bincount", weld)
        actual = render_prepared_pixels(
            prepared_box, "cached", width=80, height=60, face_chunk_size=5
        )
        assert actual is not None
        assert actual.rgba == expected

    @pytest.mark.parametrize(
        "size", [1, 5, 1000], ids=["one", "cross-placement", "whole"]
    )
    def test_preserves_scene_pixels_across_face_chunks(self, size):
        mesh = box_mesh()
        reflected = np.diag([-2.0, 2, 2, 1])
        reflected[:3, 3] = [4, 2, 0]
        scene = ExpandedScene(
            (MeshResource("part", mesh.vertices, mesh.faces),),
            (Instance("part", np.eye(4)), Instance("part", reflected)),
        )
        prepared = prepare_scene_render(scene, face_chunk_size=size)
        reference = rasterizer.render_scene_thumbnail(
            scene, "scene-old", width=80, height=60, face_chunk_size=size
        )
        actual = render_prepared_pixels(
            prepared, "scene-direct", width=80, height=60, face_chunk_size=size
        )
        assert reference is not None
        assert actual is not None
        assert actual.rgba == pixels(reference).tobytes()

    def test_preserves_silhouette_recovery(self):
        prepared = prepare_mesh_render(inverted_plate())
        assert prepared is not None
        reference = render_mesh_thumbnail(
            inverted_plate(), "legacy-silhouette", width=80, height=60
        )
        actual = render_prepared_pixels(
            prepared, "prepared-silhouette", width=80, height=60
        )
        assert reference is not None
        assert actual is not None
        assert actual.rgba == pixels(reference).tobytes()

    def test_refuses_invalid_camera(self, prepared_box):
        assert (
            render_prepared_pixels(
                prepared_box, "bad-camera", view_rotation=np.ones((3, 3))
            )
            is None
        )

    def test_preserves_renderer_failure_result(self, prepared_box):
        def broken(*args, **kwargs):
            raise ArithmeticError("raster_failed")

        assert (
            render_prepared_pixels(
                prepared_box, "broken", width=48, height=48, rasterise_triangles=broken
            )
            is None
        )


class TestRenderPreparedThumbnail:
    @pytest.mark.parametrize("format", ["PNG", "WEBP"], ids=["png", "webp"])
    def test_preserves_existing_encoded_bytes(self, prepared_box, format):
        reference = render_mesh_thumbnail(
            box_mesh(),
            "reference",
            width=80,
            height=60,
            face_chunk_size=5,
            output_format=format,
        )
        actual = render_prepared_thumbnail(
            prepared_box,
            "prepared",
            width=80,
            height=60,
            face_chunk_size=5,
            output_format=format,
        )
        assert reference is not None
        assert actual == reference


class TestFrozenPreparedRender:
    @pytest.mark.parametrize("case,view", _FROZEN_FRAMES)
    def test_preserves_frozen_legacy_pixels(self, case, view, frozen_frame):
        mesh, options = frozen_frame
        assert (
            _FROZEN_RENDER["provenance"]["source_commit"]
            == "59e91d25172ca53c47f26b1ec773992defddc57a"
        )
        assert (
            hashlib.sha256(mesh.vertices.tobytes()).hexdigest()
            == case["vertices_sha256"]
        )
        assert hashlib.sha256(mesh.faces.tobytes()).hexdigest() == case["faces_sha256"]
        prepared = prepare_mesh_render(mesh)
        assert prepared is not None
        actual = render_prepared_pixels(prepared, case["name"], **options)
        assert actual is not None
        ignored = actual.rgb(RGBBackground.IGNORE_ALPHA)
        white = actual.rgb(RGBBackground.WHITE)
        gray = (
            Image.frombytes("RGB", (actual.width, actual.height), ignored)
            .convert("L")
            .tobytes()
        )
        assert hashlib.sha256(actual.rgba).hexdigest() == view["rgba_sha256"]
        assert hashlib.sha256(ignored).hexdigest() == view["rgb_ignore_alpha_sha256"]
        assert hashlib.sha256(white).hexdigest() == view["rgb_white_sha256"]
        assert hashlib.sha256(gray).hexdigest() == view["gray_sha256"]

    @pytest.mark.parametrize("case,view", _FROZEN_FRAMES)
    def test_preserves_frozen_legacy_png(self, case, view, frozen_frame):
        mesh, options = frozen_frame
        prepared = prepare_mesh_render(mesh)
        assert prepared is not None
        actual = render_prepared_thumbnail(prepared, case["name"], **options)
        assert actual is not None
        assert hashlib.sha256(actual).hexdigest() == view["png_sha256"]
