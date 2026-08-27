"""Defends ``test_density_for_known_materials`` behavior for the ``filament`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import math

import pytest
from printstash_core.filament import DEFAULT_DIAMETER_MM, density_for, mm_to_grams


@pytest.mark.parametrize(
    ("material_type", "expected"),
    [
        ("PLA", 1.24),
        (" petg ", 1.27),
        ("ABS", 1.04),
        ("ASA", 1.07),
        ("TPU", 1.21),
        ("nylon", 1.14),
        ("PA", 1.14),
        ("PC", 1.20),
        ("HIPS", 1.04),
        ("PVA", 1.23),
    ],
)
def test_density_for_known_materials(
    material_type: str,
    expected: float,
) -> None:
    assert density_for(material_type) == expected


@pytest.mark.parametrize("material_type", [None, "", "unknown", "PLA+"])
def test_density_for_unknown_material_uses_pla(
    material_type: str | None,
) -> None:
    assert density_for(material_type) == 1.24


def test_mm_to_grams_uses_cylinder_volume_and_rounds_to_two_places() -> None:
    radius = DEFAULT_DIAMETER_MM / 2.0
    expected = round(math.pi * radius**2 * 1000 / 1000.0 * 1.24, 2)

    assert mm_to_grams(1000, "PLA") == expected


def test_mm_to_grams_honors_positive_density_and_diameter_overrides() -> None:
    assert mm_to_grams(1000, "PLA", density_g_cm3=1.30) == round(
        math.pi * (DEFAULT_DIAMETER_MM / 2.0) ** 2 * 1.30,
        2,
    )
    assert mm_to_grams(1000, "PLA", diameter_mm=2.85) > mm_to_grams(1000, "PLA")


@pytest.mark.parametrize("length_mm", [None, 0, -1])
def test_mm_to_grams_rejects_non_positive_length(
    length_mm: float | None,
) -> None:
    assert mm_to_grams(length_mm) is None


@pytest.mark.parametrize("diameter_mm", [0, -1.75])
def test_mm_to_grams_rejects_non_positive_diameter(diameter_mm: float) -> None:
    assert mm_to_grams(1000, diameter_mm=diameter_mm) is None


@pytest.mark.parametrize("density_g_cm3", [None, 0, -1])
def test_mm_to_grams_non_positive_density_falls_back_to_material(
    density_g_cm3: float | None,
) -> None:
    assert mm_to_grams(1000, "ABS", density_g_cm3=density_g_cm3) == mm_to_grams(
        1000, "ABS"
    )
