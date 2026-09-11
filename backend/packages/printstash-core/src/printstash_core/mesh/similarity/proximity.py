"""Bounded closest-surface queries for registration, without native dependencies.

A median AABB hierarchy prunes triangles. Leaves use exact point/triangle
projection; at most 128 points by 32 triangles are broadcast at once. Work has a
hard ceiling independent of mesh layout, including adversarial overlapping faces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .fingerprint import GeometryError
from .geometry import Surface

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    Array = NDArray[np.float64]
    Indices = NDArray[np.int64]


@dataclass(frozen=True)
class _Node:
    low: Array
    high: Array
    indices: Indices | None
    left: _Node | None = None
    right: _Node | None = None


class SurfaceProximity:
    def __init__(self, surface: Surface):
        import numpy as np

        self.triangles = surface.vertices[surface.faces]
        low, high = self.triangles.min(axis=1), self.triangles.max(axis=1)
        centers = (low + high) / 2

        def build(indices: Indices) -> _Node:
            lo, hi = low[indices].min(axis=0), high[indices].max(axis=0)
            if len(indices) <= 32:
                return _Node(lo, hi, indices)
            axis = int(np.argmax(np.ptp(centers[indices], axis=0)))
            order = indices[np.argsort(centers[indices, axis], kind="stable")]
            middle = len(order) // 2
            return _Node(lo, hi, None, build(order[:middle]), build(order[middle:]))

        self.root = build(np.arange(len(self.triangles)))

    def closest(
        self, points: Array, *, max_work: int = 32_000_000
    ) -> tuple[Array, Array]:
        import numpy as np

        if (
            points.ndim != 2
            or points.shape[1] != 3
            or not 1 <= len(points) <= 5000
            or not np.isfinite(points).all()
        ):
            raise GeometryError("invalid_proximity_points")
        if type(max_work) is not int or not 1 <= max_work <= 32_000_000:
            raise GeometryError("invalid_proximity_budget")
        best = np.full(len(points), np.inf)
        nearest = np.empty_like(points, dtype=np.float64)
        ids = np.arange(len(points))
        stack = [(self.root, ids, False)]
        if self.root.indices is None:
            # First establish a real distance upper bound from one nearby leaf
            # per point. Starting every point at infinity made a left-first walk
            # test distant triangles before reaching its own region of the mesh.
            # The subsequent complete traversal still proves the closest point.
            stack.append((self.root, ids, True))
        work = 0
        while stack:
            node, ids, seed = stack.pop()
            delta = np.maximum(
                np.maximum(node.low - points[ids], points[ids] - node.high), 0
            )
            ids = ids[np.einsum("ij,ij->i", delta, delta) <= best[ids] + 1e-20]
            if not len(ids):
                continue
            if node.indices is None:
                assert node.left is not None and node.right is not None
                if seed:
                    left_delta = np.maximum(
                        np.maximum(
                            node.left.low - points[ids], points[ids] - node.left.high
                        ),
                        0,
                    )
                    right_delta = np.maximum(
                        np.maximum(
                            node.right.low - points[ids], points[ids] - node.right.high
                        ),
                        0,
                    )
                    prefer_left = np.einsum(
                        "ij,ij->i", left_delta, left_delta
                    ) <= np.einsum("ij,ij->i", right_delta, right_delta)
                    stack.extend(
                        (
                            (node.right, ids[~prefer_left], True),
                            (node.left, ids[prefer_left], True),
                        )
                    )
                else:
                    stack.extend(((node.right, ids, False), (node.left, ids, False)))
                continue
            work += len(ids) * len(node.indices)
            if work > max_work:
                raise GeometryError("proximity_work_limit")
            triangles = self.triangles[node.indices]
            for start in range(0, len(ids), 128):
                subset = ids[start : start + 128]
                distance, closest = _leaf(points[subset], triangles)
                improved = distance < best[subset]
                best[subset[improved]] = distance[improved]
                nearest[subset[improved]] = closest[improved]
        return np.sqrt(best), nearest


def _leaf(points: Array, triangles: Array) -> tuple[Array, Array]:
    import numpy as np

    a, b, c = (triangles[:, index] for index in range(3))
    ab, ac = b - a, c - a
    normal = np.cross(ab, ac)
    normal_square = np.einsum("ij,ij->i", normal, normal)
    offset = points[:, None, :] - a
    height = np.einsum("pti,ti->pt", offset, normal) / normal_square
    projected = points[:, None, :] - height[:, :, None] * normal
    ap = projected - a
    d00 = np.einsum("ij,ij->i", ab, ab)
    d01 = np.einsum("ij,ij->i", ab, ac)
    d11 = np.einsum("ij,ij->i", ac, ac)
    d20 = np.einsum("pti,ti->pt", ap, ab)
    d21 = np.einsum("pti,ti->pt", ap, ac)
    # |ab x ac|² avoids cancellation in d00*d11-d01² on narrow facets.
    v = (d11 * d20 - d01 * d21) / normal_square
    w = (d00 * d21 - d01 * d20) / normal_square
    inside = (v >= 0) & (w >= 0) & (v + w <= 1)
    distance = np.where(inside, height**2 * normal_square, np.inf)
    chosen = projected.copy()
    for first, second in ((a, b), (b, c), (c, a)):
        edge = second - first
        parameter = np.clip(
            np.einsum("pti,ti->pt", points[:, None, :] - first, edge)
            / np.einsum("ij,ij->i", edge, edge),
            0,
            1,
        )
        closest = first + parameter[:, :, None] * edge
        delta = points[:, None, :] - closest
        squared = np.einsum("pti,pti->pt", delta, delta)
        improved = squared < distance
        distance = np.minimum(distance, squared)
        chosen[improved] = closest[improved]
    indices = np.argmin(distance, axis=1)
    return distance[np.arange(len(points)), indices], chosen[
        np.arange(len(points)), indices
    ]
