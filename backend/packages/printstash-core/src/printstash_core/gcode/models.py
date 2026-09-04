"""Immutable metadata models produced by the G-code parser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

LegacyMaterialRequirement: TypeAlias = dict[str, int | str | None]
LegacyGcodeMetadata: TypeAlias = dict[str, object]


@dataclass(frozen=True, slots=True)
class MaterialRequirement:
    """Material expected by one tool in a sliced print."""

    tool_index: int
    material_type: str
    color_hex: str | None = None

    def to_legacy_dict(self) -> LegacyMaterialRequirement:
        """Serialize to the mapping consumed by the existing application."""
        return {
            "tool_index": self.tool_index,
            "material_type": self.material_type,
            "color_hex": self.color_hex,
        }


@dataclass(frozen=True, slots=True)
class GcodeMetadata:
    """Framework-neutral slicer metadata extracted from a G-code file."""

    slicer_name: str | None = None
    slicer_version: str | None = None
    printer_model: str | None = None
    nozzle_diameter_mm: float | None = None
    layer_height_mm: float | None = None
    first_layer_height_mm: float | None = None
    infill_percent: float | None = None
    wall_loops: int | None = None
    top_shell_layers: int | None = None
    bottom_shell_layers: int | None = None
    support_material: bool | None = None
    nozzle_temperature_c: float | None = None
    bed_temperature_c: float | None = None
    estimated_time_s: int | None = None
    filament_weight_g: float | None = None
    filament_length_mm: float | None = None
    filament_cost: float | None = None
    material_type: str | None = None
    material_brand: str | None = None
    material_requirements: tuple[MaterialRequirement, ...] | None = None
    printer_preset_name: str | None = None

    def to_legacy_dict(self) -> LegacyGcodeMetadata:
        """Serialize to the exact mapping shape used by the legacy parser."""
        requirements = (
            [requirement.to_legacy_dict() for requirement in self.material_requirements]
            if self.material_requirements is not None
            else None
        )
        return {
            "slicer_name": self.slicer_name,
            "slicer_version": self.slicer_version,
            "printer_model": self.printer_model,
            "nozzle_diameter_mm": self.nozzle_diameter_mm,
            "layer_height_mm": self.layer_height_mm,
            "first_layer_height_mm": self.first_layer_height_mm,
            "infill_percent": self.infill_percent,
            "wall_loops": self.wall_loops,
            "top_shell_layers": self.top_shell_layers,
            "bottom_shell_layers": self.bottom_shell_layers,
            "support_material": self.support_material,
            "nozzle_temperature_c": self.nozzle_temperature_c,
            "bed_temperature_c": self.bed_temperature_c,
            "estimated_time_s": self.estimated_time_s,
            "filament_weight_g": self.filament_weight_g,
            "filament_length_mm": self.filament_length_mm,
            "filament_cost": self.filament_cost,
            "material_type": self.material_type,
            "material_brand": self.material_brand,
            "material_requirements": requirements,
            "printer_preset_name": self.printer_preset_name,
        }


def to_legacy_dict(metadata: GcodeMetadata) -> LegacyGcodeMetadata:
    """Serialize typed metadata for legacy application callers."""
    return metadata.to_legacy_dict()
