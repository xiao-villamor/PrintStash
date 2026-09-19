"""Bounded orthographic surface voxelization, with optional closed-solid fill.

All meshes use one supplied physical cube. Triangle intersections are computed
at voxel-column centers; duplicate intersections along shared edges count once.
Open surfaces retain surface occupancy without inventing an enclosed volume.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fingerprint import GeometryError

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


def voxelize(
    vertices: NDArray[np.float64],
    faces: NDArray[np.int64],
    *,
    half_width: float,
    resolution: int = 64,
    fill: bool = True,
) -> NDArray[np.bool_]:
    import numpy as np

    from ..native_rasterizer import kernel

    if resolution not in (16, 32, 64) or not np.isfinite(half_width) or half_width <= 0:
        raise GeometryError("invalid_voxel_recipe")
    try:
        packed = kernel().voxelize(
            np.asarray(vertices, dtype="=f8").tobytes(),
            np.asarray(faces, dtype="=i8").tobytes(),
            half_width,
            resolution,
            fill,
        )
    except ValueError as exc:
        raise GeometryError(str(exc)) from exc
    return np.frombuffer(packed, dtype=np.bool_).reshape((resolution,) * 3).copy()
