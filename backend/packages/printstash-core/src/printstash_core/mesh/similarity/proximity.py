"""Typed boundary for bounded native closest-surface queries and alignment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fingerprint import GeometryError
from .geometry import Surface

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    Array = NDArray[np.float64]
    Indices = NDArray[np.int64]


class SurfaceProximity:
    def __init__(self, surface: Surface):
        import numpy as np

        from ..native_rasterizer import kernel

        # Drop ndarray subclasses before indexing; TrackedArray hooks otherwise
        # run while assembling the immutable native input.
        triangles = np.asarray(surface.vertices)[np.asarray(surface.faces)]
        implementation = getattr(kernel(), "SurfaceTree", None)
        if implementation is None:
            raise GeometryError("native_similarity_unavailable")
        try:
            self._native = implementation(np.asarray(triangles, dtype="=f8").tobytes())
        except ValueError as exc:
            raise GeometryError(str(exc)) from exc

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
        try:
            packed = self._native.closest(
                np.asarray(points, dtype="=f8").tobytes(), max_work
            )
        except ValueError as exc:
            raise GeometryError(str(exc)) from exc
        result = np.frombuffer(packed, dtype="=f8").reshape(-1, 4)
        return result[:, 0].copy(), result[:, 1:].copy()

    def align(
        self, points: Array, rotation: Array, diagonal: float
    ) -> tuple[float, Array, Array, float]:
        """Refine a row-vector rotation against the owned triangle surface."""
        import numpy as np

        try:
            result = self._native.align(
                np.asarray(points, dtype="=f8").tobytes(),
                np.asarray(rotation, dtype="=f8").ravel().tolist(),
                diagonal,
            )
        except ValueError as exc:
            raise GeometryError(str(exc)) from exc
        refined, offset, convergence, error = result
        return (
            float(error),
            np.asarray(refined, dtype=np.float64),
            np.asarray(offset, dtype=np.float64),
            float(convergence),
        )
