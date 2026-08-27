"""Profile metadata normalization must reject unusable slicer values safely.

These leaf tests defend the pure boundary before parsed values reach persistent
printer and filament profiles.
"""

from __future__ import annotations

import pytest

from app.services import profile_detection


class TestToFloat:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("not-a-number", id="nonnumeric"),
            pytest.param(float("nan"), id="nan"),
            pytest.param(float("inf"), id="positive-infinity"),
            pytest.param(float("-inf"), id="negative-infinity"),
            pytest.param(-0.1, id="negative"),
        ],
    )
    def test_ignores_nonfinite_negative_and_unparseable_numeric_metadata(
        self, value: object
    ) -> None:
        result = profile_detection._to_float(value)

        assert result is None


class TestInferCostPerKg:
    def test_infers_cost_per_kilogram_from_valid_cost_and_weight(self) -> None:
        result = profile_detection._infer_cost_per_kg(
            {"filament_cost": 2.5, "filament_weight_g": 50}
        )

        assert result == 50.0

    def test_does_not_infer_cost_per_kilogram_from_zero_weight(self) -> None:
        result = profile_detection._infer_cost_per_kg(
            {"filament_cost": 2.5, "filament_weight_g": 0}
        )

        assert result is None
