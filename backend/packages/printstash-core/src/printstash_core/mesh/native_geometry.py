"""Bounded native measurements for import metadata, without inertia arrays."""

from __future__ import annotations

import math
from typing import Any

from .native_rasterizer import kernel


def measure_mesh(
    mesh: Any, *, face_chunk_size: int = 65_536
) -> dict[str, float | int | None]:
    """Return bounded native import measurements."""
    import numpy as np

    measure = kernel().measure_triangles
    if type(face_chunk_size) is not int or face_chunk_size < 1:
        raise ValueError("invalid geometry chunk size")
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    out: dict[str, float | int | None] = {
        "bbox_x_mm": None,
        "bbox_y_mm": None,
        "bbox_z_mm": None,
        "volume_mm3": None,
        "triangle_count": None,
    }
    if len(vertices) == 0 or len(faces) == 0:
        return out
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    integrals = []
    for start in range(0, len(faces), face_chunk_size):
        # Immutable, bounded snapshots let Rust release the GIL safely without
        # borrowing mutable NumPy data or retaining a whole-mesh triangle array.
        packed = vertices[faces[start : start + face_chunk_size]].tobytes()
        bounds, integral = measure(packed)
        low = np.minimum(low, bounds[0])
        high = np.maximum(high, bounds[1])
        integrals.append(integral)
    for axis, extent in zip(
        ("bbox_x_mm", "bbox_y_mm", "bbox_z_mm"), high - low, strict=True
    ):
        out[axis] = round(float(extent), 2)
    out["triangle_count"] = len(faces)
    volume = math.fsum(integrals) / 6.0
    if math.isfinite(volume) and volume > 0:
        out["volume_mm3"] = round(volume, 2)
    return out
