"""Disposable numeric LOD experiment; never publishes or replaces source geometry.

Run with the optional candidate in an isolated environment. Surface errors are
finite deterministic samples, not a Hausdorff bound or a printability guarantee.
The caller supervises this entire finite CLI process (including native crashes).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from printstash_core.mesh.similarity.fingerprint import fingerprint_mesh
from printstash_core.mesh.similarity.geometry import prepare_surface, sample_surface
from printstash_core.mesh.similarity.proximity import SurfaceProximity
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import KDTree

if TYPE_CHECKING:
    from trimesh import Trimesh

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
MAX_FACES = 10_000
MAX_SAMPLES = 2_048
CANDIDATE_VERSION = "0.2.0"
MAX_BOUNDARY_PRECISION_MM = 1e-6


class _Simplifier(Protocol):
    def simplify(
        self,
        points: FloatArray,
        triangles: IntArray,
        *,
        target_reduction: float,
        agg: float,
        preserve_border: bool,
    ) -> tuple[FloatArray, NDArray[np.int32] | IntArray]: ...


class Family(StrEnum):
    SHARP_CUBE = "sharp_cube"
    TORUS_HOLE = "torus_hole"
    OPEN_BORDER = "open_border"
    THIN_SOLID = "thin_solid"
    TINY_COMPONENT = "tiny_component"


class Failure(StrEnum):
    COMPONENTS = "component_count"
    TOPOLOGY = "component_euler"
    BOUNDARY = "boundary_profile"
    SURFACE = "sampled_surface_error"
    CUBE = "sharp_cube_features"
    HOLE = "torus_bore"
    THICKNESS = "thin_solid_thickness"
    TINY = "tiny_component_extent"
    INVALID_ARRAYS = "invalid_candidate_arrays"
    DEGENERATE = "degenerate_candidate_triangles"


@dataclass(frozen=True)
class Mesh:
    vertices: FloatArray
    faces: IntArray

    def __post_init__(self) -> None:
        if (
            self.vertices.ndim != 2
            or self.vertices.shape[1] != 3
            or self.faces.ndim != 2
            or self.faces.shape[1] != 3
            or not 1 <= len(self.faces) <= MAX_FACES
            or not 1 <= len(self.vertices) <= MAX_FACES * 3
            or self.vertices.dtype != np.float64
            or self.faces.dtype != np.int64
            or not np.isfinite(self.vertices).all()
            or self.faces.min() < 0
            or self.faces.max() >= len(self.vertices)
        ):
            raise ValueError("invalid bounded pilot mesh")


@dataclass(frozen=True)
class Limits:
    reduction: float
    samples: int = 512
    aggression: float = 3

    def __post_init__(self) -> None:
        if (
            not np.isfinite(self.reduction)
            or not 0.1 <= self.reduction <= 0.9
            or type(self.samples) is not int
            or not 32 <= self.samples <= MAX_SAMPLES
            or not np.isfinite(self.aggression)
            or not 1 <= self.aggression <= 7
        ):
            raise ValueError("pilot limits exceeded")


@dataclass(frozen=True)
class Component:
    faces: int
    euler: int
    low: tuple[float, float, float]
    high: tuple[float, float, float]


@dataclass(frozen=True)
class Stats:
    vertices: int
    faces: int
    components: tuple[Component, ...]
    boundary_edges: int
    boundary_loops: int
    boundary_regular: bool
    nonmanifold_edges: int
    inconsistent_winding_edges: int
    crease_length: float
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(frozen=True)
class Distances:
    source_to_target_max: float
    target_to_source_max: float
    source_to_target_p95: float
    target_to_source_p95: float
    source_points: int
    target_points: int


@dataclass(frozen=True)
class ClosedBoundary:
    tolerance_mm: float
    state: Literal["closed"] = "closed"


@dataclass(frozen=True)
class MissingBoundary:
    source_points: int
    target_points: int
    tolerance_mm: float
    state: Literal["missing"] = "missing"


@dataclass(frozen=True)
class ComparedBoundary:
    source_points: int
    target_points: int
    tolerance_mm: float
    max_position_delta_mm: float
    mapping_bijective: bool
    mapped_edges_identical: bool
    state: Literal["compared"] = "compared"


BoundaryEvidence = ClosedBoundary | MissingBoundary | ComparedBoundary


@dataclass(frozen=True)
class Quality:
    source: Stats
    target: Stats
    distances: Distances
    failures: tuple[Failure, ...]
    feature_probe_distances: tuple[float, ...]
    ray_intersections: tuple[tuple[float, ...], ...]
    surface_tolerance_mm: float
    boundary: BoundaryEvidence
    state: Literal["measured"] = "measured"


@dataclass(frozen=True)
class InvalidArrayQuality:
    source: Stats
    target_vertices: int
    target_faces: int
    failures: tuple[Failure, ...] = (Failure.INVALID_ARRAYS,)
    state: Literal["invalid_arrays"] = "invalid_arrays"


@dataclass(frozen=True)
class DegenerateQuality:
    source: Stats
    target_vertices: int
    target_faces: int
    zero_area_triangles: int
    failures: tuple[Failure, ...] = (Failure.DEGENERATE,)
    state: Literal["degenerate_triangles"] = "degenerate_triangles"


def _owned(mesh: Trimesh) -> Mesh:
    vertices = np.array(mesh.vertices, dtype=np.float64, copy=True)
    faces = np.array(mesh.faces, dtype=np.int64, copy=True)
    vertices.setflags(write=False)
    faces.setflags(write=False)
    return Mesh(vertices, faces)


def _box(extents: tuple[float, float, float], subdivisions: int) -> Mesh:
    import trimesh

    mesh = trimesh.creation.box(extents=extents)
    for _ in range(subdivisions):
        mesh = mesh.subdivide()
    return _owned(mesh)


def source_mesh(family: Family) -> Mesh:
    """Deterministic physical millimetre fixtures with independently known features."""
    import trimesh

    match family:
        case Family.SHARP_CUBE:
            return _box((10, 10, 10), 3)
        case Family.THIN_SOLID:
            return _box((20, 10, 0.2), 3)
        case Family.TORUS_HOLE:
            return _owned(trimesh.creation.torus(10, 2, 48, 16))
        case Family.OPEN_BORDER:
            theta = np.arange(64) * (2 * np.pi / 64)
            vertices = np.array(
                [
                    (4 * np.cos(t), 4 * np.sin(t), z)
                    for z in np.linspace(-5, 5, 6)
                    for t in theta
                ],
                dtype=np.float64,
            )
            faces = np.array(
                [
                    (
                        ring * 64 + i,
                        ring * 64 + (i + 1) % 64,
                        (ring + 1) * 64 + (i + 1) % 64,
                    )
                    for ring in range(5)
                    for i in range(64)
                ]
                + [
                    (ring * 64 + i, (ring + 1) * 64 + (i + 1) % 64, (ring + 1) * 64 + i)
                    for ring in range(5)
                    for i in range(64)
                ],
                dtype=np.int64,
            )
            vertices.setflags(write=False)
            faces.setflags(write=False)
            return Mesh(vertices, faces)
        case Family.TINY_COMPONENT:
            large = _box((10, 10, 10), 2)
            small = _box((0.2, 0.2, 0.2), 2)
            vertices = np.concatenate((large.vertices, small.vertices + (20, 0, 0)))
            faces = np.concatenate((large.faces, small.faces + len(large.vertices)))
            vertices.setflags(write=False)
            faces.setflags(write=False)
            return Mesh(vertices, faces)


def array_digest(mesh: Mesh) -> str:
    digest = hashlib.sha256()
    digest.update(np.asarray(mesh.vertices, dtype="<f8").tobytes())
    digest.update(np.asarray(mesh.faces, dtype="<i8").tobytes())
    return digest.hexdigest()


def reference_digest(mesh: Mesh) -> tuple[str, str]:
    """Real canonical source descriptor, never computed from simplified geometry."""
    result = fingerprint_mesh(mesh.vertices, mesh.faces)
    payload = json.dumps(asdict(result), sort_keys=True, allow_nan=False).encode()
    return result.algorithm_version, hashlib.sha256(payload).hexdigest()


def _edges(mesh: Mesh) -> tuple[IntArray, IntArray, IntArray]:
    edges = np.sort(mesh.faces[:, ((0, 1), (1, 2), (2, 0))].reshape(-1, 2), axis=1)
    unique, inverse, counts = np.unique(
        edges, axis=0, return_inverse=True, return_counts=True
    )
    return unique, inverse, counts


def _labels(edges: IntArray, count: int) -> IntArray:
    graph = coo_matrix(
        (np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(count, count)
    )
    return np.asarray(connected_components(graph, directed=False)[1], dtype=np.int64)


def boundary_points(mesh: Mesh) -> FloatArray:
    edges, _, counts = _edges(mesh)
    return mesh.vertices[np.unique(edges[counts == 1])]


def _validate_boundary_tolerance(tolerance_mm: float) -> None:
    if (
        type(tolerance_mm) not in (int, float)
        or not np.isfinite(tolerance_mm)
        or not 0 < tolerance_mm <= MAX_BOUNDARY_PRECISION_MM
    ):
        raise ValueError("boundary tolerance exceeds strict position precision cap")


def _boundary_evidence(
    source: Mesh, target: Mesh, tolerance_mm: float
) -> BoundaryEvidence:
    left_edges, _, left_counts = _edges(source)
    right_edges, _, right_counts = _edges(target)
    left_edges, right_edges = (
        left_edges[left_counts == 1],
        right_edges[right_counts == 1],
    )
    left_ids, right_ids = np.unique(left_edges), np.unique(right_edges)
    if not len(left_ids) and not len(right_ids):
        return ClosedBoundary(tolerance_mm)
    if not len(left_ids) or not len(right_ids):
        return MissingBoundary(len(left_ids), len(right_ids), tolerance_mm)
    left, right = source.vertices[left_ids], target.vertices[right_ids]
    forward, matches = KDTree(left).query(right)
    matches = np.asarray(matches, dtype=np.int64)
    reverse, _ = KDTree(right).query(left)
    delta = float(max(forward.max(), reverse.max()))
    bijective = len(left_ids) == len(right_ids) and len(np.unique(matches)) == len(
        left_ids
    )
    edges_identical = False
    if bijective:
        # Compare the actual edge graph after geometric vertex correspondence.
        # Float32 ties may reorder lexicographic coordinates without changing it.
        mapped = np.sort(
            left_ids[matches[np.searchsorted(right_ids, right_edges)]], axis=1
        )
        edges_identical = bool(
            np.array_equal(
                mapped[np.lexsort(mapped.T[::-1])],
                left_edges[np.lexsort(left_edges.T[::-1])],
            )
        )
    return ComparedBoundary(
        len(left_ids), len(right_ids), tolerance_mm, delta, bijective, edges_identical
    )


def mesh_stats(mesh: Mesh) -> Stats:
    edges, inverse, counts = _edges(mesh)
    labels = _labels(edges, len(mesh.vertices))
    components: list[Component] = []
    for label in np.unique(labels[mesh.faces[:, 0]]):
        faces = mesh.faces[labels[mesh.faces[:, 0]] == label]
        used = np.unique(faces)
        component_edges = edges[labels[edges[:, 0]] == label]
        points = mesh.vertices[used]
        components.append(
            Component(
                len(faces),
                len(used) - len(component_edges) + len(faces),
                tuple(points.min(axis=0)),
                tuple(points.max(axis=0)),
            )
        )
    components.sort(key=lambda item: (-item.faces, item.low))
    boundary = edges[counts == 1]
    boundary_ids = np.unique(boundary)
    boundary_labels = _labels(boundary, len(mesh.vertices))
    loops = len(np.unique(boundary_labels[boundary_ids]))
    degrees = np.bincount(boundary.reshape(-1), minlength=len(mesh.vertices))
    regular = bool(np.all(degrees[boundary_ids] == 2))
    triangles = mesh.vertices[mesh.faces]
    normals = np.cross(
        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    )
    norm = np.linalg.norm(normals, axis=1)
    if np.any(norm == 0):
        raise ValueError("degenerate pilot triangle")
    normals /= norm[:, None]
    order = np.argsort(inverse, kind="stable")
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    paired = starts[counts == 2]
    first, second = order[paired] // 3, order[paired + 1] // 3
    sharp = np.einsum("ij,ij->i", normals[first], normals[second]) < 0.5
    directed = mesh.faces[:, ((0, 1), (1, 2), (2, 0))].reshape(-1, 2)
    direction = directed[:, 0] < directed[:, 1]
    inconsistent = int(
        np.count_nonzero(direction[order[paired]] == direction[order[paired + 1]])
    )
    selected = edges[counts == 2][sharp]
    length = float(
        np.linalg.norm(
            mesh.vertices[selected[:, 0]] - mesh.vertices[selected[:, 1]], axis=1
        ).sum()
    )
    used = mesh.vertices[np.unique(mesh.faces)]
    return Stats(
        len(used),
        len(mesh.faces),
        tuple(components),
        len(boundary),
        loops,
        regular,
        int(np.count_nonzero(counts > 2)),
        inconsistent,
        length,
        (tuple(used.min(axis=0)), tuple(used.max(axis=0))),
    )


def surface_distances(mesh: Mesh, points: FloatArray) -> FloatArray:
    surface = prepare_surface(mesh.vertices, mesh.faces)
    return SurfaceProximity(surface).closest(points - surface.centroid)[0]


def _sample_points(mesh: Mesh, count: int) -> FloatArray:
    surface = prepare_surface(mesh.vertices, mesh.faces)
    samples = sample_surface(surface, count, seed=20261005) + surface.centroid
    # Vertices supplement area samples so tiny disconnected parts are not hidden.
    used = mesh.vertices[np.unique(mesh.faces)]
    if len(used) + count > 5_000:
        raise ValueError("proximity point budget exceeded")
    return np.concatenate((samples, used))


def _ray_z(mesh: Mesh, x: float, y: float) -> tuple[float, ...]:
    """Intersect an infinite vertical line with triangle interiors and boundaries."""
    triangles = mesh.vertices[mesh.faces]
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    ab, ac = b - a, c - a
    det = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
    valid = np.abs(det) > 1e-12
    a, ab, ac, det = a[valid], ab[valid], ac[valid], det[valid]
    dx, dy = x - a[:, 0], y - a[:, 1]
    u = (dx * ac[:, 1] - dy * ac[:, 0]) / det
    v = (ab[:, 0] * dy - ab[:, 1] * dx) / det
    inside = (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)
    z = a[inside, 2] + u[inside] * ab[inside, 2] + v[inside] * ac[inside, 2]
    return tuple(float(item) for item in np.unique(np.round(z, 9)))


def assess(
    family: Family,
    source: Mesh,
    target: Mesh,
    *,
    samples: int = 512,
    boundary_tolerance_mm: float = 1e-8,
) -> Quality:
    _validate_boundary_tolerance(boundary_tolerance_mm)
    Limits(0.5, samples)
    before, after = mesh_stats(source), mesh_stats(target)
    boundary = _boundary_evidence(source, target, boundary_tolerance_mm)
    failures: list[Failure] = []
    if len(before.components) != len(after.components):
        failures.append(Failure.COMPONENTS)
    if sorted(item.euler for item in before.components) != sorted(
        item.euler for item in after.components
    ):
        failures.append(Failure.TOPOLOGY)
    if (
        after.nonmanifold_edges > before.nonmanifold_edges
        or after.inconsistent_winding_edges > before.inconsistent_winding_edges
    ):
        if Failure.TOPOLOGY not in failures:
            failures.append(Failure.TOPOLOGY)
    if (
        before.boundary_loops != after.boundary_loops
        or before.boundary_regular != after.boundary_regular
    ):
        failures.append(Failure.BOUNDARY)
    points_a, points_b = (
        _sample_points(source, samples),
        _sample_points(target, samples),
    )
    ab, ba = surface_distances(target, points_a), surface_distances(source, points_b)
    tolerance = 0.005 if family is Family.TINY_COMPONENT else 0.05
    if max(float(ab.max()), float(ba.max())) > tolerance:
        failures.append(Failure.SURFACE)
    probes: tuple[float, ...] = ()
    rays: tuple[tuple[float, ...], ...] = ()
    match family:
        case Family.SHARP_CUBE:
            corners = np.array(
                [(x, y, z) for x in (-5, 5) for y in (-5, 5) for z in (-5, 5)],
                dtype=np.float64,
            )
            probes = tuple(float(item) for item in surface_distances(target, corners))
            if max(probes) > 1e-6 or abs(after.crease_length - 120) > 0.01:
                failures.append(Failure.CUBE)
        case Family.TORUS_HOLE:
            rays = tuple(
                _ray_z(target, x, y)
                for x, y in ((0, 0), (7.5, 0), (-7.5, 0), (0, 7.5), (0, -7.5))
            )
            if any(rays):
                failures.append(Failure.HOLE)
        case Family.OPEN_BORDER:
            if (
                not isinstance(boundary, ComparedBoundary)
                or not boundary.mapping_bijective
                or not boundary.mapped_edges_identical
                or boundary.max_position_delta_mm > boundary.tolerance_mm
            ):
                if Failure.BOUNDARY not in failures:
                    failures.append(Failure.BOUNDARY)
        case Family.THIN_SOLID:
            rays = tuple(
                _ray_z(target, x, y) for x, y in ((0.3, 0.4), (7.5, 2), (-7.5, -2))
            )
            if any(len(ray) != 2 or abs(ray[-1] - ray[0] - 0.2) > 1e-6 for ray in rays):
                failures.append(Failure.THICKNESS)
        case Family.TINY_COMPONENT:
            small = np.array(
                [
                    (x, y, z)
                    for x in (19.9, 20.1)
                    for y in (-0.1, 0.1)
                    for z in (-0.1, 0.1)
                ],
                dtype=np.float64,
            )
            probes = tuple(float(item) for item in surface_distances(target, small))
            if max(probes) > 0.005:
                failures.append(Failure.TINY)
    distances = Distances(
        float(ab.max()),
        float(ba.max()),
        float(np.quantile(ab, 0.95)),
        float(np.quantile(ba, 0.95)),
        len(points_a),
        len(points_b),
    )
    return Quality(
        before, after, distances, tuple(failures), probes, rays, tolerance, boundary
    )


def _candidate_quality(
    family: Family,
    source: Mesh,
    vertices: FloatArray,
    faces: IntArray,
    *,
    samples: int,
    boundary_tolerance_mm: float = 1e-8,
) -> Quality | InvalidArrayQuality | DegenerateQuality:
    _validate_boundary_tolerance(boundary_tolerance_mm)
    # Source authority remains strict. Only candidate inadmissibility is report data.
    source_stats = mesh_stats(source)
    try:
        target = Mesh(vertices, faces)
    except ValueError:
        return InvalidArrayQuality(source_stats, len(vertices), len(faces))
    triangles = target.vertices[target.faces]
    normals = np.cross(
        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    )
    zero_area = int(np.count_nonzero(np.linalg.norm(normals, axis=1) == 0))
    if zero_area:
        return DegenerateQuality(source_stats, len(vertices), len(faces), zero_area)
    return assess(
        family,
        source,
        target,
        samples=samples,
        boundary_tolerance_mm=boundary_tolerance_mm,
    )


def run_case(
    family: Family, limits: Limits, *, source: Mesh | None = None
) -> dict[str, object]:
    fast_simplification = cast(_Simplifier, import_module("fast_simplification"))

    total_started = time.perf_counter()
    installed = version("fast-simplification")
    if installed != CANDIDATE_VERSION:
        raise ValueError(
            f"expected fast-simplification {CANDIDATE_VERSION}, got {installed}"
        )
    source = source_mesh(family) if source is None else source
    preparation_ms = (time.perf_counter() - total_started) * 1000
    reference_started = time.perf_counter()
    digest = array_digest(source)
    identity, reference_before = reference_digest(source)
    reference_ms = (time.perf_counter() - reference_started) * 1000
    started = time.perf_counter()
    vertices, faces = fast_simplification.simplify(
        source.vertices.copy(),
        source.faces.copy(),
        target_reduction=limits.reduction,
        agg=limits.aggression,
        preserve_border=True,
    )
    simplification_ms = (time.perf_counter() - started) * 1000
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    started = time.perf_counter()
    quality = _candidate_quality(
        family, source, vertices, faces, samples=limits.samples
    )
    verification_ms = (time.perf_counter() - started) * 1000
    reference_started = time.perf_counter()
    identity_after, reference_after = reference_digest(source)
    reference_ms += (time.perf_counter() - reference_started) * 1000
    if array_digest(source) != digest or (identity_after, reference_after) != (
        identity,
        reference_before,
    ):
        raise RuntimeError("candidate mutated the authoritative reference")
    return {
        "family": family.value,
        "candidate_version": installed,
        "parameters": asdict(limits) | {"preserve_border": True},
        "requested_faces": int(len(source.faces) * (1 - limits.reduction)),
        "attained_faces": len(faces),
        "attained_reduction": 1 - len(faces) / len(source.faces),
        "preparation_ms": preparation_ms,
        "reference_check_ms": reference_ms,
        "total_ms": (time.perf_counter() - total_started) * 1000,
        "simplification_ms": simplification_ms,
        "verification_ms": verification_ms,
        "source_array_sha256": digest,
        "source_array_sha256_after": array_digest(source),
        "canonical_source_fingerprint_version": identity,
        "canonical_source_fingerprint_sha256": reference_before,
        "canonical_source_fingerprint_sha256_after": reference_after,
        "quality": asdict(quality),
        "passes_declared_checks": not quality.failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--family", type=Family, action="append", choices=list(Family))
    parser.add_argument("--reduction", type=float, action="append")
    parser.add_argument("--samples", type=int, default=512)
    parser.add_argument("--aggression", type=float, default=3)
    args = parser.parse_args(argv)
    families = args.family if args.family is not None else list(Family)
    reductions = args.reduction if args.reduction is not None else [0.5, 0.8]
    if (
        len(set(families)) != len(families)
        or len(reductions) > 2
        or len(set(reductions)) != len(reductions)
    ):
        raise ValueError(
            "pilot allows unique families and at most two unique reductions"
        )
    limits = [Limits(value, args.samples, args.aggression) for value in reductions]
    rows = [run_case(family, limit) for family in families for limit in limits]
    report = {
        "schema_version": 1,
        "runtime_integration": False,
        "production_adoption": False,
        "limitations": [
            "Finite deterministic surface samples are not a Hausdorff bound.",
            "Analytic fixtures do not establish quality for every real model.",
            "No cached metadata, runtime similarity or original download is replaced.",
            "Caller supervises this finite native experiment process.",
        ],
        "cases": rows,
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
