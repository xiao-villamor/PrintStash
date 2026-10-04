"""Serializable fingerprint derivatives from one prepared mesh lifetime."""

from __future__ import annotations

from copy import deepcopy
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

from .mesh_facts import CompleteGeometry, FingerprintFailureCode, FingerprintResultState
from .mesh_resources import PreparedMesh

ALGORITHM_VERSION = "geometry-v7-sh5f4577c4"
SH_BASIS_DIGEST = "5f4577c4d06d73c281f343f4538adebcfff371b9d356185b42f5f11ddd41ea46"


@dataclass(frozen=True)
class FingerprintRecord:
    component_index: int
    instance_count: int
    values: dict[str, Any]
    instances: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if (
            type(self.component_index) is not int
            or self.component_index < 0
            or type(self.instance_count) is not int
            or self.instance_count < 1
            or not isinstance(self.values, dict)
            or not self.values
            or not isinstance(self.instances, tuple)
            or any(not isinstance(instance, dict) for instance in self.instances)
        ):
            raise ValueError("invalid_fingerprint_record")
        if self.component_index == 0:
            if self.instance_count != 1 or self.instances:
                raise ValueError("invalid_fingerprint_record_whole_instances")
        elif len(self.instances) != self.instance_count:
            raise ValueError("invalid_fingerprint_record_instance_count")


@dataclass(frozen=True)
class FingerprintResult:
    state: FingerprintResultState
    records: tuple[FingerprintRecord, ...] = ()
    failure_code: FingerprintFailureCode | None = None
    algorithm_version: str = ALGORITHM_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.state, FingerprintResultState):
            raise TypeError("invalid_fingerprint_state")
        if self.failure_code is not None and not isinstance(
            self.failure_code, FingerprintFailureCode
        ):
            raise TypeError("invalid_fingerprint_failure_code")
        if not isinstance(self.records, tuple) or any(
            not isinstance(record, FingerprintRecord) for record in self.records
        ):
            raise TypeError("invalid_fingerprint_records")
        if self.state in {
            FingerprintResultState.FAILED,
            FingerprintResultState.UNSUPPORTED,
        }:
            if self.records:
                raise ValueError("invalid_fingerprint_records_on_failure")
            if self.failure_code is None:
                raise ValueError("missing_fingerprint_failure_code")
            return
        if not self.records:
            raise ValueError("missing_fingerprint_records")
        indices = tuple(record.component_index for record in self.records)
        if indices != tuple(range(len(self.records))):
            raise ValueError("invalid_fingerprint_component_indices")
        if self.state is FingerprintResultState.READY:
            if self.failure_code is not None:
                raise ValueError("invalid_fingerprint_failure_on_ready")
        else:
            if self.failure_code not in {
                FingerprintFailureCode.SAMPLED_SOURCE,
                FingerprintFailureCode.SAMPLED_OVERSIZED_SOURCE,
            }:
                raise ValueError("invalid_fingerprint_sampling_cause")
            whole = self.records[0].values
            recipe = whole.get("recipe")
            if (
                len(self.records) != 1
                or not isinstance(recipe, dict)
                or recipe.get("complete_geometry") is not False
                or whole.get("keys") is not None
                or any(
                    whole.get(field) is not None
                    for field in (
                        "vertex_count",
                        "face_count",
                        "euler_characteristic",
                        "watertight",
                        "winding_consistent",
                        "volume",
                        "area_volume_ratio",
                    )
                )
            ):
                raise ValueError("invalid_fingerprint_sampling_records")


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
            partial=not isinstance(prepared.geometry, CompleteGeometry),
        )
        # A single connected mesh already describes its sole Component. Preserve
        # independent mutable records while avoiding a second full analysis.
        whole_shape = deepcopy(whole) if prepared.whole_resource_id else None
        whole["component_count"] = (
            component_count if isinstance(prepared.geometry, CompleteGeometry) else None
        )
        if prepared.brep is not None:
            whole["brep"] = prepared.brep
            whole["recipe"]["tessellation"] = prepared.brep["recipe"]
        records.append(FingerprintRecord(0, 1, whole, ()))
        if isinstance(prepared.geometry, CompleteGeometry):
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
                described = (
                    deepcopy(whole_shape)
                    if resource.resource_id == prepared.whole_resource_id
                    and whole_shape is not None
                    else _describe(resource.vertices, resource.faces, partial=False)
                )
                described["component_count"] = 1
                records.append(
                    FingerprintRecord(index, len(instances), described, instances)
                )
        return FingerprintResult(
            FingerprintResultState.READY
            if isinstance(prepared.geometry, CompleteGeometry)
            else FingerprintResultState.PARTIAL,
            tuple(records),
            prepared.failure_code,
        )
    except GeometryError as exc:
        return FingerprintResult(
            FingerprintResultState.FAILED, failure_code=FingerprintFailureCode(exc.code)
        )
    except (ValueError, OverflowError, MemoryError):
        return FingerprintResult(
            FingerprintResultState.FAILED,
            failure_code=FingerprintFailureCode.ANALYSIS_FAILED,
        )


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
