"""Turning a filament length into a weight, which is what a spool is measured in.

Slicers report filament use in millimetres of extruded length; a spool is bought,
weighed, and depleted in grams. This module is the conversion, and everything it
touches ends up in the filament ledger — so a wrong answer is not a display bug,
it is a spool that reads full when it is empty, or a print that appears to have
cost nothing.

Two properties matter more than the arithmetic.

**A value it cannot compute is `None`, never zero.** A zero is a claim: posted to
the ledger it says "this print used no filament", which silently stops depleting
the spool. So a missing length, a non-positive length, and an impossible diameter
all refuse rather than round down.

**An unknown material falls back to PLA rather than refusing.** PLA is the most
common filament by a wide margin and the densities of everything else sit within
about 20% of it, so a slightly-wrong weight is much more useful than no weight at
all — the estimate is already approximate, and the operator can correct a spool.
`PLA+` is the interesting case: it is a real product name that is not a key here,
and it lands on the PLA default by exactly that rule.

The material table is pinned value by value because those densities come from
manufacturer data sheets, not from a formula: a "cleanup" that changed one would
skew every print of that material.
"""

from __future__ import annotations

import math

import pytest

from printstash_core.filament import DEFAULT_DIAMETER_MM, density_for, mm_to_grams

PLA_DENSITY = 1.24


class TestDensityFor:
    @pytest.mark.parametrize(
        ("material_type", "density"),
        [
            ("PLA", 1.24),
            ("PETG", 1.27),
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
    def test_reports_the_data_sheet_density_for_a_known_material(
        self, material_type: str, density: float
    ) -> None:
        # These come from manufacturer data sheets rather than a formula, so a
        # tidy-up that changed one would skew every print of that material.
        assert density_for(material_type) == density

    def test_reads_a_material_name_regardless_of_case_or_padding(self) -> None:
        # The name arrives from a slicer comment, which may be spelled anyhow.
        assert density_for(" petg ") == 1.27

    @pytest.mark.parametrize("material_type", [None, "", "unknown", "PLA+"])
    def test_falls_back_to_pla_for_a_material_it_does_not_know(
        self, material_type: str | None
    ) -> None:
        # Every other common filament is within ~20% of PLA, and the estimate is
        # approximate anyway, so a slightly-wrong weight beats no weight.
        # `PLA+` is a real product name that is deliberately not a key.
        assert density_for(material_type) == PLA_DENSITY


class TestMmToGrams:
    def test_weighs_a_length_as_a_cylinder_of_filament(self) -> None:
        radius = DEFAULT_DIAMETER_MM / 2.0
        expected = round(math.pi * radius**2 * 1000 / 1000.0 * PLA_DENSITY, 2)

        assert mm_to_grams(1000, "PLA") == expected

    def test_rounds_to_a_hundredth_of_a_gram(self) -> None:
        weight = mm_to_grams(1000, "PLA")

        # A scale reads to 0.1 g at best; more decimals imply a precision the
        # slicer's own estimate does not have.
        assert weight is not None
        assert weight == round(weight, 2)

    def test_uses_the_material_density_it_was_given_a_name_for(self) -> None:
        # ABS is lighter than PLA, so the same length weighs less.
        pla = mm_to_grams(1000, "PLA")
        abs_weight = mm_to_grams(1000, "ABS")

        assert pla is not None and abs_weight is not None
        assert abs_weight < pla

    def test_honours_an_explicit_density(self) -> None:
        # A user who has weighed their own spool can correct the estimate.
        assert mm_to_grams(1000, "PLA", density_g_cm3=1.30) == round(
            math.pi * (DEFAULT_DIAMETER_MM / 2.0) ** 2 * 1.30, 2
        )

    def test_honours_an_explicit_diameter(self) -> None:
        # 2.85 mm filament holds well over twice the volume per millimetre, so
        # defaulting to 1.75 mm for it would under-report by more than half.
        wide = mm_to_grams(1000, "PLA", diameter_mm=2.85)
        default = mm_to_grams(1000, "PLA")

        assert wide is not None and default is not None
        assert wide > default

    def test_defaults_to_one_point_seven_five_millimetre_filament(self) -> None:
        assert mm_to_grams(1000, "PLA", diameter_mm=DEFAULT_DIAMETER_MM) == (
            mm_to_grams(1000, "PLA")
        )

    @pytest.mark.parametrize("length_mm", [None, 0, -1])
    def test_refuses_a_length_that_is_not_a_length(
        self, length_mm: float | None
    ) -> None:
        # `None`, not `0`. A zero posted to the filament ledger claims the print
        # used nothing and silently stops depleting the spool.
        assert mm_to_grams(length_mm) is None

    @pytest.mark.parametrize("diameter_mm", [0, -1.75])
    def test_refuses_a_diameter_that_is_not_a_diameter(
        self, diameter_mm: float
    ) -> None:
        assert mm_to_grams(1000, diameter_mm=diameter_mm) is None

    @pytest.mark.parametrize("density_g_cm3", [None, 0, -1])
    def test_falls_back_to_the_material_density_for_an_unusable_override(
        self, density_g_cm3: float | None
    ) -> None:
        # An empty or zeroed density column in the database must not zero the
        # weight; the material name is still good information.
        assert mm_to_grams(1000, "ABS", density_g_cm3=density_g_cm3) == mm_to_grams(
            1000, "ABS"
        )
