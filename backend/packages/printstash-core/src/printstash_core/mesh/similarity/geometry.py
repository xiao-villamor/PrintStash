"""Bounded geometry primitives shared by descriptors and verification.

Arrays describe analysis copies. Transform matrices map source coordinates into
the target's physical units; point samples always record their deterministic seed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .fingerprint import (
    FingerprintBudget,
    GeometryError,
    SurfaceMetrics,
    clean_mesh,
    measure_triangles,
    validate_mesh_arrays,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]
    IntArray = NDArray[np.int64]


@dataclass(frozen=True)
class Surface:
    vertices: FloatArray
    faces: IntArray
    areas: FloatArray
    centroid: FloatArray
    frame: FloatArray
    eigenvalues: FloatArray
    radius: float


def prepare_surface(vertices: NDArray[Any], faces: NDArray[Any]) -> Surface:
    """Return a centered analysis surface while retaining its original origin."""
    import numpy as np

    validate_mesh_arrays(vertices, faces, FingerprintBudget())
    # Tracking belongs to the caller's mutable mesh, not to analysis copies.
    vertices, faces = np.asarray(vertices), np.asarray(faces)
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            used = vertices[np.unique(faces)]
            origin = used[np.lexsort(used.T[::-1])[0]].astype(np.float64)
            verts, tris = clean_mesh(vertices, faces)
            triangles = verts[tris]
            areas = (
                np.linalg.norm(
                    np.cross(
                        triangles[:, 1] - triangles[:, 0],
                        triangles[:, 2] - triangles[:, 0],
                    ),
                    axis=1,
                )
                / 2
            )
            weights = areas / areas.sum()
            center = np.sum(triangles.mean(axis=1) * weights[:, None], axis=0)
            verts -= center
            triangles = verts[tris]
            sums = triangles.sum(axis=1)
            moment = (
                np.einsum("fvi,fvj,f->ij", triangles, triangles, weights)
                + np.einsum("fi,fj,f->ij", sums, sums, weights)
            ) / 12
            eigenvalues, frame = np.linalg.eigh(moment)
            frame[:, 0] *= np.linalg.det(frame)
            return Surface(
                verts,
                tris,
                areas,
                origin + center,
                frame,
                np.maximum(eigenvalues, 0),
                float(np.sqrt(np.trace(moment))),
            )
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise GeometryError("numeric_range") from exc


def measure_surface(surface: Surface) -> SurfaceMetrics:
    """Measure a prepared surface without re-cleaning or computing retrieval keys."""
    import numpy as np

    diagonal = float(np.linalg.norm(np.ptp(surface.vertices @ surface.frame, axis=0)))
    return measure_triangles(
        surface.vertices,
        surface.faces,
        float(surface.areas.sum()),
        diagonal,
        surface.eigenvalues,
    )


def sample_surface(surface: Surface, count: int, seed: int) -> FloatArray:
    """Independent area-weighted samples in centered physical coordinates."""
    import numpy as np

    if type(count) is not int or not 1 <= count <= 10_000:
        raise GeometryError("invalid_sample_count")
    rng = np.random.Generator(np.random.PCG64(seed))
    cdf = np.cumsum(surface.areas / surface.areas.sum())
    cdf[-1] = 1
    triangles = surface.vertices[surface.faces[np.searchsorted(cdf, rng.random(count))]]
    uv = rng.random((count, 2))
    root = np.sqrt(uv[:, 0])
    weights = np.asarray(
        np.column_stack((1 - root, root * (1 - uv[:, 1]), root * uv[:, 1])),
        dtype=np.float64,
    )
    return np.einsum("fi,fij->fj", weights, triangles)


def nearest_neighbors(
    source: FloatArray, target: FloatArray
) -> tuple[FloatArray, IntArray]:
    """Nearest points in blocks of at most 128 × target-count distances.

    The target is capped by the owning operation. Verification samples at most
    5,000 points; exact vertex equivalence uses the admitted mesh vertex cap.
    """
    import numpy as np

    from ..native_rasterizer import kernel

    if not len(target):
        raise GeometryError("empty_target")
    try:
        packed = kernel().nearest_neighbors(
            np.asarray(source, dtype="=f8").tobytes(),
            np.asarray(target, dtype="=f8").tobytes(),
        )
    except ValueError as exc:
        raise GeometryError(str(exc)) from exc
    records = np.frombuffer(
        packed, dtype=np.dtype([("distance", "=f8"), ("index", "=u8")])
    )
    return records["distance"].copy(), records["index"].astype(np.int64)


def equivalent_triangles(
    left: Surface,
    right: Surface,
    rotation: FloatArray,
    scale: float,
    translation: FloatArray,
    tolerance: float,
) -> bool:
    """Verify a bijection of vertices and triangle incidence, after alignment.

    Spatial orderings propose bijections; actual Euclidean distances decide
    correspondence. This avoids a quadratic dense-mesh equality check and never
    treats quantized cells themselves as proof of equivalence.
    """
    import numpy as np

    from ..native_rasterizer import kernel

    try:
        return bool(
            kernel().equivalent_triangles(
                np.asarray(left.vertices, dtype="=f8").tobytes(),
                np.asarray(left.faces, dtype="=i8").tobytes(),
                np.asarray(right.vertices, dtype="=f8").tobytes(),
                np.asarray(right.faces, dtype="=i8").tobytes(),
                np.asarray(rotation, dtype="=f8").ravel().tolist(),
                scale,
                np.asarray(translation, dtype="=f8").tolist(),
                tolerance,
            )
        )
    except ValueError as exc:
        raise GeometryError(str(exc)) from exc
