"""Typed loading boundary for the required native G-code engine."""

from __future__ import annotations

import importlib
from functools import cache
from pathlib import Path
from typing import Protocol, TypedDict, cast


class NativeMaterialRequirement(TypedDict):
    tool_index: int
    material_type: str
    color_hex: str | None


class NativeGcodeMetadata(TypedDict):
    slicer_name: str | None
    slicer_version: str | None
    printer_model: str | None
    nozzle_diameter_mm: float | None
    layer_height_mm: float | None
    first_layer_height_mm: float | None
    infill_percent: float | None
    wall_loops: int | None
    top_shell_layers: int | None
    bottom_shell_layers: int | None
    support_material: bool | None
    nozzle_temperature_c: float | None
    bed_temperature_c: float | None
    estimated_time_s: int | None
    filament_weight_g: float | None
    filament_length_mm: float | None
    filament_cost: float | None
    material_type: str | None
    material_brand: str | None
    material_requirements: list[NativeMaterialRequirement] | None
    printer_preset_name: str | None


class GcodeKernel(Protocol):
    def parse_gcode_metadata(self, path: Path) -> NativeGcodeMetadata: ...

    def parse_gcode_duration(self, value: str) -> int | None: ...

    def is_bgcode(self, path: Path) -> bool: ...

    def is_valid_bgcode(self, path: Path) -> bool: ...

    def bgcode_metadata_text(self, path: Path) -> str | None: ...

    def gcode_thumbnails(self, path: Path) -> list[tuple[int, int, int, bytes]]: ...


@cache
def kernel() -> GcodeKernel:
    """Load the native module once without coupling this package to PyO3."""
    return cast(GcodeKernel, importlib.import_module("printstash_mesh_native"))
