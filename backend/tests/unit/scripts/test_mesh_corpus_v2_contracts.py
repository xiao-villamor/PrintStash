"""Geometry expectations reject invalid counters, coordinates and exclusive states."""

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.mesh_corpus_v2_contracts import ExpectedGeometry, VolumeContract


class TestExpectationValidation:
    @pytest.mark.parametrize(
        "invalid",
        [
            {"volume_contract": "unknown"},
            {"triangle_count": True},
            {"volume_contract": VolumeContract.UNKNOWN},
            {"volume_mm3": float("nan")},
            {"bbox_mm": (20, float("inf"), 20)},
        ],
    )
    def test_rejects_invalid_geometry_expectation(self, invalid) -> None:
        valid = ExpectedGeometry(
            12, (20, 20, 20), 8000, VolumeContract.CERTIFIED_MAGNITUDE
        )
        with pytest.raises(ValueError):
            replace(valid, **invalid)
