"""Deterministic retrieval evidence from already-loaded triangle arrays.

Only NumPy is needed, imported at the operation boundary. The caller owns file
validation, unit conversion, content leases and the shared compute budget. This
first T0 increment is deliberately partial: absent descriptors have reasons,
and no score, Evidence Class or equivalence claim is produced here.

Keys enumerate proper PCA sign choices after sorting eigenvalues (which fixes
axis permutations for separated eigenvalues). Repeated eigenvalues suppress
keys: choosing an arbitrary frame for a symmetric shape would invent stability.
Quantization can collide and can split nearby surfaces at either grid boundary;
every retrieved pair still needs independent geometric verification.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]
    IntArray = NDArray[np.int64]

# Includes cleanup, surface PCA, both grids, serialization and D2 recipe.
# This is not a calibrated classifier version. Never silently replace its golden.
ALGORITHM_VERSION = "geometry-t0-v1"
_GRID_RELATIVE = 1e-4
_PCA_GAP_RELATIVE = 1e-6
_AREA_RELATIVE = 1e-14
_D2_PAIRS = 8192
_D2_BINS = 64


class GeometryError(ValueError):
    """Stable code without source paths, filenames or raw coordinates."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FingerprintBudget:
    """Caps checked before allocating analysis copies; callers may lower them.

    O(vertices + faces + sample pairs) memory; no all-pairs distance matrix.
    These caps do not replace the application's cgroup-aware admission control.
    """

    max_vertices: int = 600_000
    max_faces: int = 200_000


_DEFAULT_BUDGET = FingerprintBudget()


@dataclass(frozen=True)
class CanonicalKeys:
    """Four keys per scale group: oriented base/offset, unoriented base/offset.

    Winding-insensitive means triangle vertex order is ignored, not that
    reflections are rotations. Physical keys include an absolute scale token.
    """

    physical: tuple[str, ...]
    normalized: tuple[str, ...]


@dataclass(frozen=True)
class SurfaceMetrics:
    vertex_count: int
    face_count: int
    euler_characteristic: int
    watertight: bool
    winding_consistent: bool
    surface_area: float
    bbox_dimensions: tuple[float, ...]
    volume: float | None
    volume_reason: str | None
    area_volume_ratio: float | None
    surface_eigenvalue_ratios: tuple[float, ...]


@dataclass(frozen=True)
class D2Descriptor:
    histogram: tuple[float, ...]
    mean_distance: float
    seed: int
    sample_pairs: int = _D2_PAIRS
    # Equal bins over [0, 4); the last bin also contains the overflow tail.
    histogram_range: tuple[float, float] = (0.0, 4.0)


@dataclass(frozen=True)
class MeshFingerprint:
    algorithm_version: str
    numpy_version: str
    keys: CanonicalKeys | None
    ambiguous_frame: bool
    metrics: SurfaceMetrics
    d2: D2Descriptor
    unavailable: tuple[tuple[str, str], ...]
    state: Literal["partial"] = "partial"


def fingerprint_mesh(
    vertices: NDArray[Any],
    faces: NDArray[Any],
    *,
    budget: FingerprintBudget = _DEFAULT_BUDGET,
) -> MeshFingerprint:
    """Analyze a mesh copy in caller-supplied physical units.

    Raises GeometryError for invalid, degenerate or over-budget input. Flat
    surfaces remain useful; volume is absent unless topology and orientation
    support it. Even a closed, oriented mesh is not checked for self-intersection,
    so its volume is only a retrieval descriptor, not proof of a valid solid.
    """
    import numpy as np

    for limit, ceiling in ((budget.max_vertices, 600_000), (budget.max_faces, 200_000)):
        if type(limit) is not int or not 1 <= limit <= ceiling:
            raise GeometryError("invalid_budget")
    if vertices.ndim != 2 or vertices.shape[1] != 3 or vertices.dtype.kind not in "fiu":
        raise GeometryError("invalid_vertices")
    if faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in "iu":
        raise GeometryError("invalid_faces")
    if len(vertices) > budget.max_vertices or len(faces) > budget.max_faces:
        raise GeometryError("resource_limit")
    if not np.isfinite(vertices).all():
        raise GeometryError("nonfinite_geometry")
    if len(faces) and (faces.min() < 0 or faces.max() >= len(vertices)):
        raise GeometryError("invalid_faces")
    if not len(faces):
        raise GeometryError("degenerate_surface")

    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return _fingerprint(vertices, faces)
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise GeometryError("numeric_range") from exc


def _clean(vertices: NDArray[Any], faces: NDArray[Any]) -> tuple[FloatArray, IntArray]:
    import numpy as np

    # Work only on referenced vertices: a stray unused coordinate must not
    # change cleanup tolerances, centering, PCA, or any surface descriptor.
    used, remap = np.unique(faces, return_inverse=True)
    verts, welded = np.unique(
        vertices[used].astype(np.float64), axis=0, return_inverse=True
    )
    tris = welded[remap].reshape((-1, 3))
    # Canonical cyclic order keeps winding, including when duplicate faces
    # arrive with opposite winding. Lexical order makes that choice repeatable.
    tris = np.take_along_axis(
        tris, (tris.argmin(axis=1)[:, None] + np.arange(3)) % 3, axis=1
    )
    tris = tris[np.lexsort(tris.T[::-1])]
    _, unique = np.unique(np.sort(tris, axis=1), axis=0, return_index=True)
    tris = tris[np.sort(unique)]
    # Translation before products avoids avoidable precision loss at an offset.
    verts = verts - verts[0]
    tri = verts[tris]
    twice_area = np.linalg.norm(
        np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1
    )
    diagonal_squared = float(np.sum(np.ptp(verts, axis=0) ** 2))
    tris = tris[twice_area > diagonal_squared * _AREA_RELATIVE]
    if not len(tris):
        raise GeometryError("degenerate_surface")
    used, remap = np.unique(tris, return_inverse=True)
    return verts[used], remap.reshape((-1, 3))


def _fingerprint(vertices: NDArray[Any], faces: NDArray[Any]) -> MeshFingerprint:
    import numpy as np

    verts, tris = _clean(vertices, faces)
    tri = verts[tris]
    areas = (
        np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        / 2
    )
    area = float(areas.sum())
    weights = areas / area
    centroid = np.sum(tri.mean(axis=1) * weights[:, None], axis=0)
    verts -= centroid
    tri = verts[tris]
    # Exact second moment of uniform points on each triangle, weighted by area;
    # vertex PCA would overweight densely tessellated patches of the surface.
    sums = tri.sum(axis=1)
    moment = (
        np.einsum("fvi,fvj,f->ij", tri, tri, weights)
        + np.einsum("fi,fj,f->ij", sums, sums, weights)
    ) / 12
    eigenvalues, frame = np.linalg.eigh(moment)
    ambiguous = bool(
        np.any(np.diff(eigenvalues) <= eigenvalues[-1] * _PCA_GAP_RELATIVE)
    )
    # eigh may return an improper basis. Keep reflection hypotheses separate.
    frame[:, 0] *= np.linalg.det(frame)
    oriented = verts @ frame
    diagonal = float(np.linalg.norm(np.ptp(oriented, axis=0)))
    normalized = oriented / diagonal

    keys: CanonicalKeys | None = None
    unavailable = [
        ("sh", "uncalibrated_basis"),
        ("view_hashes", "not_implemented"),
        ("convex_hull_ratio", "not_implemented"),
        ("volumetric_inertia", "not_implemented"),
        ("components", "not_implemented"),
    ]
    if ambiguous:
        unavailable.append(("canonical_keys", "ambiguous_frame"))
        # Eigenvalue ratios and normalized areas are content-derived invariants,
        # including when no orientation can be chosen reliably.
        seed_data = (
            np.round(np.r_[eigenvalues / eigenvalues[-1], np.sort(weights)], 6)
            .astype("<f8")
            .tobytes()
        )
        sample_tri = tri / diagonal
    else:
        keys, sample_tri, seed_data = _canonical_keys(normalized, tris, diagonal)

    seed = int.from_bytes(hashlib.sha256(seed_data).digest()[:8], "little")
    d2 = _d2(sample_tri, seed, diagonal)
    edges = tris[:, ((0, 1), (1, 2), (2, 0))].reshape((-1, 2))
    unique_edges, inverse, counts = np.unique(
        np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    watertight = bool(np.all(counts == 2))
    direction_sums = np.bincount(
        inverse, weights=np.where(edges[:, 0] < edges[:, 1], 1, -1)
    )
    winding_consistent = bool(
        np.all(direction_sums[counts == 2] == 0) and np.all(counts <= 2)
    )
    volume: float | None = None
    volume_reason: str | None = None
    if not watertight:
        volume_reason = "not_watertight"
    elif not winding_consistent:
        volume_reason = "inconsistent_winding"
    else:
        estimate = abs(
            float(
                np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum()
                / 6
            )
        )
        if estimate <= diagonal**3 * _AREA_RELATIVE:
            volume_reason = "degenerate_volume"
        else:
            volume = estimate
    metrics = SurfaceMetrics(
        vertex_count=len(verts),
        face_count=len(tris),
        euler_characteristic=len(verts) - len(unique_edges) + len(tris),
        watertight=watertight,
        winding_consistent=winding_consistent,
        surface_area=area,
        bbox_dimensions=tuple(float(x) for x in np.ptp(verts, axis=0)),
        volume=volume,
        volume_reason=volume_reason,
        area_volume_ratio=area**1.5 / volume if volume is not None else None,
        surface_eigenvalue_ratios=tuple(
            float(x) for x in eigenvalues / eigenvalues[-1]
        ),
    )
    return MeshFingerprint(
        ALGORITHM_VERSION,
        np.__version__,
        keys,
        ambiguous,
        metrics,
        d2,
        tuple(unavailable),
    )


def _canonical_keys(
    vertices: FloatArray, faces: IntArray, diagonal: float
) -> tuple[CanonicalKeys, FloatArray, bytes]:
    import numpy as np

    # Relative grid + absolute diagonal token retains physical scale. Six
    # significant digits tolerate common ASCII STL precision without dropping
    # the scale entirely. This is a retrieval key, not an equality tolerance.
    scale = format(diagonal, ".6g").encode("ascii")
    physical: list[list[str]] = [[], [], [], []]
    normalized: list[list[str]] = [[], [], [], []]
    sample_options: list[tuple[bytes, FloatArray]] = []
    for signs in ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)):
        oriented = vertices * signs
        for insensitive in (False, True):
            for offset in (0.0, 0.5):
                quantized = np.floor(oriented / _GRID_RELATIVE + offset).astype(
                    np.int64
                )
                # Unique positions give numeric lexicographic corner order,
                # independent of vertex indices, face order and host endian.
                positions, ids = np.unique(quantized, axis=0, return_inverse=True)
                face_ids = ids[faces]
                if insensitive:
                    corners = np.argsort(face_ids, axis=1, kind="stable")
                else:
                    corners = (face_ids.argmin(axis=1)[:, None] + np.arange(3)) % 3
                ordered = np.take_along_axis(face_ids, corners, axis=1)
                order = np.lexsort(ordered.T[::-1])
                data = positions[ordered[order]].astype("<i8").tobytes()
                slot = 2 * int(insensitive) + int(offset != 0)
                prefix = f"{ALGORITHM_VERSION}:{slot}:".encode("ascii")
                normalized[slot].append(hashlib.sha256(prefix + data).hexdigest())
                physical[slot].append(
                    hashlib.sha256(prefix + scale + b":" + data).hexdigest()
                )
                if insensitive and offset == 0:
                    triangles = np.take_along_axis(
                        oriented[faces], corners[:, :, None], axis=1
                    )[order]
                    sample_options.append((data, triangles))
    seed_data, sample_tri = min(sample_options, key=lambda item: item[0])
    return (
        CanonicalKeys(
            tuple(min(x) for x in physical), tuple(min(x) for x in normalized)
        ),
        sample_tri,
        seed_data,
    )


def _d2(triangles: FloatArray, seed: int, diagonal: float) -> D2Descriptor:
    import numpy as np

    areas = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
        axis=1,
    )
    cdf = np.cumsum(areas / areas.sum())
    cdf[-1] = 1.0
    rng = np.random.Generator(np.random.PCG64(seed))
    selected = triangles[np.searchsorted(cdf, rng.random(_D2_PAIRS * 2))]
    uv = rng.random((_D2_PAIRS * 2, 2))
    root = np.sqrt(uv[:, 0])
    weights = np.column_stack((1 - root, root * (1 - uv[:, 1]), root * uv[:, 1]))
    points = np.einsum("fi,fij->fj", weights, selected)
    distances = np.linalg.norm(points[:_D2_PAIRS] - points[_D2_PAIRS:], axis=1)
    mean = float(distances.mean())
    bins = np.minimum(
        (distances / mean * (_D2_BINS / 4)).astype(np.int64), _D2_BINS - 1
    )
    histogram = np.bincount(bins, minlength=_D2_BINS) / _D2_PAIRS
    return D2Descriptor(tuple(float(x) for x in histogram), mean * diagonal, seed)
