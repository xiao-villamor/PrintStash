"""Bounded QuickHull over exterior point sets, independent of tessellation density."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fingerprint import GeometryError

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]
    IntArray = NDArray[np.int64]


def hull_volume(vertices: FloatArray, *, max_work: int = 20_000_000) -> float:
    """Compute the outer volume while visiting only points outside the current hull.

    Each point belongs to at most one facet's exterior set. A hull expansion
    reassigns only the sets of removed facets; interior points never return.
    Count every point/plane test, including initialization and visible facets.
    The hard work ceiling still handles adversarial convex inputs explicitly.
    """
    import numpy as np

    if type(max_work) is not int or not 1 <= max_work <= 20_000_000:
        raise GeometryError("invalid_hull_budget")
    # Deterministic ties and translation before products keep exports stable.
    vertices = np.unique(vertices, axis=0)
    vertices = vertices - vertices[0]
    scale = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    first = 0
    second = int(np.argmax(np.linalg.norm(vertices, axis=1)))
    axis = vertices[second]
    third = int(np.argmax(np.linalg.norm(np.cross(vertices, axis), axis=1)))
    normal = np.cross(vertices[third], axis)
    fourth = int(np.argmax(np.abs(vertices @ normal)))
    if abs(float(vertices[fourth] @ normal)) <= scale**3 * 1e-12:
        raise GeometryError("degenerate_hull")
    chosen = np.array([first, second, third, fourth])
    interior = vertices[chosen].mean(axis=0)
    tolerance = scale * 1e-10
    work = 0

    def consume(count: int) -> None:
        nonlocal work
        work += count
        if work > max_work:
            raise GeometryError("hull_resource_limit")

    def planes(facets: IntArray) -> tuple[FloatArray, FloatArray]:
        triangles = vertices[facets]
        normals = np.cross(
            triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        )
        lengths = np.linalg.norm(normals, axis=1)
        if np.any(lengths <= scale**2 * 1e-14):
            raise GeometryError("degenerate_hull")
        normals /= lengths[:, None]
        inward = np.einsum("ij,ij->i", normals, interior - triangles[:, 0]) > 0
        normals[inward] *= -1
        return normals, np.einsum("ij,ij->i", normals, triangles[:, 0])

    def assign(ids: IntArray, normals: FloatArray, offsets: FloatArray):
        consume(len(ids) * len(normals))
        outside: list[list[IntArray]] = [[] for _ in normals]
        # Bound the distance temporary even for a dense input or wide horizon.
        chunk = max(1, 250_000 // len(normals))
        for start in range(0, len(ids), chunk):
            subset = ids[start : start + chunk]
            distances = vertices[subset] @ normals.T - offsets
            owner = np.argmax(distances, axis=1)
            external = distances[np.arange(len(subset)), owner] > tolerance
            for face in np.unique(owner[external]):
                outside[int(face)].append(subset[external & (owner == face)])
        return [
            np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
            for parts in outside
        ]

    facets = chosen[np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])]
    normals, offsets = planes(facets)
    outside = assign(np.arange(len(vertices)), normals, offsets)
    while True:
        active = next((i for i, ids in enumerate(outside) if len(ids)), None)
        if active is None:
            break
        ids = outside[active]
        consume(len(ids))
        eye = int(ids[np.argmax(vertices[ids] @ normals[active] - offsets[active])])
        consume(len(facets))
        visible = vertices[eye] @ normals.T - offsets > tolerance
        edges = facets[visible][:, ((0, 1), (1, 2), (2, 0))].reshape((-1, 2))
        unique, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
        horizon = unique[counts == 1]
        new = np.column_stack((horizon, np.full(len(horizon), eye)))
        new_normals, new_offsets = planes(new)
        candidates = np.concatenate([outside[i] for i in np.flatnonzero(visible)])
        reassigned = assign(candidates[candidates != eye], new_normals, new_offsets)
        outside = [outside[i] for i in np.flatnonzero(~visible)] + reassigned
        facets = np.vstack((facets[~visible], new))
        normals = np.vstack((normals[~visible], new_normals))
        offsets = np.r_[offsets[~visible], new_offsets]
    triangles = vertices[facets] - interior
    return float(
        np.abs(
            np.einsum(
                "ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])
            )
        ).sum()
        / 6
    )
