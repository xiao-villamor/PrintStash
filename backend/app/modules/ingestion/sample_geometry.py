"""Small printable solids for the sample collection."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class SampleMesh:
    vertices: np.ndarray
    faces: np.ndarray

    def to_stl(self, header: bytes) -> bytes:
        """Serialize a small generated solid directly as binary STL."""
        triangles = self.vertices[self.faces]
        normals = np.cross(
            triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        )
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        facets = np.zeros(
            len(triangles),
            dtype=[
                ("normal", "<f4", (3,)),
                ("vertices", "<f4", (3, 3)),
                ("attributes", "<u2"),
            ],
        )
        facets["normal"] = normals
        facets["vertices"] = triangles
        return struct.pack("<80sI", header, len(facets)) + facets.tobytes()


def _relief(heights: np.ndarray, layer_edges: tuple[float, ...]) -> SampleMesh:
    """Build the exposed faces of a stepped solid, sharing vertices at seams."""
    faces: list[tuple[int, int, int]] = []
    vertices: dict[tuple[float, float, float], int] = {}

    def quad(*points: tuple[float, float, float]) -> None:
        a, b, c, d = (vertices.setdefault(point, len(vertices)) for point in points)
        faces.extend(((a, b, c), (a, c, d)))

    width, depth = heights.shape
    for x in range(width):
        for y in range(depth):
            layers = int(heights[x, y])
            if not layers:
                continue
            x1, y1 = x + 1, y + 1
            z0, top = layer_edges[0], layer_edges[layers]
            quad((x, y, z0), (x, y1, z0), (x1, y1, z0), (x1, y, z0))
            quad((x, y, top), (x1, y, top), (x1, y1, top), (x, y1, top))
            for index, (low, high) in enumerate(
                zip(layer_edges[:layers], layer_edges[1 : layers + 1], strict=True),
                start=1,
            ):
                if x == 0 or heights[x - 1, y] < index:
                    quad((x, y, low), (x, y, high), (x, y1, high), (x, y1, low))
                if x1 == width or heights[x1, y] < index:
                    quad((x1, y, low), (x1, y1, low), (x1, y1, high), (x1, y, high))
                if y == 0 or heights[x, y - 1] < index:
                    quad((x, y, low), (x1, y, low), (x1, y, high), (x, y, high))
                if y1 == depth or heights[x, y1] < index:
                    quad((x, y1, low), (x, y1, high), (x1, y1, high), (x1, y1, low))
    return SampleMesh(
        np.asarray(list(vertices), dtype=np.float64), np.asarray(faces, dtype=np.int64)
    )


def _logo_plaque() -> SampleMesh:
    # Trace the white monogram from frontend/public/logo.svg in its original
    # coordinate system. Sample the arc and cubic filament stroke before extrusion.
    image = Image.new("1", (56, 56))
    draw = ImageDraw.Draw(image)

    def point(x: float, y: float) -> tuple[float, float]:
        return (x - 260) / 8, (y - 106) / 8

    def stroke(points: list[tuple[float, float]], width: int) -> None:
        draw.line(points, width=width)
        radius = width / 2
        for x, y in points:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=1)

    arc = [
        point(485 + 80 * math.cos(angle), 245 + 80 * math.sin(angle))
        for angle in np.linspace(-math.pi / 2, math.pi / 2, 33)
    ]
    stroke([point(397, 350), point(397, 165), *arc, point(397, 325)], 7)
    filament = [point(397, 450)]
    for controls in (
        ((397, 470), (381, 480), (363, 480)),
        ((345, 480), (335, 490), (335, 502)),
        ((335, 514), (347, 520), (361, 520)),
    ):
        start = filament[-1]
        points = [start, *(point(*control) for control in controls)]
        for t in np.linspace(0, 1, 9)[1:]:
            weights = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3)
            x, y = np.asarray(weights) @ np.asarray(points)
            filament.append((float(x), float(y)))
    stroke([*filament, point(492, 520)], 4)
    draw.rounded_rectangle((*point(335, 330), *point(459, 378)), radius=2, fill=1)
    draw.rounded_rectangle((*point(375, 382), *point(419, 404)), radius=1, fill=1)
    draw.polygon(
        [
            point(x, y)
            for x, y in [(367, 404), (427, 404), (409, 432), (397, 442), (385, 432)]
        ],
        fill=1,
    )
    heights = 1 + np.asarray(image, dtype=np.uint8)[::-1].T
    return _relief(heights, (0.0, 2.0, 3.2))


def sample_models() -> tuple[tuple[str, SampleMesh], ...]:
    tray = np.ones((48, 32), dtype=np.uint8)
    tray[:2, :] = tray[-2:, :] = tray[:, :2] = tray[:, -2:] = 2
    unit = _relief(np.ones((1, 1), dtype=np.uint8), (0.0, 20.0))
    cube = SampleMesh(unit.vertices * (20, 20, 1), unit.faces)
    return (
        ("PrintStash logo plaque", _logo_plaque()),
        ("20 mm calibration cube", cube),
        ("Small parts tray", _relief(tray, (0.0, 2.0, 8.0))),
    )
