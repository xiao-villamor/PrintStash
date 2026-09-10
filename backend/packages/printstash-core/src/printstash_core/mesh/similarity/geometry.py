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
    clean_mesh,
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
    weights = np.column_stack((1 - root, root * (1 - uv[:, 1]), root * uv[:, 1]))
    return np.einsum("fi,fij->fj", weights, triangles)


def nearest_neighbors(
    source: FloatArray, target: FloatArray
) -> tuple[FloatArray, IntArray]:
    """Nearest points in blocks of at most 128 × target-count distances.

    The target is capped by the owning operation. Verification samples at most
    5,000 points; exact vertex equivalence uses the admitted mesh vertex cap.
    """
    import numpy as np

    if not len(target):
        raise GeometryError("empty_target")
    distances = np.empty(len(source), dtype=np.float64)
    indices = np.empty(len(source), dtype=np.int64)
    target_norm = np.einsum("ij,ij->i", target, target)
    # Bound the distance temporary at 1M float64 cells even for dense meshes.
    chunk = max(1, min(128, 1_000_000 // len(target)))
    for start in range(0, len(source), chunk):
        block = source[start : start + chunk]
        squared = (
            np.einsum("ij,ij->i", block, block)[:, None]
            + target_norm[None, :]
            - 2 * (block @ target.T)
        )
        choices = np.argmin(squared, axis=1)
        distances[start : start + len(block)] = np.sqrt(
            np.maximum(squared[np.arange(len(block)), choices], 0)
        )
        indices[start : start + len(block)] = choices
    return distances, indices


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

    if len(left.vertices) != len(right.vertices) or len(left.faces) != len(right.faces):
        return False
    target = right.vertices @ rotation * scale + translation
    # Lexicographic order suffices only after demonstrating every positional
    # correspondence. Near ties are handled with a second shifted grid.
    for offset in (0.0, 0.5):
        a = np.floor(left.vertices / tolerance + offset).astype(np.int64)
        b = np.floor(target / tolerance + offset).astype(np.int64)
        order_a = np.lexsort(a.T[::-1])
        order_b = np.lexsort(b.T[::-1])
        if not np.all(
            np.linalg.norm(left.vertices[order_a] - target[order_b], axis=1)
            <= tolerance
        ):
            continue
        mapping = np.empty(len(target), dtype=np.int64)
        mapping[order_b] = order_a
        faces_a = np.sort(left.faces, axis=1)
        faces_b = np.sort(mapping[right.faces], axis=1)
        faces_a = faces_a[np.lexsort(faces_a.T[::-1])]
        faces_b = faces_b[np.lexsort(faces_b.T[::-1])]
        if np.array_equal(faces_a, faces_b):
            return True
    return False
