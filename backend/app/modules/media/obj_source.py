"""Small plain OBJ source arrays with the canonical indexed topology.

Only the untextured triangle dialect is handled here. Other layouts return
None for the existing scene loader. Stored normal indices split vertices just
as the scene loader does; geometry measurement may weld a separate copy after
an open-topology result. No normal or UV seam is silently discarded on load.
"""

from __future__ import annotations

import math
import os
import time
from array import array
from pathlib import Path

from app.modules.media import mesh_policy
from app.modules.media.stl_reader import STLSourceSnapshot
from app.modules.media.visual_loading import VisualMesh

_MAX_SOURCE_BYTES = 8 * 1024**2
_MAX_LINE_BYTES = 64 * 1024


def load_obj_source(path: Path) -> VisualMesh | None:
    """Try a bounded common dialect; delegate every other dialect unchanged."""
    try:
        return _load_obj_source(path)
    except OSError:
        return None


def _load_obj_source(path: Path) -> VisualMesh | None:
    import numpy as np

    vertices, positions, normals = array("d"), array("q"), array("q")
    normal_count = 0
    largest_ignored_reference = 0
    face_limit = mesh_policy.load_face_budget(".obj")
    deadline = time.monotonic() + 45.0
    with path.open("rb") as stream:
        before = STLSourceSnapshot.from_stat(os.fstat(stream.fileno()))
        if before.size > _MAX_SOURCE_BYTES:
            return None
        consumed = 0
        while raw := stream.readline(_MAX_LINE_BYTES + 1):
            consumed += len(raw)
            if (
                len(raw) > _MAX_LINE_BYTES
                or consumed > before.size
                or time.monotonic() >= deadline
                or b"\\" in raw
                or b"\x00" in raw
                or b"mtllib" in raw
            ):
                return None
            parts = raw.split()
            if not parts or parts[0].startswith(b"#"):
                continue
            # The canonical parser recognizes directives at the beginning of
            # a line with a space separator. Leave other whitespace dialects
            # to that parser rather than interpreting extra geometry here.
            if not raw.startswith(parts[0] + b" "):
                return None
            kind = parts[0]
            if kind in (b"v", b"vn", b"f", b"l", b"p") and b"_" in raw:
                return None
            try:
                if kind in (b"v", b"vn"):
                    if len(parts) != 4:
                        return None
                    xyz = tuple(float(value) for value in parts[1:])
                    if not all(math.isfinite(value) for value in xyz):
                        return None
                    if kind == b"vn":
                        normal_count += 1
                        if normal_count > 3 * face_limit:
                            return None
                    else:
                        if len(vertices) // 3 >= 3 * face_limit:
                            return None
                        vertices.extend(xyz)
                elif kind == b"f":
                    if len(parts) != 4 or len(positions) // 3 >= face_limit:
                        return None
                    for field in parts[1:]:
                        pair = field.split(b"//")
                        if len(pair) != 2:
                            return None
                        position, normal = int(pair[0]), int(pair[1])
                        if position <= 0 or normal <= 0:
                            return None
                        positions.append(position - 1)
                        normals.append(normal - 1)
                elif kind in (b"l", b"p"):
                    # The pinned scene loader ignores polylines and points.
                    # They cannot introduce external assets in this dialect.
                    if len(parts) < (3 if kind == b"l" else 2):
                        return None
                    references = tuple(int(value) for value in parts[1:])
                    if min(references) <= 0:
                        return None
                    largest_ignored_reference = max(
                        largest_ignored_reference, max(references)
                    )
                elif kind not in (b"g", b"s"):
                    return None
            except ValueError, OverflowError:
                return None
        if (
            consumed != before.size
            or before != STLSourceSnapshot.from_stat(os.fstat(stream.fileno()))
            or before != STLSourceSnapshot.from_stat(path.stat())
        ):
            return None
    xyz = np.frombuffer(vertices, dtype=np.float64).reshape(-1, 3)
    vertex_indices = np.frombuffer(positions, dtype=np.int64)
    normal_indices = np.frombuffer(normals, dtype=np.int64)
    if (
        not len(vertex_indices)
        or not len(xyz)
        or vertex_indices.max() >= len(xyz)
        or normal_indices.max() >= normal_count
        or largest_ignored_reference > len(xyz)
    ):
        return None
    # Exact integer pairs preserve stored-normal seams. The numeric order of
    # these vertex IDs is irrelevant: face order and every corner coordinate
    # remain identical, including first source representatives when welding.
    pairs, inverse = np.unique(
        np.column_stack((vertex_indices, normal_indices)),
        axis=0,
        return_inverse=True,
    )
    return VisualMesh(xyz[pairs[:, 0]], inverse.reshape(-1, 3))
