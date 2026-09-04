"""Unit coverage for small pure helpers that had no dedicated test file.

Covers slug generation, filament length→mass conversion and the parsing
helpers used by profile detection — the leaf functions other services lean on.
"""

from __future__ import annotations

import math

import pytest

from app.services import profile_detection as pd
from app.services.filament import DEFAULT_DIAMETER_MM, density_for, mm_to_grams
from app.services.storage import ensure_unique_slug, slugify
from app.services.taxonomy import parse_tag_input


# --------------------------------------------------------------------------- #
# slugify
# --------------------------------------------------------------------------- #
class TestSlugify:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Hello World", "hello-world"),
            ("  Trim  ", "trim"),
            ("Über Box", "uber-box"),  # NFKD-folds accents to ASCII
            ("C++ Holder", "c-holder"),
            ("a---b", "a-b"),  # runs collapse
            ("MiXeD_Case", "mixed-case"),
            ("--leading-and-trailing--", "leading-and-trailing"),
        ],
    )
    def test_kebab_cases(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "已经", "🎉", "///"])
    def test_unsluggable_falls_back_to_model(self, raw: str) -> None:
        # Empty / non-ASCII-only input must never yield an empty slug.
        assert slugify(raw) == "model"


class TestEnsureUniqueSlug:
    def test_returns_base_when_free(self) -> None:
        assert ensure_unique_slug("benchy", lambda s: False) == "benchy"

    def test_appends_next_free_suffix(self) -> None:
        taken = {"benchy", "benchy-2"}
        assert ensure_unique_slug("benchy", lambda s: s in taken) == "benchy-3"

    def test_starts_numbering_at_two(self) -> None:
        # First collision becomes -2, never -1 / -0.
        assert ensure_unique_slug("x", lambda s: s == "x") == "x-2"


# --------------------------------------------------------------------------- #
# filament length -> mass
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# profile_detection leaf parsers
# --------------------------------------------------------------------------- #
class TestProfileParsers:
    @pytest.mark.parametrize(
        "value, expected",
        [("  x ", "x"), ("", None), ("   ", None), (None, None), (5, "5")],
    )
    def test_clean(self, value, expected) -> None:
        assert pd._clean(value) == expected

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("3.5", 3.5),
            ("0", 0.0),
            ("-3", None),
            ("nan", None),
            ("inf", None),
            ("-inf", None),
            ("abc", None),
            (None, None),
        ],
    )
    def test_to_float_rejects_anything_but_a_positive_number(
        self, value, expected
    ) -> None:
        assert pd._to_float(value) == expected

    def test_infer_cost_per_kg_scales_to_kilogram(self) -> None:
        # 1.0 cost for 20 g => 50.0 per kg.
        meta = {"filament_cost": "1.0", "filament_weight_g": "20"}
        assert pd._infer_cost_per_kg(meta) == 50.0

    @pytest.mark.parametrize(
        "meta",
        [
            {},
            {"filament_cost": "1.0"},  # missing weight
            {"filament_cost": "1.0", "filament_weight_g": "0"},  # zero weight
            {"filament_cost": "abc", "filament_weight_g": "20"},  # bad cost
        ],
    )
    def test_infer_cost_per_kg_none_on_bad_input(self, meta) -> None:
        assert pd._infer_cost_per_kg(meta) is None


# --------------------------------------------------------------------------- #
# taxonomy.parse_tag_input
# --------------------------------------------------------------------------- #
class TestParseTagInput:
    def test_splits_a_list_trimming_each_entry(self) -> None:
        assert parse_tag_input("a, b ,c") == ["a", "b", "c"]

    def test_drops_empty_segments(self) -> None:
        assert parse_tag_input("a, ,,b,") == ["a", "b"]

    @pytest.mark.parametrize("value", [None, "", "   ", ",,,"])
    def test_empty_inputs_return_empty_list(self, value) -> None:
        assert parse_tag_input(value) == []
