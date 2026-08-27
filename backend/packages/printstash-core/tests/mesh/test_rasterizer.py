"""Standalone tests for the framework-neutral software mesh rasterizer."""

from __future__ import annotations

import ast
import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from printstash_core.mesh import rasterizer, render_mesh_thumbnail

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _box_mesh() -> SimpleNamespace:
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


def test_renderer_accepts_a_structural_mesh_without_trimesh() -> None:
    png = render_mesh_thumbnail(
        _box_mesh(),
        "box.stl",
        width=80,
        height=60,
        face_chunk_size=3,
    )

    assert png is not None and png.startswith(PNG_MAGIC)
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    assert image.size == (80, 60)
    assert np.asarray(image)[:, :, 3].max() == 255


def test_face_chunk_size_does_not_change_pixels() -> None:
    mesh = _box_mesh()

    one_chunk = render_mesh_thumbnail(mesh, "box.stl", face_chunk_size=10_000)
    many_chunks = render_mesh_thumbnail(mesh, "box.stl", face_chunk_size=1)

    assert one_chunk is not None and many_chunks is not None
    one = np.asarray(Image.open(io.BytesIO(one_chunk)).convert("RGBA"))
    many = np.asarray(Image.open(io.BytesIO(many_chunks)).convert("RGBA"))
    np.testing.assert_array_equal(one, many)


def test_rasterizer_is_injected_for_each_bounded_face_chunk() -> None:
    seen: list[int] = []

    def spy(*args: object) -> None:
        triangles = args[2]
        seen.append(int(triangles.shape[0]))
        rasterizer._rasterise_triangles(*args)

    png = render_mesh_thumbnail(
        _box_mesh(),
        "box.stl",
        width=48,
        height=48,
        face_chunk_size=2,
        rasterise_triangles=spy,
    )

    assert png is not None
    assert seen
    assert max(seen) <= 2


def test_flat_mesh_uses_its_broad_face_for_the_view() -> None:
    vertices = np.array(
        [
            [-5.0, -5.0, -0.2],
            [5.0, -5.0, -0.2],
            [5.0, 5.0, 0.2],
            [-5.0, 5.0, 0.2],
        ],
        dtype=np.float64,
    )

    rotation = rasterizer._select_view_rotation(vertices)

    tilt = np.radians(25.0)
    cosine, sine = np.cos(tilt), np.sin(tilt)
    expected = np.array(
        [[1, 0, 0], [0, cosine, -sine], [0, sine, cosine]],
        dtype=np.float64,
    ) @ np.diag([1.0, 1.0, -1.0])
    np.testing.assert_allclose(rotation, expected, atol=1e-12)


def test_solid_mesh_uses_a_proper_z_up_rotation() -> None:
    vertices = _box_mesh().vertices

    rotation = rasterizer._select_view_rotation(vertices)

    np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-9)
    assert np.linalg.det(rotation) > 0.99
    z_on_screen = rotation @ np.array([0.0, 0.0, 1.0])
    assert z_on_screen[1] > 0.8
    assert abs(z_on_screen[0]) < 0.2


class _RecordingLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: object, *args: object) -> None:
        self.errors.append(str(msg) % args if args else str(msg))

    def warning(
        self,
        msg: object,
        *args: object,
        exc_info: bool = False,
    ) -> None:
        self.warnings.append(str(msg) % args if args else str(msg))


def test_empty_mesh_returns_none_and_uses_only_injected_logging() -> None:
    log = _RecordingLogger()
    mesh = SimpleNamespace(
        vertices=np.empty((0, 3), dtype=np.float64),
        faces=np.empty((0, 3), dtype=np.int64),
    )

    assert render_mesh_thumbnail(mesh, "empty.stl", logger=log) is None
    assert log.errors == []
    assert log.warnings == ["mesh_render: empty mesh for empty.stl"]


def test_runtime_module_has_no_framework_loader_or_cascadio_imports() -> None:
    source_path = Path(rasterizer.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])

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
