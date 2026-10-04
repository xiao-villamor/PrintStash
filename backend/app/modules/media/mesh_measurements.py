"""Source-precision mesh dimensions and signed volume evidence.

Measurement never repairs winding or changes source buffers. Optional similarity
analysis and rendering have separate owners and budgets.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeMeasurement,
    VolumeUnavailable,
    VolumeUnavailableCause,
    validate_geometry_extents,
    volume_value,
)

from app.core.logging import get_logger
from app.modules.media.mesh_contracts import MeshMeasurements

if TYPE_CHECKING:
    from trimesh import Trimesh

logger = get_logger(__name__)
_VOLUME_INTEGRAL_BATCH_FACES = 4096


def signed_mesh_integral(mesh: Trimesh) -> float | VolumeUnavailable:
    """Return a finite signed integral for already-closed, consistent surfaces.

    Orientation is retained, including negative cavity contributions and zero.
    Callers own topology admission and positive aggregate classification.
    """
    import numpy as np
    import trimesh

    labels = trimesh.graph.connected_component_labels(
        mesh.face_adjacency, node_count=len(mesh.faces)
    )
    # Every face receives a component, including disconnected singleton nodes.
    first = np.full(int(labels.max()) + 1, len(mesh.faces), dtype=np.int64)
    np.minimum.at(first, labels, np.arange(len(mesh.faces)))
    origins = mesh.vertices[mesh.faces[first, 0]]
    integrals: list[float] = []
    for offset in range(0, len(mesh.faces), _VOLUME_INTEGRAL_BATCH_FACES):
        end = min(offset + _VOLUME_INTEGRAL_BATCH_FACES, len(mesh.faces))
        # Gather only this batch; never cache expanded source .triangles.
        triangles = mesh.vertices[mesh.faces[offset:end]]
        triangles -= origins[labels[offset:end]][:, None, :]
        integral = float(
            trimesh.triangles.mass_properties(
                triangles, center_mass=np.zeros(3), skip_inertia=True
            ).volume
        )
        if not math.isfinite(integral):
            return VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
        integrals.append(integral)
        del triangles
    try:
        total = math.fsum(integrals)
    except OverflowError:
        return VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
    if not math.isfinite(total):
        return VolumeUnavailable(VolumeUnavailableCause.NONFINITE_INTEGRAL)
    return total


def _volume_from_closed_mesh(mesh: Trimesh) -> VolumeMeasured | VolumeUnavailable:
    integral = signed_mesh_integral(mesh)
    if isinstance(integral, VolumeUnavailable):
        return integral
    if integral <= 0:
        return VolumeUnavailable(VolumeUnavailableCause.NON_POSITIVE_INTEGRAL)
    return VolumeMeasured(integral)


def geometry_from_mesh(mesh: Trimesh | None) -> MeshMeasurements:
    unavailable = MeshMeasurements.unavailable()
    if mesh is None:
        return unavailable
    out = unavailable.geometry.copy()
    volume: VolumeMeasurement = unavailable.volume

    if mesh.vertices.shape[0] > 0:
        # Persist source precision; display formatting belongs to consumers.
        bounds = mesh.bounds
        out["bbox_x_mm"] = float(bounds[1][0]) - float(bounds[0][0])
        out["bbox_y_mm"] = float(bounds[1][1]) - float(bounds[0][1])
        out["bbox_z_mm"] = float(bounds[1][2]) - float(bounds[0][2])

    if mesh.faces is not None and len(mesh.faces) > 0:
        out["triangle_count"] = len(mesh.faces)

    validate_geometry_extents(out)
    if out["triangle_count"] is None:
        return MeshMeasurements(out, volume)

    try:
        measured = mesh
        if not measured.is_watertight:
            # STL stores independent facet vertices. A measurement copy welds
            # them without repairing winding or changing source buffers.
            measured = mesh.copy()
            measured.merge_vertices()
        if not measured.is_watertight:
            volume = VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT)
        elif not measured.is_winding_consistent:
            volume = VolumeUnavailable(VolumeUnavailableCause.INCONSISTENT_WINDING)
        else:
            volume = _volume_from_closed_mesh(measured)
    except MemoryError:
        raise
    except Exception:
        # Keep independently obtained dimensions/count if only volume fails.
        logger.warning("mesh volume measurement failed", exc_info=True)
        volume = VolumeUnavailable(VolumeUnavailableCause.MEASUREMENT_FAILED)

    out["volume_mm3"] = volume_value(volume)
    return MeshMeasurements(out, volume)
