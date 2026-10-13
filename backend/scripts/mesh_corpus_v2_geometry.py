"""Small analytic meshes and bounded STL writers for the v2 benchmark corpus."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import BinaryIO

Point = tuple[float, float, float]
Face = tuple[int, int, int]
WRITE_CHUNK_BYTES = 64 * 1024
MAX_GRID_FACES = 2_000_000


@dataclass(frozen=True)
class Mesh:
    vertices: tuple[Point, ...]
    faces: tuple[Face, ...]


def box(*, size: Point = (20, 20, 20), offset: Point = (0, 0, 0)) -> Mesh:
    corners = (
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
    )
    faces = (
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (3, 7, 6),
        (3, 6, 2),
        (0, 4, 7),
        (0, 7, 3),
        (1, 2, 6),
        (1, 6, 5),
    )
    return Mesh(
        tuple(
            (x * size[0] + offset[0], y * size[1] + offset[1], z * size[2] + offset[2])
            for x, y, z in corners
        ),
        faces,
    )


def sphere() -> Mesh:
    """Radius-one octahedral sphere tessellation; exact polyhedron volume 4/3."""
    return Mesh(
        ((1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)),
        (
            (0, 1, 4),
            (1, 2, 4),
            (2, 3, 4),
            (3, 0, 4),
            (1, 0, 5),
            (2, 1, 5),
            (3, 2, 5),
            (0, 3, 5),
        ),
    )


def torus() -> Mesh:
    """Square annular prism: genus one, volume (4² - 2²) * 1 = 12."""
    ring = ((-2, -2), (2, -2), (2, 2), (-2, 2), (-1, -1), (1, -1), (1, 1), (-1, 1))
    vertices = tuple((x, y, z) for z in (0, 1) for x, y in ring)
    faces: list[Face] = []
    for i in range(4):
        j = (i + 1) % 4
        for a, b, c, d in (
            (i, j, j + 8, i + 8),
            (j + 4, i + 4, i + 12, j + 12),
            (i + 8, j + 8, j + 12, i + 12),
            (j, i, i + 4, j + 4),
        ):
            faces.extend(((a, b, c), (a, c, d)))
    return Mesh(vertices, tuple(faces))


def combine(*meshes: Mesh) -> Mesh:
    vertices: list[Point] = []
    faces: list[Face] = []
    for mesh in meshes:
        start = len(vertices)
        vertices.extend(mesh.vertices)
        faces.extend((a + start, b + start, c + start) for a, b, c in mesh.faces)
    return Mesh(tuple(vertices), tuple(faces))


def duplicate_vertices(mesh: Mesh) -> Mesh:
    return Mesh(
        tuple(mesh.vertices[index] for face in mesh.faces for index in face),
        tuple((i, i + 1, i + 2) for i in range(0, len(mesh.faces) * 3, 3)),
    )


def permute(mesh: Mesh) -> Mesh:
    count = len(mesh.vertices)
    return Mesh(
        tuple(reversed(mesh.vertices)),
        tuple(
            (count - 1 - a, count - 1 - b, count - 1 - c)
            for a, b, c in reversed(mesh.faces)
        ),
    )


def binary(
    mesh: Mesh,
    *,
    header: bytes = b"PrintStash corpus v2",
    normal: Point = (0, 0, 0),
    attribute: int = 0,
) -> bytes:
    return (
        header.ljust(80, b"\x00")
        + struct.pack("<I", len(mesh.faces))
        + b"".join(
            struct.pack(
                "<12fH",
                *normal,
                *(value for index in face for value in mesh.vertices[index]),
                attribute,
            )
            for face in mesh.faces
        )
    )


def ascii_mesh(mesh: Mesh, *, name: str = "mesh", indent: str = "  ") -> bytes:
    lines = [f"solid {name}"]
    for face in mesh.faces:
        lines.extend((indent + "facet normal 0 0 0", indent + "outer loop"))
        lines.extend(
            indent + "vertex " + " ".join(f"{v:.9e}" for v in mesh.vertices[i])
            for i in face
        )
        lines.extend((indent + "endloop", indent + "endfacet"))
    return ("\r\n".join((*lines, f"endsolid {name}")) + "\r\n").encode()


def write_grid(
    destination: BinaryIO, *, width: int, height: int, ascii_format: bool = False
) -> None:
    """Stream a planar grid with fixed bounded buffers, never a triangle array."""
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise ValueError("invalid grid size")
    count = width * height * 2
    if count > MAX_GRID_FACES:
        raise ValueError("invalid grid size")
    destination.write(
        b"solid grid\n"
        if ascii_format
        else b"PrintStash v2 grid".ljust(80, b"\x00") + struct.pack("<I", count)
    )
    buffer = bytearray()
    for y in range(height):
        for x in range(width):
            for points in (
                ((x, y, 0), (x + 1, y, 0), (x + 1, y + 1, 0)),
                ((x, y, 0), (x + 1, y + 1, 0), (x, y + 1, 0)),
            ):
                if ascii_format:
                    record = (
                        "facet normal 0 0 1\nouter loop\n"
                        + "".join(f"vertex {a} {b} {c}\n" for a, b, c in points)
                        + "endloop\nendfacet\n"
                    ).encode()
                else:
                    record = struct.pack(
                        "<12fH", 0, 0, 1, *(v for point in points for v in point), 0
                    )
                if len(buffer) + len(record) > WRITE_CHUNK_BYTES:
                    destination.write(buffer)
                    buffer.clear()
                buffer.extend(record)
    if buffer:
        destination.write(buffer)
    if ascii_format:
        destination.write(b"endsolid grid\n")


def nonfinite_cube(value: float) -> Mesh:
    if math.isfinite(value):
        raise ValueError("nonfinite fixture requires NaN or infinity")
    mesh = box()
    return Mesh(((value, 0, 0), *mesh.vertices[1:]), mesh.faces)
