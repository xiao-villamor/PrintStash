"""Physical measures over unique resources and bounded affine placements.

Raw closed resources retain independent index domains under composition, so
signed integrals add even for overlapping closed placements. Open resources
require a separate global topology evaluation; this owner never welds or repairs
individual pieces to infer whole-scene eligibility.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeUnavailable,
    VolumeUnavailableCause,
    volume_value,
)
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from printstash_core.mesh.similarity.components import ExpandedScene

from app.core.logging import get_logger
from app.modules.media.mesh_contracts import Geometry, MeshMeasurements
from app.modules.media.mesh_measurements import signed_mesh_integral
from app.modules.media.mesh_resources import PreparedScene

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray
    from trimesh import Trimesh

logger = get_logger(__name__)
_POINT_CHUNK_SIZE = 64_000


@dataclass(frozen=True)
class VolumeTopologyRequired:
    """Local resources cannot establish whole-scene closedness without welding."""


SceneVolume: TypeAlias = VolumeMeasured | VolumeUnavailable | VolumeTopologyRequired


@dataclass(frozen=True)
class SceneMeasurements:
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    triangle_count: int
    volume: SceneVolume

    def __post_init__(self) -> None:
        for bounds in (self.bounds_min, self.bounds_max):
            if (
                type(bounds) is not tuple
                or len(bounds) != 3
                or any(
                    type(value) is not float or not math.isfinite(value)
                    for value in bounds
                )
            ):
                raise ValueError("invalid_scene_bounds")
        if any(
            high < low or not math.isfinite(high - low)
            for low, high in zip(self.bounds_min, self.bounds_max, strict=True)
        ):
            raise ValueError("invalid_scene_bounds")
        if not isinstance(
            self.volume, (VolumeMeasured, VolumeUnavailable, VolumeTopologyRequired)
        ):
            raise TypeError("invalid_scene_volume_outcome")
        if (
            type(self.triangle_count) is not int
            or not 1 <= self.triangle_count <= MAX_ANALYSIS_FACES
        ):
            raise ValueError("invalid_scene_triangle_count")

    @property
    def geometry(self) -> Geometry:
        return {
            "bbox_x_mm": self.bounds_max[0] - self.bounds_min[0],
            "bbox_y_mm": self.bounds_max[1] - self.bounds_min[1],
            "bbox_z_mm": self.bounds_max[2] - self.bounds_min[2],
            "triangle_count": self.triangle_count,
            "volume_mm3": None
            if isinstance(self.volume, VolumeTopologyRequired)
            else volume_value(self.volume),
        }

    @property
    def measurements(self) -> MeshMeasurements:
        if isinstance(self.volume, VolumeTopologyRequired):
            raise ValueError("topology_resolution_required")
        return MeshMeasurements(self.geometry, self.volume)


def _resource_integral(mesh: Trimesh) -> tuple[float, int] | VolumeUnavailable:
    """Keep ordinary signed integrals; rescale only lost numeric range.

    The integer records the source coordinate exponent. Scaling by powers of
    two is exact and preserves component-local subtraction in the canonical kernel.
    Only a unique resource gets a temporary coordinate buffer, never placements.
    """
    import numpy as np
    import trimesh

    integral = signed_mesh_integral(mesh)
    if isinstance(integral, VolumeUnavailable):
        if integral.cause is not VolumeUnavailableCause.NONFINITE_INTEGRAL:
            return integral
    elif integral != 0:
        return integral, 0
    magnitude = max(abs(float(mesh.vertices.min())), abs(float(mesh.vertices.max())))
    if magnitude == 0:
        return 0.0, 0
    _, exponent = math.frexp(magnitude)
    normalized = trimesh.Trimesh(
        vertices=np.ldexp(mesh.vertices, -exponent),
        faces=mesh.faces,
        process=False,
    )
    integral = signed_mesh_integral(normalized)
    if isinstance(integral, VolumeUnavailable):
        return integral
    return integral, exponent


def _placed_integral(
    resource: tuple[float, int], transform: NDArray[np.float64]
) -> float | VolumeUnavailable:
    """Apply placement scale without overflowing an intermediate determinant."""
    import numpy as np

    value, exponent = resource
    if value == 0:
        return 0.0
    if exponent == 0:
        # Preserve the ordinary path, including its exact rounding behavior.
        determinant = abs(float(np.linalg.det(transform[:3, :3])))
        product = value * determinant
        if math.isfinite(product) and product != 0:
            return product
    # compose_scene reverses reflected facets. Only determinant magnitude is
    # needed; the source integral's sign still represents cavities or inversion.
    _, log_determinant = np.linalg.slogdet(transform[:3, :3])
    logarithm = (
        math.log(abs(value)) + 3 * exponent * math.log(2) + float(log_determinant)
    )
    if not math.isfinite(logarithm):
        return VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
    try:
        product = math.copysign(math.exp(logarithm), value)
    except OverflowError:
        return VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
    if not math.isfinite(product):
        return VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
    return product


def _scene_volume(scene: ExpandedScene) -> SceneVolume:
    """Keep volume faults independent from already-obtained dimensions/count."""
    import numpy as np
    import trimesh

    raw_integrals: dict[str, tuple[float, int]] = {}
    unresolved = False
    inconsistent = False
    unavailable: VolumeUnavailable | None = None
    try:
        for resource in scene.resources:
            raw = trimesh.Trimesh(
                vertices=resource.vertices, faces=resource.faces, process=False
            )
            if not raw.is_watertight:
                unresolved = True
            elif not raw.is_winding_consistent:
                inconsistent = True
            else:
                integral = _resource_integral(raw)
                if isinstance(integral, VolumeUnavailable):
                    unavailable = integral
                else:
                    raw_integrals[resource.resource_id] = integral
            del raw
        # Global welding may change closure/winding when any resource is open.
        if unresolved:
            return VolumeTopologyRequired()
        if inconsistent:
            return VolumeUnavailable(VolumeUnavailableCause.INCONSISTENT_WINDING)
        if unavailable is not None:
            return unavailable
        integrals: list[float] = []
        with np.errstate(over="ignore", invalid="ignore"):
            for instance in scene.instances:
                integral = _placed_integral(
                    raw_integrals[instance.resource_id], instance.transform
                )
                if isinstance(integral, VolumeUnavailable):
                    return integral
                integrals.append(integral)
        try:
            total = math.fsum(integrals)
        except OverflowError:
            return VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
        if not math.isfinite(total):
            return VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
        if total <= 0:
            return VolumeUnavailable(VolumeUnavailableCause.NON_POSITIVE_INTEGRAL)
        return VolumeMeasured(total)
    except Exception:
        # This boundary promises independent useful dimensions on volume faults,
        # matching geometry_from_mesh; ordinary topology refusals are not logs.
        logger.warning("scene volume measurement failed", exc_info=True)
        return VolumeUnavailable(VolumeUnavailableCause.MEASUREMENT_FAILED)


def measure_scene(scene: ExpandedScene) -> SceneMeasurements:
    """Measure source precision without allocating whole placed geometry.

    Retained indices cost O(unique referenced vertices). Placed scratch is
    bounded to one 64k-point chunk; topology/integrals run once per resource.
    """
    import numpy as np

    scene = PreparedScene(scene).scene
    by_id = {resource.resource_id: resource for resource in scene.resources}
    referenced: dict[str, NDArray[np.int64]] = {}
    for resource in scene.resources:
        used = np.zeros(len(resource.vertices), dtype=bool)
        used[resource.faces] = True
        referenced[resource.resource_id] = np.flatnonzero(used)
    minimum = np.full(3, np.inf)
    maximum = np.full(3, -np.inf)
    count = 0
    try:
        with np.errstate(over="raise", invalid="raise"):
            for instance in scene.instances:
                resource = by_id[instance.resource_id]
                count += len(resource.faces)
                indices = referenced[instance.resource_id]
                transform = instance.transform
                for start in range(0, len(indices), _POINT_CHUNK_SIZE):
                    points = resource.vertices[
                        indices[start : start + _POINT_CHUNK_SIZE]
                    ]
                    placed = points @ transform[:3, :3].T + transform[:3, 3]
                    minimum = np.minimum(minimum, placed.min(axis=0))
                    maximum = np.maximum(maximum, placed.max(axis=0))
            if (
                not np.isfinite(minimum).all()
                or not np.isfinite(maximum).all()
                or not np.isfinite(maximum - minimum).all()
            ):
                raise GeometryError("numeric_range")
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise GeometryError("numeric_range") from exc
    return SceneMeasurements(
        (float(minimum[0]), float(minimum[1]), float(minimum[2])),
        (float(maximum[0]), float(maximum[1]), float(maximum[2])),
        count,
        _scene_volume(scene),
    )
