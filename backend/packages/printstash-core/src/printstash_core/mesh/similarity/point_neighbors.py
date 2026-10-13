"""Reusable exact point lookup for bounded verification samples.

This owns the native index and target snapshot. No tree object escapes the owner;
queries use one native thread so application admission controls parallelism.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .fingerprint import GeometryError
from .time_budget import check_deadline

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]
    IntArray = NDArray[np.int64]

MAX_NEIGHBOR_POINTS = 10_000
_QUERY_BLOCK = 256


def _points(value: FloatArray) -> FloatArray:
    import numpy as np

    array = np.asarray(value)
    if array.ndim != 2 or array.shape[1] != 3 or array.dtype.kind != "f":
        raise GeometryError("invalid_point_cloud")
    if len(array) > MAX_NEIGHBOR_POINTS:
        raise GeometryError("point_limit")
    if not np.isfinite(array).all():
        raise GeometryError("invalid_point_cloud")
    return np.asarray(array, dtype=np.float64)


def _scaled(points: FloatArray, exponent: int) -> FloatArray:
    import numpy as np

    with np.errstate(over="raise", invalid="raise"):
        scaled = np.ldexp(points, -exponent)
        # A power-of-two scale is exact unless a value leaves float64's range.
        # Refuse that range instead of silently merging distinguishable points.
        if not np.array_equal(np.ldexp(scaled, exponent), points):
            raise GeometryError("numeric_range")
    return scaled


class PointNeighbors:
    def __init__(self, target: FloatArray, *, deadline: float | None = None) -> None:
        import numpy as np
        from scipy.spatial import KDTree  # pyright: ignore[reportMissingTypeStubs]

        check_deadline(deadline)
        points = _points(target)
        if not len(points):
            raise GeometryError("empty_target")
        self._points, self._original = np.unique(points, axis=0, return_index=True)
        self._exponent = math.frexp(float(np.abs(self._points).max()))[1]
        try:
            self._tree = KDTree(_scaled(self._points, self._exponent), copy_data=True)
        except GeometryError:
            raise
        except (FloatingPointError, ValueError) as exc:
            raise GeometryError("numeric_range") from exc
        check_deadline(deadline)

    def query(
        self, source: FloatArray, *, deadline: float | None = None
    ) -> tuple[FloatArray, IntArray]:
        """Return physical distances and original target indices.

        Equal distances choose the first target occurrence. Ambiguous native
        ties use bounded direct distances, independent of KD-tree traversal order.
        The enclosing worker still enforces a hard deadline during native calls.
        """
        import numpy as np

        check_deadline(deadline)
        points = _points(source)
        distances = np.empty(len(points), dtype=np.float64)
        indices = np.empty(len(points), dtype=np.int64)
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                for start in range(0, len(points), _QUERY_BLOCK):
                    check_deadline(deadline)
                    block = points[start : start + _QUERY_BLOCK]
                    native_distance, native_index = self._tree.query(
                        _scaled(block, self._exponent),
                        k=min(2, len(self._points)),
                        eps=0,
                        workers=1,
                    )
                    check_deadline(deadline)
                    native_distance = np.asarray(native_distance).reshape(
                        len(block), -1
                    )
                    native_index = np.asarray(native_index).reshape(len(block), -1)
                    if not np.isfinite(native_distance).all():
                        raise GeometryError("numeric_range")
                    choices = native_index[:, 0].copy()
                    if len(self._points) > 1:
                        near_tie = np.isclose(
                            native_distance[:, 0],
                            native_distance[:, 1],
                            rtol=8 * np.finfo(np.float64).eps,
                            atol=0,
                        )
                        for row in np.flatnonzero(near_tie):
                            check_deadline(deadline)
                            exact = np.hypot.reduce(self._points - block[row], axis=1)
                            tied = np.flatnonzero(exact == exact.min())
                            choices[row] = tied[np.argmin(self._original[tied])]
                    result = np.hypot.reduce(block - self._points[choices], axis=1)
                    if not np.isfinite(result).all():
                        raise GeometryError("numeric_range")
                    stop = start + len(block)
                    distances[start:stop] = result
                    indices[start:stop] = self._original[choices]
        except GeometryError:
            raise
        except (FloatingPointError, ValueError) as exc:
            raise GeometryError("numeric_range") from exc
        check_deadline(deadline)
        return distances, indices
