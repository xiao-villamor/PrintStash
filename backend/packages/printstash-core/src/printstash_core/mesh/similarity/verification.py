"""Geometric verification separates retrieval keys from evidence of equivalence.

Correspondences are demonstrated by positions and triangle incidence. PCA/ICP
are alignment hypotheses, never proof by themselves. Distances are estimates
from independent surface samples, with counts and seeds retained in evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations, product
from typing import TYPE_CHECKING, Any, Literal

from .fingerprint import GeometryError
from .geometry import (
    Surface,
    equivalent_triangles,
    measure_surface,
    nearest_neighbors,
    prepare_surface,
    sample_surface,
)
from .proximity import SurfaceProximity
from .voxel import voxelize

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]

EvidenceClass = Literal[
    "identical_geometry",
    "rescaled",
    "mirrored",
    "rescaled_mirrored",
    "remeshed",
    "repaired",
    "similar_shape",
    "component_of",
    "plate_of",
]
VERIFICATION_VERSION = "surface-verification-v3"


@dataclass(frozen=True)
class Verification:
    evidence_class: EvidenceClass | None
    confidence: float
    scale_factor: float
    mirrored: bool
    mirror_ambiguous: bool
    exact_equivalence: bool
    transform: tuple[tuple[float, ...], ...]
    sampled_hausdorff: float
    sampled_chamfer: float
    voxel_iou: float | None
    convergence_error: float
    sample_points: int
    fit_seed: int = 154
    evaluation_seeds: tuple[int, int] = (15401, 15402)
    tolerance_relative: float = 1e-6
    version: str = VERIFICATION_VERSION
    distance_normalization: str = "pca_bbox_diagonal"
    sampled_hausdorff_mm: float = 0.0
    sampled_chamfer_mm: float = 0.0
    unavailable: tuple[tuple[str, str], ...] = ()
    sampled_surface_chamfer: float = 0.0
    sampled_surface_hausdorff: float = 0.0


def verify_meshes(
    left_vertices: NDArray[Any],
    left_faces: NDArray[Any],
    right_vertices: NDArray[Any],
    right_faces: NDArray[Any],
    *,
    sample_points: int = 5000,
    partial: bool = False,
) -> Verification:
    """Compare right to left; scale_factor is right-size / left-size.

    The returned homogeneous transform maps original right coordinates onto the
    left geometry. Partial samples can never establish exact equivalence.
    """
    import numpy as np

    if type(sample_points) is not int or not 256 <= sample_points <= 5000:
        raise GeometryError("invalid_sample_count")
    left, right = (
        prepare_surface(left_vertices, left_faces),
        prepare_surface(right_vertices, right_faces),
    )
    factor = right.radius / left.radius
    scale = 1 / factor
    diagonal = float(np.linalg.norm(np.ptp(left.vertices @ left.frame, axis=0)))
    tolerance = diagonal * 1e-6
    fit_a = sample_surface(left, 512, 154) / diagonal
    fit_b = sample_surface(right, 512, 154) * scale / diagonal
    proposals = list(_proposals(left, right, scale))
    ranked = sorted(
        (
            (
                float(nearest_neighbors(fit_b[:64] @ rotation, fit_a[:128])[0].mean()),
                index,
                rotation,
            )
            for index, rotation in enumerate(proposals)
        ),
        key=lambda item: (item[0], item[1]),
    )
    exact: dict[bool, FloatArray] = {}
    for _, _, rotation in ranked:
        reflected = bool(np.linalg.det(rotation) < 0)
        if (
            not partial
            and reflected not in exact
            and equivalent_triangles(
                left, right, rotation, scale, np.zeros(3), tolerance
            )
        ):
            exact[reflected] = rotation
        if len(exact) == 2:
            break
    proximity_a, proximity_b = SurfaceProximity(left), SurfaceProximity(right)
    if exact:
        reflected = False if False in exact else True
        rotation = exact[reflected]
        offset, convergence = np.zeros(3), 0.0
    else:
        # Sample-to-sample ICP has a tessellation-dependent noise floor. Keep
        # original PCA hypotheses and refine against actual triangle surfaces.
        # This prevents moving a correctly aligned subdivision off its surface.
        aligned: dict[bool, tuple[float, FloatArray, FloatArray, float]] = {}
        fit = sample_surface(right, 512, 154) * scale
        for parity in (False, True):
            hypotheses = [
                r for _, _, r in ranked if bool(np.linalg.det(r) < 0) == parity
            ][:4]
            initial = min(
                hypotheses,
                key=lambda r: float(proximity_a.closest(fit[:256] @ r)[0].mean()),
            )
            aligned[parity] = _surface_icp(proximity_a, fit, initial, diagonal)
        reflected = aligned[True][0] < aligned[False][0] * 0.8
        _, rotation, offset, convergence = aligned[reflected]
    a = sample_surface(left, sample_points, 15401)
    b = sample_surface(right, sample_points, 15402) @ rotation * scale + offset
    distance_ab = nearest_neighbors(a / diagonal, b / diagonal)[0]
    distance_ba = nearest_neighbors(b / diagonal, a / diagonal)[0]
    hausdorff = float(max(distance_ab.max(), distance_ba.max()))
    chamfer = float((distance_ab.mean() + distance_ba.mean()) / 2)
    surface_ab = (
        proximity_b.closest((a - offset) @ rotation.T / scale)[0] * scale / diagonal
    )
    surface_ba = proximity_a.closest(b)[0] / diagonal
    surface_chamfer = float((surface_ab.mean() + surface_ba.mean()) / 2)
    surface_hausdorff = float(max(surface_ab.max(), surface_ba.max()))
    metrics_a = measure_surface(left)
    metrics_b = measure_surface(right)
    half_width = (
        float(
            max(
                np.linalg.norm(left.vertices, axis=1).max(),
                np.linalg.norm(
                    right.vertices @ rotation * scale + offset, axis=1
                ).max(),
            )
        )
        * 1.01
    )
    vox_a = None
    unavailable: list[tuple[str, str]] = []
    fill = metrics_a.watertight and metrics_b.watertight
    try:
        vox_a = voxelize(left.vertices, left.faces, half_width=half_width, fill=fill)
        vox_b = voxelize(
            right.vertices @ rotation * scale + offset,
            right.faces,
            half_width=half_width,
            fill=fill,
        )
        union = int(np.count_nonzero(vox_a | vox_b))
        iou = float(np.count_nonzero(vox_a & vox_b) / union) if union else None
    except GeometryError as exc:
        iou = None
        unavailable.append(("voxel_iou", exc.code))
    mirror_ambiguous = len(exact) == 2
    # A symmetric surface can have chiral triangle diagonals. An alternative
    # parity with indistinguishable occupied volume makes reflection history
    # ambiguous even when triangle incidence differs. This is uncertainty,
    # never a second exact-equivalence proof.
    if (
        exact
        and not mirror_ambiguous
        and fill
        and iou is not None
        and vox_a is not None
    ):
        alternatives = [
            r for _, _, r in ranked if bool(np.linalg.det(r) < 0) != reflected
        ][:8]
        for alternative in alternatives:
            try:
                other = voxelize(
                    right.vertices @ alternative * scale,
                    right.faces,
                    half_width=half_width,
                )
            except GeometryError as exc:
                if ("mirror_ambiguity", exc.code) not in unavailable:
                    unavailable.append(("mirror_ambiguity", exc.code))
                continue
            other_union = int(np.count_nonzero(vox_a | other))
            if other_union and np.count_nonzero(vox_a & other) / other_union >= 0.999:
                mirror_ambiguous = True
                break
    scaled = abs(factor - 1) > 1e-6
    evidence: EvidenceClass | None = None
    confidence = 0.0
    if exact:
        evidence = (
            ("rescaled_mirrored" if scaled else "mirrored")
            if reflected
            else ("rescaled" if scaled else "identical_geometry")
        )
        confidence = 1.0
        if reflected and mirror_ambiguous:
            evidence, confidence = "remeshed", 0.99
    elif (
        surface_chamfer < 0.004
        and surface_hausdorff < 0.025
        and iou is not None
        and iou >= 0.94
    ):
        if (
            metrics_a.watertight != metrics_b.watertight
            or metrics_a.euler_characteristic != metrics_b.euler_characteristic
        ):
            evidence = "repaired"
        elif metrics_a.face_count != metrics_b.face_count:
            evidence = "remeshed"
        else:
            evidence = "similar_shape"
        confidence = min(0.99, 0.8 + 0.19 * iou)
    elif chamfer < 0.03 and hausdorff < 0.08 and iou is not None and iou > 0.9:
        evidence, confidence = "similar_shape", min(0.95, iou)
    transform = np.eye(4)
    transform[:3, :3] = rotation.T * scale
    transform[:3, 3] = left.centroid + offset - right.centroid @ rotation * scale
    return Verification(
        evidence,
        confidence,
        factor,
        reflected,
        mirror_ambiguous,
        bool(exact),
        tuple(tuple(float(x) for x in row) for row in transform),
        hausdorff,
        chamfer,
        iou,
        convergence,
        sample_points,
        sampled_hausdorff_mm=hausdorff * diagonal,
        sampled_chamfer_mm=chamfer * diagonal,
        unavailable=tuple(unavailable),
        sampled_surface_chamfer=surface_chamfer,
        sampled_surface_hausdorff=surface_hausdorff,
    )


def _proposals(left: Surface, right: Surface, scale: float):
    import numpy as np

    # Exports commonly change only the origin or units. Float32 STL rounding
    # perturbs PCA axes slightly; the unchanged axes are a useful independent
    # hypothesis. Full vertex/triangle correspondence still has to prove it.
    yield np.eye(3)
    for permutation in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            yield right.frame @ (np.eye(3)[:, permutation] * signs) @ left.frame.T
    # Congruent triangle frames handle degenerate PCA (e.g. a rotated cube).
    # Matching lengths propose correspondences; equivalent_triangles verifies
    # the complete mesh before any exact Evidence Class is emitted.
    a = left.vertices[left.faces[int(np.argmax(left.areas))]]
    wanted = np.sort(np.linalg.norm(a - np.roll(a, 1, axis=0), axis=1))
    triangles = right.vertices[right.faces] * scale
    lengths = np.sort(
        np.linalg.norm(triangles - np.roll(triangles, 1, axis=1), axis=2), axis=1
    )
    eligible = np.flatnonzero(
        np.all(np.abs(lengths - wanted) < left.radius * 1e-6, axis=1)
    )[:8]
    frame_a = _triangle_frame(a)
    for index in eligible:
        for order in permutations(range(3)):
            frame_b = _triangle_frame(triangles[index, list(order)])
            for sign in (1, -1):
                yield frame_b @ np.diag([1, 1, sign]) @ frame_a.T


def _triangle_frame(triangle: FloatArray) -> FloatArray:
    import numpy as np

    x = triangle[1] - triangle[0]
    x /= np.linalg.norm(x)
    z = np.cross(x, triangle[2] - triangle[0])
    z /= np.linalg.norm(z)
    return np.column_stack((x, np.cross(z, x), z))


def _surface_icp(
    proximity: SurfaceProximity,
    points: FloatArray,
    rotation: FloatArray,
    diagonal: float,
) -> tuple[float, FloatArray, FloatArray, float]:
    import numpy as np

    r, offset = rotation.copy(), np.zeros(3)
    distances, target = proximity.closest(points @ r)
    best = (float(distances.mean()) / diagonal, r.copy(), offset.copy(), 0.0)
    for _ in range(8):
        moved = points @ r + offset
        center_a, center_b = moved.mean(axis=0), target.mean(axis=0)
        u, _, vt = np.linalg.svd((moved - center_a).T @ (target - center_b))
        correction = u @ np.diag([1, 1, np.linalg.det(u @ vt)]) @ vt
        r = r @ correction
        offset = (offset - center_a) @ correction + center_b
        distances, target = proximity.closest(points @ r + offset)
        error = float(distances.mean()) / diagonal
        if error >= best[0] - 1e-9:
            break
        best = (error, r.copy(), offset.copy(), error)
    return best
