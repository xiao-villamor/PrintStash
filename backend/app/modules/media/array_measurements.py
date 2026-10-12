"""Basic measurements over bounded source arrays, without a topology object.

The STL consumer uses the same 1e-8 vertex weld and component-relative float64
integral as the canonical mesh path. Rendering owns a separate float32 copy;
these source buffers are never centred, rounded or mutated in place.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeUnavailable,
    VolumeUnavailableCause,
    validate_geometry_extents,
    volume_value,
)

from app.modules.media.mesh_contracts import MeshMeasurements

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    from app.modules.media.visual_loading import VisualMesh

_BATCH_FACES = 4096


def _edge_pairs(faces: NDArray[np.int64]) -> tuple[NDArray[np.int64] | None, bool]:
    import numpy as np

    edges = faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    ordered = np.sort(edges, axis=1)
    # Source policy bounds vertices far below sqrt(int64.max), so the packed
    # key is exact, with no hash collision or coordinate quantization.
    width = int(faces.max()) + 1
    if width > 3_000_000_000:
        raise ValueError("source_index_range_exceeded")
    keys = ordered[:, 0] * width + ordered[:, 1]
    order = np.argsort(keys, kind="stable")
    keys = keys[order]
    boundaries = np.r_[0, np.flatnonzero(keys[1:] != keys[:-1]) + 1, len(keys)]
    if np.any(np.diff(boundaries) != 2):
        return None, False
    pairs = order.reshape(-1, 2)
    winding = bool(np.all(edges[pairs[:, 0], 0] == edges[pairs[:, 1], 1]))
    return pairs // 3, winding


def _integral(vertices, faces, pairs) -> float:
    import numpy as np
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    rows, columns = pairs.T
    graph = coo_matrix(
        (np.ones(len(rows), dtype=np.int8), (rows, columns)),
        shape=(len(faces), len(faces)),
    ).tocsr()
    count, labels = connected_components(graph, directed=False)
    del graph
    first = np.full(count, len(faces), dtype=np.int64)
    np.minimum.at(first, labels, np.arange(len(faces)))
    origins = vertices[faces[first, 0]]
    integrals = []
    for offset in range(0, len(faces), _BATCH_FACES):
        end = min(offset + _BATCH_FACES, len(faces))
        triangles = vertices[faces[offset:end]]
        triangles -= origins[labels[offset:end]][:, None, :]
        # Keep the canonical edge subtraction and summation order. Only the
        # volume term of the divergence integral is needed, not inertia/centroid.
        edges = triangles[:, 1:, :] - triangles[:, :2, :]
        cross_x = edges[:, 0, 1] * edges[:, 1, 2] - edges[:, 0, 2] * edges[:, 1, 1]
        sum_x = triangles[:, 0, 0] + triangles[:, 1, 0] + triangles[:, 2, 0]
        integrals.append(float(np.sum(cross_x * sum_x) / 6.0))
    return math.fsum(integrals)


def geometry_from_arrays(source: VisualMesh) -> MeshMeasurements:
    """Measure admitted, nonempty source arrays with position-only topology.

    This is suitable for STL. OBJ UV/normal seams need their analytical loader;
    passing their preview-only representation here would discard topology data.
    """
    import numpy as np

    vertices, faces = source.vertices, source.faces
    used = np.unique(faces)
    points = vertices[used]
    bounds = points.max(axis=0) - points.min(axis=0)
    geometry = MeshMeasurements.unavailable().geometry.copy()
    geometry.update(
        bbox_x_mm=float(bounds[0]),
        bbox_y_mm=float(bounds[1]),
        bbox_z_mm=float(bounds[2]),
        triangle_count=len(faces),
    )
    validate_geometry_extents(geometry)
    pairs, winding = _edge_pairs(faces)
    if pairs is None:
        # Match the existing merge tolerance and first source representative.
        # Quantized coordinates are keys only, never measurement coordinates.
        with np.errstate(invalid="ignore", over="ignore"):
            quantized = np.round(points * 1e8).astype(np.int64)
        keys = np.ascontiguousarray(quantized).view(np.dtype((np.void, 24))).ravel()
        _, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
        mapping = np.zeros(len(vertices), dtype=np.int64)
        mapping[used] = inverse
        vertices, faces = points[first], mapping[faces]
        del quantized, keys, mapping, inverse, points
        pairs, winding = _edge_pairs(faces)
    if pairs is None:
        volume = VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT)
    elif not winding:
        volume = VolumeUnavailable(VolumeUnavailableCause.INCONSISTENT_WINDING)
    else:
        try:
            integral = _integral(vertices, faces, pairs)
        except OverflowError:
            integral = math.inf
        if not math.isfinite(integral):
            volume = VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
        elif integral <= 0:
            volume = VolumeUnavailable(VolumeUnavailableCause.NON_POSITIVE_INTEGRAL)
        else:
            volume = VolumeMeasured(integral)
    geometry["volume_mm3"] = volume_value(volume)
    return MeshMeasurements(geometry, volume)
