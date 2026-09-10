"""Serializable fingerprint derivatives from one prepared mesh lifetime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from printstash_core.mesh.similarity import GeometryError, fingerprint_mesh
from printstash_core.mesh.similarity.descriptors import (
    SH_RECIPE,
    VIEW_RECIPE,
    describe_surface,
)
from printstash_core.mesh.similarity.geometry import prepare_surface
from printstash_core.mesh.similarity.verification import VERIFICATION_VERSION

from .mesh_resources import PreparedMesh

ALGORITHM_VERSION = "geometry-v2-sh5f4577c4"
SH_BASIS_DIGEST = "5f4577c4d06d73c281f343f4538adebcfff371b9d356185b42f5f11ddd41ea46"


@dataclass(frozen=True)
class FingerprintRecord:
    component_index: int
    instance_count: int
    values: dict[str, Any]
    instances: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class FingerprintResult:
    state: str
    records: tuple[FingerprintRecord, ...] = ()
    failure_code: str | None = None
    algorithm_version: str = ALGORITHM_VERSION


class GeometryMetadata(dict[str, float | None]):
    """Existing geometry mapping with a separate, non-metadata derivative."""

    def __init__(
        self,
        values: dict[str, float | None],
        fingerprint_result: FingerprintResult | None,
    ) -> None:
        super().__init__(values)
        self.fingerprint_result = fingerprint_result


def extract(prepared: PreparedMesh) -> FingerprintResult:
    """No source I/O or ORM calls; errors affect the derivative, never ingestion."""
    import numpy as np

    try:
        records = []
        mesh = prepared.whole_mesh
        component_count = len(prepared.scene.instances)
        whole = _describe(
            np.asarray(mesh.vertices),
            np.asarray(mesh.faces),
            partial=not prepared.complete,
        )
        whole["component_count"] = component_count if prepared.complete else None
        if prepared.brep is not None:
            whole["brep"] = prepared.brep
            whole["recipe"]["tessellation"] = prepared.brep["recipe"]
        records.append(FingerprintRecord(0, 1, whole, ()))
        if prepared.complete:
            for index, resource in enumerate(prepared.scene.resources, 1):
                instances = tuple(
                    {
                        "resource_id": resource.resource_id,
                        "transform": instance.transform.tolist(),
                    }
                    for instance in prepared.scene.instances
                    if instance.resource_id == resource.resource_id
                )
                # Preserve scale/reflection per instance; resource arrays stay in
                # their physical source frame and are not flattened or duplicated.
                described = _describe(resource.vertices, resource.faces, partial=False)
                described["component_count"] = 1
                records.append(
                    FingerprintRecord(index, len(instances), described, instances)
                )
        return FingerprintResult(
            "ready" if prepared.complete else "partial",
            tuple(records),
            prepared.failure_code,
        )
    except GeometryError as exc:
        return FingerprintResult("failed", failure_code=exc.code)
    except (ValueError, OverflowError, MemoryError):
        return FingerprintResult("failed", failure_code="analysis_failed")


def _describe(vertices: Any, faces: Any, *, partial: bool) -> dict[str, Any]:
    import numpy as np

    core = fingerprint_mesh(vertices, faces)
    surface = prepare_surface(vertices, faces)
    values = asdict(core.metrics)
    values["radius"] = surface.radius
    values["normalized_area"] = core.metrics.surface_area / surface.radius**2
    values["eigen_ratio_0"] = core.metrics.surface_eigenvalue_ratios[0]
    values["keys"] = (
        asdict(core.keys) if core.keys is not None and not partial else None
    )
    values["d2_blob"] = np.array(core.d2.histogram, dtype="<f4").tobytes()
    values["recipe"] = {
        "core": core.algorithm_version,
        "numpy": core.numpy_version,
        "sh": SH_RECIPE,
        "sh_basis": SH_BASIS_DIGEST,
        "views": VIEW_RECIPE,
        "verification": VERIFICATION_VERSION,
        "d2_seed": core.d2.seed,
        "d2_pairs": core.d2.sample_pairs,
        "ambiguous_frame": core.ambiguous_frame,
        "complete_geometry": not partial,
    }
    values["unavailable"] = []
    if partial:
        for field in (
            "vertex_count",
            "face_count",
            "euler_characteristic",
            "watertight",
            "winding_consistent",
            "volume",
            "area_volume_ratio",
        ):
            values[field] = None
            values["unavailable"].append((field, "sampled_source"))
        values["recipe"]["surface_area_estimated"] = True
        return values
    shape = describe_surface(
        surface, volume=core.metrics.volume, ambiguous_frame=core.ambiguous_frame
    )
    if shape.sh_basis_digest is not None and shape.sh_basis_digest != SH_BASIS_DIGEST:
        raise GeometryError("algorithm_basis_mismatch")
    values.update(
        hull_ratio=shape.hull_ratio,
        fill_ratio=shape.fill_ratio,
        inertia_ratios=shape.inertia_ratios,
        inertia_ratio_0=shape.inertia_ratios[0] if shape.inertia_ratios else None,
        inertia_ratio_1=shape.inertia_ratios[1] if shape.inertia_ratios else None,
        unavailable=list(shape.unavailable),
    )
    values["sh_blob"] = (
        np.array(shape.sh, dtype="<f4").tobytes() if shape.sh is not None else None
    )
    values["view_blob"] = shape.view_hashes
    return values
