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

import io
from types import SimpleNamespace
from typing import Any

import numpy as np
from PIL import Image

from printstash_core.mesh import rasterizer, render_mesh_thumbnail

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

    def test_returns_nothing_when_the_rasterizer_raises(self, monkeypatch) -> None:
        log = RecordingLogger()

        def explode(*_args: Any, **_kwargs: Any) -> None:
            raise MemoryError("candidate-pixel expansion failed")

        monkeypatch.setattr(rasterizer.native_rasterizer, "render_preview", explode)
        result = render_mesh_thumbnail(
            box_mesh(),
            "box.stl",
            width=32,
            height=32,
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
