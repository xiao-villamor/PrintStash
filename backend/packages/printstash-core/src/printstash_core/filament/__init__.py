"""Filament density and length-to-mass conversion helpers."""

from __future__ import annotations

import math

__all__ = ["DEFAULT_DIAMETER_MM", "density_for", "mm_to_grams"]

# Standard FDM filament diameter. The 2.85 mm ecosystem exists but is rare.
DEFAULT_DIAMETER_MM = 1.75

# Material densities in g/cm³. Unknown materials use PLA's density.
_DENSITY_G_CM3: dict[str, float] = {
    "pla": 1.24,
    "petg": 1.27,
    "abs": 1.04,
    "asa": 1.07,
    "tpu": 1.21,
    "nylon": 1.14,
    "pa": 1.14,
    "pc": 1.20,
    "hips": 1.04,
    "pva": 1.23,
}
_DEFAULT_DENSITY = _DENSITY_G_CM3["pla"]


def density_for(material_type: str | None) -> float:
    """Return material density in g/cm³, falling back to PLA."""
    if not material_type:
        return _DEFAULT_DENSITY
    return _DENSITY_G_CM3.get(material_type.strip().lower(), _DEFAULT_DENSITY)


def mm_to_grams(
    length_mm: float | None,
    material_type: str | None = None,
    diameter_mm: float = DEFAULT_DIAMETER_MM,
    density_g_cm3: float | None = None,
) -> float | None:
    """Convert filament length in millimeters to mass in grams.

    ``density_g_cm3`` overrides the material density table when it is positive.
    Invalid lengths and diameters return ``None``.
    """
    if length_mm is None or length_mm <= 0 or diameter_mm <= 0:
        return None
    density = (
        density_g_cm3
        if density_g_cm3 and density_g_cm3 > 0
        else density_for(material_type)
    )
    radius_mm = diameter_mm / 2.0
    volume_mm3 = math.pi * radius_mm * radius_mm * length_mm
    grams = volume_mm3 / 1000.0 * density
    return round(grams, 2)
