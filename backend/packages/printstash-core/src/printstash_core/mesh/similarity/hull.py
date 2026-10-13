"""Validated, normalized three-dimensional hull volume using SciPy/Qhull.

The mesh extra owns the native dependency. No library object crosses this
array-to-scalar boundary. Applications enforce native memory and wall-clock
budgets in their worker supervisor; point count bounds inputs here.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .budgets import MAX_ANALYSIS_VERTICES
from .fingerprint import GeometryError

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]


def hull_volume(
    vertices: FloatArray, *, max_points: int = MAX_ANALYSIS_VERTICES
) -> float:
    """Return outer volume without perturbing degenerate input into a solid.

    Normalize before native products, then restore physical units. Sorting and
    deduplicating gives Qhull the same input for equivalent vertex orderings.
    The former Python point/plane operation counter is replaced by an explicit
    input ceiling; it cannot meaningfully bound a different native algorithm.
    """
    import numpy as np

    # SciPy 1.17 ships no typing metadata; keep the untyped API inside this owner.
    from scipy.spatial import (  # pyright: ignore[reportMissingTypeStubs]
        ConvexHull,
        QhullError,
    )

    if type(max_points) is not int or not 1 <= max_points <= MAX_ANALYSIS_VERTICES:
        raise GeometryError("invalid_hull_budget")
    if vertices.ndim != 2 or vertices.shape[1] != 3:
        raise GeometryError("invalid_geometry")
    if len(vertices) > max_points:
        raise GeometryError("hull_resource_limit")
    if not np.isfinite(vertices).all():
        raise GeometryError("invalid_geometry")
    if len(vertices) < 4:
        raise GeometryError("degenerate_hull")
    points = np.unique(vertices, axis=0)
    if len(points) < 4:
        raise GeometryError("degenerate_hull")
    # Halving before subtracting also avoids overflow between finite extremes.
    low, high = points.min(axis=0), points.max(axis=0)
    center = low * 0.5 + high * 0.5
    points = points - center
    scale = float(np.max(np.abs(points)))
    if not math.isfinite(scale) or scale <= 0:
        raise GeometryError("degenerate_hull")
    points /= scale
    try:
        normalized_volume = float(ConvexHull(points).volume)
    except QhullError as exc:
        raise GeometryError("degenerate_hull") from exc
    # Multiplication in this order preserves small finite volumes that a
    # separately computed scale**3 could underflow, and detects overflow.
    volume = normalized_volume * scale * scale * scale
    if not math.isfinite(volume) or volume <= 0:
        raise GeometryError("invalid_geometry")
    return volume
