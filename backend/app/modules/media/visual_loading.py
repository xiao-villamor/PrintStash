"""Bounded source arrays for previews, independent of topology and Trimesh.

Coordinates remain float64 until render preparation centres them. OBJ materials,
UVs and stored normals do not participate in the uniform preview shader. Source
STL measurements consume these same source arrays; OBJ analysis retains its
stored-normal and texture-aware loader.
"""

from __future__ import annotations

import math
import os
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from printstash_core.mesh.similarity import GeometryError

from app.core.config import settings
from app.modules.media import mesh_policy
from app.modules.media.stl_reader import (
    InvalidSTL,
    STLReadLimits,
    STLSourceSnapshot,
    materialize_stl,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

MAX_LINE_BYTES = 64 * 1024
MAX_LINES = 10_000_000


@dataclass(frozen=True)
class VisualMesh:
    vertices: NDArray[np.float64]
    faces: NDArray[np.int64]


def load_visual_mesh(path: Path, suffix: str) -> VisualMesh:
    """Load source geometry without constructing an analysis mesh or topology."""
    import numpy as np

    if suffix not in {".stl", ".obj"}:
        raise ValueError("unsupported_visual_format")
    size_limit = (
        min(1 << 30, int(settings.mesh_max_load_mb * 1024**2))
        if settings.mesh_max_load_mb > 0
        else 1 << 30
    )
    limits = STLReadLimits(
        max_triangles=mesh_policy.load_face_budget(suffix),
        max_source_bytes=size_limit,
    )
    try:
        if suffix == ".obj":
            return _load_obj(path, limits)
        loaded = materialize_stl(path, limits=limits)
    except InvalidSTL as exc:
        raise GeometryError(exc.reason.value) from exc
    except OSError as exc:
        raise GeometryError("source_unavailable") from exc
    return VisualMesh(
        loaded.triangles.reshape(-1, 3),
        np.arange(loaded.measurements.triangle_count * 3, dtype=np.int64).reshape(
            -1, 3
        ),
    )


def _load_obj(path: Path, limits: STLReadLimits) -> VisualMesh:
    import numpy as np

    vertices, faces = array("d"), array("q")
    deadline = time.monotonic() + 45.0
    with path.open("rb") as source:
        snapshot = STLSourceSnapshot.from_stat(os.fstat(source.fileno()))
        if snapshot.size > limits.max_source_bytes:
            raise GeometryError("resource_limit")
        consumed = 0
        line_number = 0
        continued = b""
        while line := source.readline(MAX_LINE_BYTES + 1):
            consumed += len(line)
            line_number += 1
            if (
                len(line) > MAX_LINE_BYTES
                or consumed > limits.max_source_bytes
                or line_number > MAX_LINES
                or time.monotonic() > deadline
            ):
                raise GeometryError("resource_limit")
            line = continued + line.partition(b"#")[0].rstrip()
            if len(line) > MAX_LINE_BYTES:
                raise GeometryError("resource_limit")
            if line.endswith(b"\\"):
                continued = line[:-1] + b" "
                continue
            continued = b""
            fields = line.split()
            if not fields:
                continue
            try:
                if fields[0] == b"v":
                    if len(fields) < 4:
                        raise ValueError("invalid vertex")
                    xyz = tuple(float(value) for value in fields[1:4])
                    if not all(math.isfinite(value) for value in xyz):
                        raise ValueError("nonfinite vertex")
                    # Unreferenced vertices are also bounded before allocation.
                    if len(vertices) // 3 >= limits.max_triangles * 3:
                        raise GeometryError("resource_limit")
                    vertices.extend(xyz)
                elif fields[0] == b"f":
                    if len(fields) < 4:
                        raise ValueError("invalid face")
                    if len(faces) // 3 + len(fields) - 3 > limits.max_triangles:
                        raise GeometryError("resource_limit")
                    count = len(vertices) // 3
                    polygon = []
                    for field in fields[1:]:
                        index = int(field.split(b"/", 1)[0])
                        if index == 0 or index < -count:
                            raise ValueError("invalid index")
                        polygon.append(index - 1 if index > 0 else count + index)
                    if len(polygon) == 4:
                        faces.extend(
                            (
                                polygon[0],
                                polygon[1],
                                polygon[2],
                                polygon[2],
                                polygon[3],
                                polygon[0],
                            )
                        )
                    else:
                        for corner in range(1, len(polygon) - 1):
                            faces.extend(
                                (polygon[0], polygon[corner], polygon[corner + 1])
                            )
            except GeometryError:
                raise
            except (ValueError, OverflowError) as exc:
                raise GeometryError("invalid_source") from exc
        if continued:
            raise GeometryError("invalid_source")
        if snapshot != STLSourceSnapshot.from_stat(os.fstat(source.fileno())):
            raise GeometryError("invalid_source")
    positions = np.frombuffer(vertices, dtype=np.float64).reshape(-1, 3)
    indices = np.frombuffer(faces, dtype=np.int64).reshape(-1, 3)
    if not len(indices) or not len(positions) or indices.max() >= len(positions):
        raise GeometryError("invalid_source")
    return VisualMesh(positions, indices)
