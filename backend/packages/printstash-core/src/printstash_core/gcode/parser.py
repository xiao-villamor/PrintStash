"""Typed Python facade for native slicer metadata extraction."""

from __future__ import annotations

from pathlib import Path

from .models import GcodeMetadata, MaterialRequirement
from .native import kernel


def parse_duration(value: str) -> int | None:
    """Parse slicer duration text into whole seconds in the native core."""
    return kernel().parse_gcode_duration(value)


def parse(path: Path) -> GcodeMetadata:
    """Translate the native result into the stable immutable Python model."""
    raw = kernel().parse_gcode_metadata(path)
    native_requirements = raw["material_requirements"]
    requirements = (
        tuple(
            MaterialRequirement(
                tool_index=requirement["tool_index"],
                material_type=requirement["material_type"],
                color_hex=requirement["color_hex"],
            )
            for requirement in native_requirements
        )
        if native_requirements is not None
        else None
    )
    return GcodeMetadata(
        slicer_name=raw["slicer_name"],
        slicer_version=raw["slicer_version"],
        printer_model=raw["printer_model"],
        nozzle_diameter_mm=raw["nozzle_diameter_mm"],
        layer_height_mm=raw["layer_height_mm"],
        first_layer_height_mm=raw["first_layer_height_mm"],
        infill_percent=raw["infill_percent"],
        wall_loops=raw["wall_loops"],
        top_shell_layers=raw["top_shell_layers"],
        bottom_shell_layers=raw["bottom_shell_layers"],
        support_material=raw["support_material"],
        nozzle_temperature_c=raw["nozzle_temperature_c"],
        bed_temperature_c=raw["bed_temperature_c"],
        estimated_time_s=raw["estimated_time_s"],
        filament_weight_g=raw["filament_weight_g"],
        filament_length_mm=raw["filament_length_mm"],
        filament_cost=raw["filament_cost"],
        material_type=raw["material_type"],
        material_brand=raw["material_brand"],
        material_requirements=requirements,
        printer_preset_name=raw["printer_preset_name"],
    )
