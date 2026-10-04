"""Required volume evidence in the library API, expressed as disjoint variants."""

from typing import Annotated, Literal

from printstash_core.mesh.measurements import (
    VolumeMethod,
    VolumeNotCalculatedCause,
    VolumeState,
    VolumeUnavailableCause,
)
from pydantic import BaseModel, ConfigDict, Field


class _VolumeRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    unit: Literal["mm3"]


class MeasuredVolumeRead(_VolumeRead):
    state: Literal[VolumeState.MEASURED]
    method: Literal[VolumeMethod.MESH_SURFACE_INTEGRAL]
    value_mm3: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    cause: None


class UnavailableVolumeRead(_VolumeRead):
    state: Literal[VolumeState.UNAVAILABLE]
    method: Literal[VolumeMethod.MESH_SURFACE_INTEGRAL]
    value_mm3: None
    cause: VolumeUnavailableCause


class NotCalculatedVolumeRead(_VolumeRead):
    state: Literal[VolumeState.NOT_CALCULATED]
    method: None
    value_mm3: None
    cause: VolumeNotCalculatedCause


class LegacyUnassessedVolumeRead(_VolumeRead):
    state: Literal[VolumeState.LEGACY_UNASSESSED]
    method: None
    value_mm3: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None
    cause: None


VolumeMeasurementRead = Annotated[
    MeasuredVolumeRead
    | UnavailableVolumeRead
    | NotCalculatedVolumeRead
    | LegacyUnassessedVolumeRead,
    Field(discriminator="state"),
]
