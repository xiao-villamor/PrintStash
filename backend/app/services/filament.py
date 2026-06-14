"""Filament length → mass conversion.

Moonraker reports consumed filament as a *length* (mm) in
``print_stats.filament_used``. To turn that into the *grams* used for real cost
tracking we need the filament cross-section (diameter) and material density.
The vault's ``FilamentProfile`` does not (yet) store these, so we use the
near-universal 1.75 mm diameter and a per-material density table, keyed by the
linked file's ``material_type``. This yields measured-length-based grams — more
accurate than the slicer estimate, while honest about its assumptions.
"""

from __future__ import annotations

import math
from typing import Optional

# Standard FDM filament diameter. The 2.85 mm ecosystem exists but is rare.
DEFAULT_DIAMETER_MM = 1.75

# Material densities in g/cm³. Falls back to PLA when the material is unknown.
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


def density_for(material_type: Optional[str]) -> float:
    if not material_type:
        return _DEFAULT_DENSITY
    return _DENSITY_G_CM3.get(material_type.strip().lower(), _DEFAULT_DENSITY)


def mm_to_grams(
    length_mm: Optional[float],
    material_type: Optional[str] = None,
    diameter_mm: float = DEFAULT_DIAMETER_MM,
) -> Optional[float]:
    """Convert a filament length (mm) to mass (g). Returns None for bad input."""
    if length_mm is None or length_mm <= 0 or diameter_mm <= 0:
        return None
    radius_mm = diameter_mm / 2.0
    volume_mm3 = math.pi * radius_mm * radius_mm * length_mm
    grams = volume_mm3 / 1000.0 * density_for(material_type)  # mm³→cm³ then ×density
    return round(grams, 2)
