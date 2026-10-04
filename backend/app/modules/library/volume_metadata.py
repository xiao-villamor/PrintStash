"""Map closed volume evidence to Metadata without certifying legacy scalars."""

from collections.abc import Mapping

from printstash_core.mesh.measurements import (
    VolumeLegacyUnassessed,
    VolumeMeasured,
    VolumeMeasurement,
    VolumeMethod,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeState,
    VolumeUnavailable,
    decode_volume,
    validate_geometry_extents,
    volume_value,
)

from app.db.models import FileType, Metadata


def apply_volume(metadata: Metadata, volume: VolumeMeasurement) -> None:
    metadata.volume_mm3 = volume_value(volume)
    metadata.volume_state = volume.state
    metadata.volume_method = (
        VolumeMethod.MESH_SURFACE_INTEGRAL
        if isinstance(volume, (VolumeMeasured, VolumeUnavailable))
        else None
    )
    metadata.volume_unavailable_cause = (
        volume.cause if isinstance(volume, VolumeUnavailable) else None
    )
    metadata.volume_not_calculated_cause = (
        volume.cause if isinstance(volume, VolumeNotCalculated) else None
    )


def read_volume(metadata: Metadata) -> VolumeMeasurement:
    if metadata.volume_state is VolumeState.MEASURED:
        if (
            metadata.volume_mm3 is None
            or metadata.volume_method is not VolumeMethod.MESH_SURFACE_INTEGRAL
            or metadata.volume_unavailable_cause is not None
            or metadata.volume_not_calculated_cause is not None
        ):
            raise ValueError("invalid persisted measured volume")
        return VolumeMeasured(metadata.volume_mm3)
    if metadata.volume_state is VolumeState.UNAVAILABLE:
        if (
            metadata.volume_mm3 is not None
            or metadata.volume_method is not VolumeMethod.MESH_SURFACE_INTEGRAL
            or metadata.volume_unavailable_cause is None
            or metadata.volume_not_calculated_cause is not None
        ):
            raise ValueError("invalid persisted unavailable volume")
        return VolumeUnavailable(metadata.volume_unavailable_cause)
    if metadata.volume_state is VolumeState.NOT_CALCULATED:
        if (
            metadata.volume_mm3 is not None
            or metadata.volume_method is not None
            or metadata.volume_unavailable_cause is not None
            or metadata.volume_not_calculated_cause is None
        ):
            raise ValueError("invalid persisted not calculated volume")
        return VolumeNotCalculated(metadata.volume_not_calculated_cause)
    if metadata.volume_state is VolumeState.LEGACY_UNASSESSED:
        if (
            metadata.volume_method is not None
            or metadata.volume_unavailable_cause is not None
            or metadata.volume_not_calculated_cause is not None
        ):
            raise ValueError("invalid persisted legacy volume")
        return VolumeLegacyUnassessed(metadata.volume_mm3)
    raise ValueError("invalid persisted volume state")


def incoming_volume(
    meta: Mapping[str, object] | None, file_type: FileType
) -> VolumeMeasurement:
    """Validate caller facts before allocating a version or publishing bytes."""
    payload = {} if meta is None else meta
    validate_geometry_extents(payload)
    internal_columns = {
        "volume_state",
        "volume_method",
        "volume_unavailable_cause",
        "volume_not_calculated_cause",
    }
    if internal_columns.intersection(payload):
        raise ValueError(
            "volume evidence uses volume_measurement, not internal columns"
        )
    if "volume_measurement" in payload:
        volume = decode_volume(payload["volume_measurement"])
        if "volume_mm3" in payload:
            scalar = VolumeLegacyUnassessed.from_value(payload["volume_mm3"])
            if scalar.value_mm3 != volume_value(volume):
                raise ValueError("volume scalar disagrees with evidence")
        return volume
    if "volume_mm3" in payload:
        # Legacy describes the old scalar-only shape, not this Artifact's age.
        return VolumeLegacyUnassessed.from_value(payload["volume_mm3"])
    return VolumeNotCalculated(
        VolumeNotCalculatedCause.NOT_APPLICABLE
        if file_type in (FileType.GCODE, FileType.DXF)
        else VolumeNotCalculatedCause.ENRICHMENT_PENDING
    )
