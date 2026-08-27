"""Detected slicer profiles are durable, conservative, and idempotent.

The ingestion pipeline may see the same profile repeatedly and must enrich
missing fields without overwriting operator-maintained values.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import FilamentProfile, PrinterProfile
from app.services import profile_detection


class TestUpsertDetectedFilamentProfile:
    def test_creates_a_filament_profile_from_parsed_slicer_metadata(
        self, db_session: Session
    ) -> None:
        profile = profile_detection.upsert_detected_filament_profile(
            db_session,
            {
                "material_type": "PLA",
                "material_brand": "Polymaker",
                "filament_cost": 2.5,
                "filament_weight_g": 50,
            },
        )

        persisted = db_session.exec(select(FilamentProfile)).one()
        assert profile is not None
        assert persisted.id == profile.id
        assert persisted.name == "Polymaker"
        assert persisted.material_type == "PLA"
        assert persisted.cost_per_kg == 50.0

    def test_reuses_an_existing_matching_profile_case_insensitively(
        self, db_session: Session
    ) -> None:
        existing = FilamentProfile(
            name="Polymaker", material_type="PLA", material_brand="Polymaker"
        )
        db_session.add(existing)
        db_session.commit()
        db_session.refresh(existing)

        result = profile_detection.upsert_detected_filament_profile(
            db_session,
            {"material_type": " pla ", "material_brand": " polymaker "},
        )

        rows = db_session.exec(select(FilamentProfile)).all()
        assert result is not None
        assert result.id == existing.id
        assert [row.id for row in rows] == [existing.id]

    def test_updates_missing_fields_without_overwriting_explicit_profile_values(
        self, db_session: Session
    ) -> None:
        existing = FilamentProfile(
            name="Polymaker",
            material_type=None,
            material_brand="Polymaker",
            cost_per_kg=25.0,
            notes="operator value",
        )
        db_session.add(existing)
        db_session.commit()
        db_session.refresh(existing)

        result = profile_detection.upsert_detected_filament_profile(
            db_session,
            {
                "material_type": "PLA",
                "material_brand": "Polymaker",
                "filament_cost": 2.5,
                "filament_weight_g": 50,
            },
        )

        assert result is not None
        assert result.id == existing.id
        assert result.material_type == "PLA"
        assert result.cost_per_kg == 25.0
        assert result.notes == "operator value"

    def test_leaves_absent_metadata_unknown(self, db_session: Session) -> None:
        result = profile_detection.upsert_detected_filament_profile(db_session, {})

        assert result is None
        assert db_session.exec(select(FilamentProfile)).all() == []


class TestUpsertDetectedPrinterProfile:
    def test_creates_a_printer_profile_from_parsed_slicer_metadata(
        self, db_session: Session
    ) -> None:
        profile = profile_detection.upsert_detected_printer_profile(
            db_session,
            {
                "printer_model": "Ender-3 V3 SE",
                "printer_preset_name": "Ender-3 V3 SE 0.4 nozzle",
                "slicer_name": "OrcaSlicer",
                "nozzle_diameter_mm": 0.4,
            },
        )

        persisted = db_session.exec(select(PrinterProfile)).one()
        assert profile is not None
        assert persisted.id == profile.id
        assert persisted.name == "Ender-3 V3 SE 0.4 nozzle"
        assert persisted.printer_model == "Ender-3 V3 SE"
        assert persisted.nozzle_diameter_mm == 0.4

    def test_reuses_an_existing_matching_printer_profile_case_insensitively(
        self, db_session: Session
    ) -> None:
        existing = PrinterProfile(
            name="Ender-3 V3 SE 0.4 nozzle", printer_model="Ender-3 V3 SE"
        )
        db_session.add(existing)
        db_session.commit()
        db_session.refresh(existing)

        result = profile_detection.upsert_detected_printer_profile(
            db_session,
            {
                "printer_model": " ender-3 v3 se ",
                "printer_preset_name": " ender-3 v3 se 0.4 NOZZLE ",
            },
        )

        rows = db_session.exec(select(PrinterProfile)).all()
        assert result is not None
        assert result.id == existing.id
        assert [row.id for row in rows] == [existing.id]

    def test_leaves_absent_printer_metadata_unknown(self, db_session: Session) -> None:
        result = profile_detection.upsert_detected_printer_profile(db_session, {})

        assert result is None
        assert db_session.exec(select(PrinterProfile)).all() == []
