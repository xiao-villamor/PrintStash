"""Map Moonraker's optional diagnostics into bounded workbench read models."""

from collections.abc import Mapping
from math import isfinite
from typing import Any

from app.schemas.printer_inspection import (
    ActiveBedMesh,
    InactiveBedMesh,
    PrinterBedMesh,
    PrinterHeaterHistory,
    RecordedHeaterSample,
    UnconfiguredBedMesh,
)

# The status chart covers five minutes; an inclusive one-second grid needs 301 slots.
_HISTORY_SLOTS = 301
_SAMPLE_INTERVAL_MS = 1000  # Moonraker data_store's documented nominal cadence.


class InvalidInspectionResponse(ValueError):
    """The controller answered with a payload outside the expected contract."""


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidInspectionResponse
    return value


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if isfinite(value) else None
    except OverflowError:
        return None


def _recent_heater_samples(value: object) -> list[RecordedHeaterSample]:
    if value is None:
        return []
    series = _object(value)
    temperatures = series.get("temperatures", [])
    targets = series.get("targets", [])
    if not isinstance(temperatures, list) or not isinstance(targets, list):
        raise InvalidInspectionResponse

    samples = []
    # Both FIFO queues end at their newest reading. A field that was added
    # later can have a shorter queue, so index from that end.
    slots = min(_HISTORY_SLOTS, max(len(temperatures), len(targets)))
    for age in range(slots - 1, -1, -1):
        temperature = (
            _finite_number(temperatures[-1 - age]) if age < len(temperatures) else None
        )
        target = _finite_number(targets[-1 - age]) if age < len(targets) else None
        if temperature is not None or target is not None:
            samples.append(
                RecordedHeaterSample(
                    age_ms=age * _SAMPLE_INTERVAL_MS,
                    temperature=temperature,
                    target=target,
                )
            )
    return samples


def read_heater_history(payload: object) -> PrinterHeaterHistory:
    result = _object(_object(payload).get("result"))
    return PrinterHeaterHistory(
        sample_interval_ms=_SAMPLE_INTERVAL_MS,
        extruder=_recent_heater_samples(result.get("extruder")),
        heater_bed=_recent_heater_samples(result.get("heater_bed")),
    )


def _required_number(value: object) -> float:
    number = _finite_number(value)
    if number is None:
        raise InvalidInspectionResponse
    return number


def _point(value: object) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise InvalidInspectionResponse
    return _required_number(value[0]), _required_number(value[1])


def read_bed_mesh(payload: object) -> PrinterBedMesh:
    result = _object(_object(payload).get("result"))
    status = _object(result.get("status"))
    mesh = status.get("bed_mesh")
    # Klipper returns an empty object for unconfigured status objects.
    if mesh is None or mesh == {}:
        return UnconfiguredBedMesh(state="unconfigured")

    obj = _object(mesh)
    matrix = obj.get("probed_matrix")
    if matrix in ([], [[]], [[0]]):
        return InactiveBedMesh(state="inactive")
    if not isinstance(matrix, list) or len(matrix) < 2:
        raise InvalidInspectionResponse

    min_x, min_y = _point(obj.get("mesh_min"))
    max_x, max_y = _point(obj.get("mesh_max"))
    if not (min_x < max_x and min_y < max_y):
        raise InvalidInspectionResponse

    grid = []
    for row in matrix:
        if (
            not isinstance(row, list)
            or len(row) < 2
            or (grid and len(row) != len(grid[0]))
        ):
            raise InvalidInspectionResponse
        grid.append([_required_number(value) for value in row])

    profile = obj.get("profile_name")
    if profile is not None and not isinstance(profile, str):
        raise InvalidInspectionResponse
    variation = max(map(max, grid)) - min(map(min, grid))
    return ActiveBedMesh(
        state="active",
        profile_name=profile or None,
        mesh_min=(min_x, min_y),
        mesh_max=(max_x, max_y),
        probed_matrix=grid,
        variation_mm=_required_number(variation),
    )
