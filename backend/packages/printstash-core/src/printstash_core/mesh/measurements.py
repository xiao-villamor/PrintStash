"""Closed evidence for a mesh volume, independent of similarity fingerprints.

All values use cubic millimetres. Unassessed geometry is distinct from an
integral rejected by the metadata orientation/topology policy. Runtime guards
view typed inputs as objects where needed to reject callers that bypass typing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, cast


class VolumeState(StrEnum):
    MEASURED = "measured"
    UNAVAILABLE = "unavailable"
    NOT_CALCULATED = "not_calculated"
    LEGACY_UNASSESSED = "legacy_unassessed"


class VolumeMethod(StrEnum):
    MESH_SURFACE_INTEGRAL = "mesh_surface_integral"


class VolumeUnavailableCause(StrEnum):
    NOT_WATERTIGHT = "not_watertight"
    INCONSISTENT_WINDING = "inconsistent_winding"
    NON_POSITIVE_INTEGRAL = "non_positive_integral"
    NONFINITE_INTEGRAL = "nonfinite_integral"
    MEASUREMENT_FAILED = "measurement_failed"


class VolumeNotCalculatedCause(StrEnum):
    ENRICHMENT_PENDING = "enrichment_pending"
    NOT_APPLICABLE = "not_applicable"
    NOT_REQUESTED = "not_requested"
    GEOMETRY_UNAVAILABLE = "geometry_unavailable"
    TOPOLOGY_NOT_EVALUATED = "topology_not_evaluated"


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("volume must be a finite number")
    try:
        measured = float(value)
    except OverflowError as exc:
        raise ValueError("volume must be a finite number") from exc
    if not math.isfinite(measured):
        raise ValueError("volume must be a finite number")
    return measured


@dataclass(frozen=True)
class VolumeMeasured:
    value_mm3: float
    state: ClassVar[VolumeState] = VolumeState.MEASURED

    def __post_init__(self) -> None:
        if _finite_number(self.value_mm3) <= 0:
            raise ValueError("measured volume must be positive")


@dataclass(frozen=True)
class VolumeUnavailable:
    cause: VolumeUnavailableCause
    state: ClassVar[VolumeState] = VolumeState.UNAVAILABLE

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.cause), VolumeUnavailableCause):
            raise ValueError("invalid unavailable volume cause")


@dataclass(frozen=True)
class VolumeNotCalculated:
    cause: VolumeNotCalculatedCause
    state: ClassVar[VolumeState] = VolumeState.NOT_CALCULATED

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.cause), VolumeNotCalculatedCause):
            raise ValueError("invalid not calculated volume cause")


@dataclass(frozen=True)
class VolumeLegacyUnassessed:
    """An old scalar is retained without certifying its measurement policy."""

    value_mm3: float | None
    state: ClassVar[VolumeState] = VolumeState.LEGACY_UNASSESSED

    def __post_init__(self) -> None:
        if self.value_mm3 is not None:
            _finite_number(self.value_mm3)

    @classmethod
    def from_value(cls, value: object) -> VolumeLegacyUnassessed:
        """Validate a scalar-only payload without assigning measurement evidence."""
        return cls(None if value is None else _finite_number(value))


VolumeMeasurement = (
    VolumeMeasured | VolumeUnavailable | VolumeNotCalculated | VolumeLegacyUnassessed
)


def volume_value(volume: VolumeMeasurement) -> float | None:
    if isinstance(volume, (VolumeMeasured, VolumeLegacyUnassessed)):
        return volume.value_mm3
    if isinstance(cast(object, volume), (VolumeUnavailable, VolumeNotCalculated)):
        return None
    raise TypeError("invalid volume measurement")


def encode_volume(volume: VolumeMeasurement) -> dict[str, object]:
    if isinstance(volume, (VolumeMeasured, VolumeUnavailable)):
        method = VolumeMethod.MESH_SURFACE_INTEGRAL.value
    elif isinstance(
        cast(object, volume), (VolumeNotCalculated, VolumeLegacyUnassessed)
    ):
        method = None
    else:
        raise TypeError("invalid volume measurement")
    return {
        "state": volume.state.value,
        "unit": "mm3",
        "method": method,
        "value_mm3": volume_value(volume),
        "cause": volume.cause.value
        if isinstance(volume, (VolumeUnavailable, VolumeNotCalculated))
        else None,
    }


def decode_volume(raw: object) -> VolumeMeasurement:
    if not isinstance(raw, dict) or set(raw) != {
        "state",
        "unit",
        "method",
        "value_mm3",
        "cause",
    }:
        raise ValueError("invalid volume measurement shape")
    if not isinstance(raw["state"], str) or raw["unit"] != "mm3":
        raise ValueError("invalid volume measurement tag")
    state = VolumeState(raw["state"])
    value, method, cause = raw["value_mm3"], raw["method"], raw["cause"]
    if state is VolumeState.MEASURED:
        if method != VolumeMethod.MESH_SURFACE_INTEGRAL.value or cause is not None:
            raise ValueError("invalid measured volume evidence")
        return VolumeMeasured(_finite_number(value))
    if state is VolumeState.UNAVAILABLE:
        if (
            value is not None
            or method != VolumeMethod.MESH_SURFACE_INTEGRAL.value
            or not isinstance(cause, str)
        ):
            raise ValueError("invalid unavailable volume evidence")
        return VolumeUnavailable(VolumeUnavailableCause(cause))
    if state is VolumeState.NOT_CALCULATED:
        if value is not None or method is not None or not isinstance(cause, str):
            raise ValueError("invalid not calculated volume evidence")
        return VolumeNotCalculated(VolumeNotCalculatedCause(cause))
    if method is not None or cause is not None:
        raise ValueError("invalid legacy volume evidence")
    return VolumeLegacyUnassessed(None if value is None else _finite_number(value))


class InvalidMeshMeasurements(ValueError):
    """Physical dimensions cannot be represented by finite nonnegative values."""


def validate_geometry_extents(geometry: Mapping[str, object]) -> None:
    for name in ("bbox_x_mm", "bbox_y_mm", "bbox_z_mm"):
        if name not in geometry:
            continue
        value = geometry[name]
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidMeshMeasurements(
                "mesh dimensions must be finite nonnegative numbers"
            )
        try:
            finite = math.isfinite(value)
        except OverflowError as exc:
            raise InvalidMeshMeasurements(
                "mesh dimensions must be finite nonnegative numbers"
            ) from exc
        if not finite or value < 0:
            raise InvalidMeshMeasurements(
                "mesh dimensions must be finite nonnegative numbers"
            )
