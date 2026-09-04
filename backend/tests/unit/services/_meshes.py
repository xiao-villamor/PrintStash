"""Mesh files built byte by byte, shared by every mesh-service test.

Nothing here loads or renders anything — these are writers that produce a file
on disk with an exactly known triangle count, and they exist because the
services under test are all *guards*: they decide whether a mesh is small
enough to load, dense enough to sample, or hole-y enough to still show a hole.
A guard can only be tested against a mesh whose properties are known in
advance, which rules out fixtures downloaded from anywhere.

The interesting ones are not the plain cubes:

- `_write_binary_stl` writes structurally valid facets that are all zeroes. That
  is deliberate — the estimators read the *count* out of the header and must
  never parse the body, so a body of nothing at all proves they did not.
- `_write_annular_binary_stl` writes a ring. Its centre must stay transparent in
  a thumbnail, which is the single most sensitive property in the fallback
  rasteriser: a sampler that misses the hole produces a filled disc, and a
  filled disc looks like a plausible thumbnail of a different object.
- `_write_microfaceted_*` write surfaces tessellated so finely that each facet
  projects to less than a pixel. This is what a real gyroid or lattice looks
  like to the sampler, and it is the case where naive sampling renders visible
  noise instead of a surface.
- `_write_large_projected_binary_stl` writes facets that each cover the whole
  frame, which is how the candidate-pixel budget is provoked.

Sizes are chosen to be the smallest that still exhibit the property; the
microfaceted writers are the two that cost real time, and they are used only by
the tests that need them.
"""

from __future__ import annotations

import io
import math
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np


def _write_binary_stl(path: Path, n_triangles: int) -> None:
    """A minimal but structurally valid binary STL with *n_triangles* facets."""
    with path.open("wb") as fh:
        fh.write(b"\x00" * 80)  # header
        fh.write(struct.pack("<I", n_triangles))
        fh.write(b"\x00" * (50 * n_triangles))


def _write_renderable_binary_stl(path: Path, n_triangles: int) -> None:
    """Valid non-degenerate facets spread across the model bounds."""
    record = struct.Struct("<12fH")
    with path.open("wb") as fh:
        fh.write(b"fallback-regression".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", n_triangles))
        for index in range(n_triangles):
            x = float(index % 100)
            y = float((index // 100) % 100)
            z = float(index % 7) * 0.1
            fh.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    x,
                    y,
                    z,
                    x + 0.8,
                    y,
                    z,
                    x,
                    y + 0.8,
                    z,
                    0,
                )
            )


def _write_large_projected_binary_stl(path: Path, n_triangles: int) -> None:
    """Write many facets whose projected boxes deliberately cover the frame."""
    record = struct.Struct("<12fH")
    with path.open("wb") as fh:
        fh.write(b"large-projected".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", n_triangles))
        for _ in range(n_triangles):
            fh.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    -100.0,
                    -100.0,
                    0.0,
                    100.0,
                    -100.0,
                    0.0,
                    0.0,
                    100.0,
                    0.0,
                    0,
                )
            )


def _write_annular_binary_stl(path: Path, segments: int = 96) -> None:
    """Write a deterministic thin ring whose projected center must stay empty."""
    record = struct.Struct("<12fH")
    outer, inner = 10.0, 4.0
    top, bottom = 0.5, -0.5

    def point(radius: float, index: int, z: float) -> tuple[float, float, float]:
        angle = 2.0 * math.pi * index / segments
        return (radius * math.cos(angle), radius * math.sin(angle), z)

    triangles: list[tuple[tuple[float, float, float], ...]] = []
    for index in range(segments):
        next_index = (index + 1) % segments
        ot0, ot1 = point(outer, index, top), point(outer, next_index, top)
        it0, it1 = point(inner, index, top), point(inner, next_index, top)
        ob0, ob1 = point(outer, index, bottom), point(outer, next_index, bottom)
        ib0, ib1 = point(inner, index, bottom), point(inner, next_index, bottom)
        triangles.extend(
            [
                (ot0, ot1, it1),
                (ot0, it1, it0),
                (ob0, ib1, ob1),
                (ob0, ib0, ib1),
                (ot0, ob1, ot1),
                (ot0, ob0, ob1),
                (it0, it1, ib1),
                (it0, ib1, ib0),
            ]
        )

    with path.open("wb") as fh:
        fh.write(b"annular-regression".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", len(triangles)))
        for first, second, third in triangles:
            fh.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    *first,
                    *second,
                    *third,
                    0,
                )
            )


def _write_microfaceted_surface_stl(
    path: Path, columns: int = 420, rows: int = 420
) -> int:
    """Write a connected, densely tessellated non-planar surface.

    The surface is intentionally much wider than an individual facet at
    thumbnail scale.  This is a small public stand-in for large microfaceted
    solids: a bounded facet sample contains real geometry, but the true
    projected area of each sampled facet is too small to hit a pixel reliably.
    """
    record = struct.Struct("<12fH")
    triangles = 2 * columns * rows

    def surface_z(x: float, y: float) -> float:
        return 4.0 * math.sin(x / 17.0) * math.cos(y / 19.0)

    with path.open("wb") as fh:
        fh.write(b"microfaceted-regression".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", triangles))
        for row in range(rows):
            y0 = float(row)
            y1 = float(row + 1)
            for column in range(columns):
                x0 = float(column)
                x1 = float(column + 1)
                z00 = surface_z(x0, y0)
                z10 = surface_z(x1, y0)
                z11 = surface_z(x1, y1)
                z01 = surface_z(x0, y1)
                fh.write(
                    record.pack(
                        0.0,
                        0.0,
                        1.0,
                        x0,
                        y0,
                        z00,
                        x1,
                        y0,
                        z10,
                        x1,
                        y1,
                        z11,
                        0,
                    )
                )
                fh.write(
                    record.pack(
                        0.0,
                        0.0,
                        1.0,
                        x0,
                        y0,
                        z00,
                        x1,
                        y1,
                        z11,
                        x0,
                        y1,
                        z01,
                        0,
                    )
                )
    return triangles


def _write_microfaceted_annular_stl(
    path: Path, segments: int = 512, radial_steps: int = 8
) -> int:
    """Write a densely tessellated annulus with a real center hole."""
    record = struct.Struct("<12fH")
    outer, inner = 10.0, 4.0
    top, bottom = 0.5, -0.5
    triangles: list[tuple[tuple[float, float, float], ...]] = []

    def point(radius: float, index: int, z: float) -> tuple[float, float, float]:
        angle = 2.0 * math.pi * index / segments
        return (radius * math.cos(angle), radius * math.sin(angle), z)

    for index in range(segments):
        next_index = (index + 1) % segments
        for step in range(radial_steps):
            outer0 = outer - (outer - inner) * step / radial_steps
            outer1 = outer - (outer - inner) * (step + 1) / radial_steps
            ot0, ot1 = point(outer0, index, top), point(outer0, next_index, top)
            it0, it1 = point(outer1, index, top), point(outer1, next_index, top)
            ob0, ob1 = point(outer0, index, bottom), point(outer0, next_index, bottom)
            ib0, ib1 = point(outer1, index, bottom), point(outer1, next_index, bottom)
            triangles.extend(
                [
                    (ot0, ot1, it1),
                    (ot0, it1, it0),
                    (ob0, ib1, ob1),
                    (ob0, ib0, ib1),
                ]
            )
        ot0, ot1 = point(outer, index, top), point(outer, next_index, top)
        ob0, ob1 = point(outer, index, bottom), point(outer, next_index, bottom)
        it0, it1 = point(inner, index, top), point(inner, next_index, top)
        ib0, ib1 = point(inner, index, bottom), point(inner, next_index, bottom)
        triangles.extend(
            [
                (ot0, ob1, ot1),
                (ot0, ob0, ob1),
                (it0, it1, ib1),
                (it0, ib1, ib0),
            ]
        )

    with path.open("wb") as fh:
        fh.write(b"microfaceted-annulus".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", len(triangles)))
        for first, second, third in triangles:
            fh.write(record.pack(0.0, 0.0, 1.0, *first, *second, *third, 0))
    return len(triangles)


def _write_obj(path: Path, tri_faces: int, *, quads: int = 0) -> None:
    lines = [b"# comment\n", b"o mesh\n", b"v 0 0 0\n", b"vn 0 0 1\n"]
    lines += [b"f 1//1 2//1 3//1\n"] * tri_faces
    lines += [b"f 1 2 3 4\n"] * quads  # quad = 2 triangles after fan
    path.write_bytes(b"".join(lines))


def _largest_component_fraction(mask: np.ndarray) -> float:
    visible = int(mask.sum())
    if visible == 0:
        return 0.0
    visited = np.zeros(mask.shape, dtype=bool)
    largest = 0
    height, width = mask.shape
    for y, x in zip(*np.where(mask), strict=True):
        if visited[y, x]:
            continue
        visited[y, x] = True
        stack = [(int(y), int(x))]
        size = 0
        while stack:
            current_y, current_x = stack.pop()
            size += 1
            for delta_y in (-1, 0, 1):
                for delta_x in (-1, 0, 1):
                    next_y, next_x = current_y + delta_y, current_x + delta_x
                    if (
                        0 <= next_y < height
                        and 0 <= next_x < width
                        and mask[next_y, next_x]
                        and not visited[next_y, next_x]
                    ):
                        visited[next_y, next_x] = True
                        stack.append((next_y, next_x))
        largest = max(largest, size)
    return largest / visible


def _valid_preview_png(color: tuple[int, int, int] = (16, 192, 224)) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(output, format="PNG")
    return output.getvalue()


def _fake_mesh(num_faces: int):
    return SimpleNamespace(
        vertices=np.zeros((3, 3), dtype=np.float64),
        bounds=np.array([[0.0, 0.0, 0.0], [10.0, 20.0, 30.0]]),
        faces=np.zeros((num_faces, 3), dtype=np.int64),
        volume=42.0,
    )


def _real_binary_stl_cube(path: Path) -> None:
    import trimesh

    trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(path, file_type="stl")


def _over_cap_3mf_with_preview(tmp_path: Path) -> tuple[Path, bytes]:
    png = _valid_preview_png((240, 128, 32))
    p = tmp_path / "big.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # ~157k tris
        zf.writestr("Metadata/thumbnail.png", png)
    return p, png
