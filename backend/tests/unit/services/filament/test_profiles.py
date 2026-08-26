"""Defends profiles at the services filament unit boundary.

A regression would misstate filament identity, conversion, or profile metadata to callers.
"""

from __future__ import annotations

from ._helpers_shared import (
    pd,
    pytest,
)


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
    def test_to_float_rejects_negative_and_garbage(self, value, expected) -> None:
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
