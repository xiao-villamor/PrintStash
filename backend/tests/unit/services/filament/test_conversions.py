"""Defends conversions at the services filament unit boundary.

A regression would misstate filament identity, conversion, or profile metadata to callers.
"""

from __future__ import annotations

from ._helpers_shared import (
    DEFAULT_DIAMETER_MM,
    density_for,
    math,
    mm_to_grams,
    pytest,
)


class TestDensityFor:
    @pytest.mark.parametrize(
        "material, density",
        [("PLA", 1.24), ("petg", 1.27), (" ABS ", 1.04), ("TPU", 1.21)],
    )
    def test_known_materials(self, material: str, density: float) -> None:
        assert density_for(material) == density

    @pytest.mark.parametrize("material", [None, "", "Unknown", "PLA+"])
    def test_unknown_falls_back_to_pla(self, material) -> None:
        assert density_for(material) == 1.24


class TestMmToGrams:
    def test_matches_cylinder_formula(self) -> None:
        radius = DEFAULT_DIAMETER_MM / 2.0
        expected = round(math.pi * radius * radius * 1000 / 1000.0 * 1.24, 2)
        assert mm_to_grams(1000, "PLA") == expected

    def test_density_affects_mass(self) -> None:
        # ABS is less dense than PETG, so the same length weighs less.
        assert mm_to_grams(1000, "abs") < mm_to_grams(1000, "petg")

    @pytest.mark.parametrize("length", [None, 0, -100])
    def test_non_positive_length_is_none(self, length) -> None:
        assert mm_to_grams(length) is None

    def test_non_positive_diameter_is_none(self) -> None:
        assert mm_to_grams(1000, "PLA", diameter_mm=0) is None
        assert mm_to_grams(1000, "PLA", diameter_mm=-1.75) is None

    def test_unknown_material_uses_pla_default(self) -> None:
        assert mm_to_grams(1000, "mystery") == mm_to_grams(1000, "PLA")
