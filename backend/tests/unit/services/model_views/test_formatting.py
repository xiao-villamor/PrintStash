"""Model views preserve filament costs, CSV zeros, and thumbnail identity."""

from __future__ import annotations

import pytest

from app.db.models import (
    FilamentProfile,
    Metadata,
    Model,
)
from app.services import model_views as mv


def _profiles() -> list[FilamentProfile]:
    return [
        FilamentProfile(
            name="Hatchbox PLA",
            material_type="PLA",
            material_brand="Hatchbox",
            cost_per_kg=20.0,
        ),
        FilamentProfile(
            name="Generic PETG",
            material_type="PETG",
            material_brand=None,
            cost_per_kg=25.0,
        ),
        FilamentProfile(
            name="No Cost PLA", material_type="PLA", material_brand="NoCost"
        ),
    ]


class TestMatchingFilamentProfile:
    def test_exact_name_match_case_insensitive(self) -> None:
        md = Metadata(file_id=1, material_brand="hatchbox pla")
        assert mv._matching_filament_profile(_profiles(), md).name == "Hatchbox PLA"

    def test_type_and_brand_match(self) -> None:
        md = Metadata(file_id=1, material_type="PLA", material_brand="Hatchbox")
        assert mv._matching_filament_profile(_profiles(), md).name == "Hatchbox PLA"

    def test_type_only_matches_brandless_profile(self) -> None:
        md = Metadata(file_id=1, material_type="PETG")
        assert mv._matching_filament_profile(_profiles(), md).name == "Generic PETG"

    def test_no_match_returns_none(self) -> None:
        md = Metadata(file_id=1, material_type="ABS")
        assert mv._matching_filament_profile(_profiles(), md) is None


class TestFilamentCostForGrams:
    def test_cost_scales_per_kg(self) -> None:
        md = Metadata(file_id=1, material_type="PLA", material_brand="Hatchbox")
        # 100 g of a 20/kg filament => 2.00.
        assert mv.filament_cost_for_grams(_profiles(), md, 100.0) == 2.0

    def test_none_grams_or_metadata(self) -> None:
        md = Metadata(file_id=1, material_type="PLA", material_brand="Hatchbox")
        assert mv.filament_cost_for_grams(_profiles(), md, None) is None
        assert mv.filament_cost_for_grams(_profiles(), None, 100.0) is None

    def test_profile_without_cost_returns_none(self) -> None:
        md = Metadata(file_id=1, material_type="PLA", material_brand="NoCost")
        assert mv.filament_cost_for_grams(_profiles(), md, 100.0) is None


class TestCsvCell:
    @pytest.mark.parametrize("value", [0, 0.0, False, "x", "0"])
    def test_keeps_real_values_including_zero(self, value) -> None:
        # Regression: a falsy-but-real value (0 % infill, 0 °C bed) must survive.
        assert mv._csv_cell(value) == value

    def test_none_becomes_empty(self) -> None:
        assert mv._csv_cell(None) == ""

    def test_export_csv_preserves_zero_metadata(self) -> None:
        # End-to-end: a vase-mode print (0 % infill, 0 °C bed, 0 top layers)
        # must export "0", not blank, while genuinely-absent fields stay blank.
        import csv
        import io

        payload = {
            "counts": {"models": 1, "files": 1},
            "models": [
                {
                    "id": 1,
                    "name": "Vase",
                    "slug": "vase",
                    "source_url": None,
                    "collection": None,
                    "tags": [],
                    "files": [
                        {
                            "id": 10,
                            "file_type": "gcode",
                            "version": 1,
                            "original_filename": "v.gcode",
                            "size_bytes": 0,
                            "sha256": "abc",
                            "is_recommended": True,
                            "uploaded_at": "2024-01-01",
                            "metadata": {
                                "infill_percent": 0,
                                "bed_temperature_c": 0,
                                "top_shell_layers": 0,
                                "slicer_name": None,
                            },
                        }
                    ],
                }
            ],
        }
        row = next(csv.DictReader(io.StringIO(mv.export_csv(payload))))
        assert row["infill_percent"] == "0"
        assert row["bed_temperature_c"] == "0"
        assert row["top_shell_layers"] == "0"
        assert row["size_bytes"] == "0"
        assert row["slicer_name"] == ""  # truly absent stays blank


class TestThumbUrl:
    def test_prefers_thumbnail_file_id(self) -> None:
        model = Model(
            name="x",
            slug="x",
            hash="a" * 64,
            thumbnail_file_id=7,
            thumbnail_path="99.png",
        )
        assert mv.thumb_url(model) == "/api/v1/files/7/thumbnail"

    def test_falls_back_to_legacy_digit_stem_path(self) -> None:
        model = Model(
            name="x", slug="x", hash="a" * 64, thumbnail_path="uploads/42.png"
        )
        assert mv.thumb_url(model) == "/api/v1/files/42/thumbnail"

    def test_non_digit_legacy_stem_returns_none(self) -> None:
        model = Model(
            name="x", slug="x", hash="a" * 64, thumbnail_path="uploads/legacy.png"
        )
        assert mv.thumb_url(model) is None

    def test_no_thumbnail_at_all_returns_none(self) -> None:
        model = Model(name="x", slug="x", hash="a" * 64)
        assert mv.thumb_url(model) is None
