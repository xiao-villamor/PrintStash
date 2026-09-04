"""Presets learned from slicer metadata, without trampling what a person typed.

Every ingested G-code carries the printer and filament its slicer was configured with,
and PrintStash turns those into local presets so cost and compatibility work out of the
box. The whole risk lives in the *upsert*: a detected value must fill a gap, never
overwrite an operator's own edit. Someone who renamed a preset, corrected its nozzle
size, or typed the real price they paid must still have that value after the next
upload — otherwise the catalogue silently reverts to whatever the last slicer said.

The routers that expose these presets are `integration/api/v1/test_printer_profiles.py`
and `test_filaments.py`.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import FilamentProfile, PrinterProfile
from app.services.profile_detection import (
    upsert_detected_filament_profile,
    upsert_detected_printer_profile,
    upsert_detected_profiles,
)


class TestUpsertDetectedPrinterProfile:
    def test_names_the_preset_after_the_full_slicer_preset(
        self, db_session: Session
    ) -> None:
        upsert_detected_printer_profile(
            db_session,
            {
                "printer_model": "Ender-3 V3 SE",
                "printer_preset_name": "Ender-3 V3 SE 0.4 nozzle",
                "slicer_name": "OrcaSlicer",
                "nozzle_diameter_mm": 0.4,
            },
        )

        row = db_session.exec(select(PrinterProfile)).one()
        assert row.name == "Ender-3 V3 SE 0.4 nozzle"
        assert row.printer_model == "Ender-3 V3 SE"

    def test_upgrades_a_bare_model_name_to_the_preset_name(
        self, db_session: Session
    ) -> None:
        # Auto-created before the preset name was parseable: name == bare model.
        db_session.add(
            PrinterProfile(name="Ender-3 V3 SE", printer_model="Ender-3 V3 SE")
        )
        db_session.commit()

        upsert_detected_printer_profile(
            db_session,
            {
                "printer_model": "Ender-3 V3 SE",
                "printer_preset_name": "Ender-3 V3 SE 0.4 nozzle",
            },
        )

        row = db_session.exec(select(PrinterProfile)).one()
        assert row.name == "Ender-3 V3 SE 0.4 nozzle"

    def test_keeps_a_name_the_operator_chose(self, db_session: Session) -> None:
        db_session.add(
            PrinterProfile(name="My garage Ender", printer_model="Ender-3 V3 SE")
        )
        db_session.commit()

        upsert_detected_printer_profile(
            db_session,
            {
                "printer_model": "Ender-3 V3 SE",
                "printer_preset_name": "Ender-3 V3 SE 0.4 nozzle",
            },
        )

        row = db_session.exec(select(PrinterProfile)).one()
        assert row.name == "My garage Ender", "a rename is a decision, not a gap"

    def test_keeps_values_the_operator_set(self, db_session: Session) -> None:
        db_session.add(
            PrinterProfile(
                name="Voron",
                printer_model="Voron 2.4 350 Klipper",
                slicer_name="ManualSlicer",
                nozzle_diameter_mm=0.6,
            )
        )
        db_session.commit()

        upsert_detected_printer_profile(
            db_session,
            {
                "printer_model": "Voron 2.4 350 Klipper",
                "slicer_name": "OrcaSlicer",
                "nozzle_diameter_mm": 0.4,
            },
        )

        row = db_session.exec(select(PrinterProfile)).one()
        assert row.slicer_name == "ManualSlicer"
        assert row.nozzle_diameter_mm == 0.6

    def test_fills_gaps_on_an_existing_preset(self, db_session: Session) -> None:
        existing = PrinterProfile(
            name="Ender-3",
            printer_model=None,
            slicer_name=None,
            nozzle_diameter_mm=None,
        )
        db_session.add(existing)
        db_session.commit()
        db_session.refresh(existing)

        upsert_detected_printer_profile(
            db_session,
            {
                "printer_model": "Ender-3",
                "printer_preset_name": "Ender-3",
                "slicer_name": "OrcaSlicer",
                "nozzle_diameter_mm": 0.4,
            },
        )

        db_session.refresh(existing)
        assert existing.printer_model == "Ender-3"
        assert existing.slicer_name == "OrcaSlicer"
        assert existing.nozzle_diameter_mm == 0.4

    def test_creates_nothing_without_a_printer_model(self, db_session: Session) -> None:
        assert upsert_detected_printer_profile(db_session, {}) is None
        assert db_session.exec(select(PrinterProfile)).all() == []


class TestUpsertDetectedFilamentProfile:
    def test_infers_the_cost_per_kilogram_from_a_print(
        self, db_session: Session
    ) -> None:
        created = upsert_detected_filament_profile(
            db_session,
            {
                "material_type": "PLA",
                "material_brand": "Generic PLA",
                "filament_weight_g": 12.5,
                "filament_cost": 0.35,
            },
        )

        assert created is not None
        assert created.cost_per_kg == 28  # 0.35 for 12.5 g

    def test_keeps_a_cost_the_operator_set(self, db_session: Session) -> None:
        created = upsert_detected_filament_profile(
            db_session,
            {
                "material_type": "PLA",
                "material_brand": "Generic PLA",
                "filament_weight_g": 12.5,
                "filament_cost": 0.35,
            },
        )
        assert created is not None
        created.cost_per_kg = 22
        db_session.add(created)
        db_session.commit()

        updated = upsert_detected_filament_profile(
            db_session,
            {
                "material_type": "PLA",
                "material_brand": "Generic PLA",
                "filament_weight_g": 10,
                "filament_cost": 0.5,
            },
        )

        assert updated is not None
        assert updated.id == created.id
        assert updated.cost_per_kg == 22, "the price someone typed is not a guess"

    def test_names_a_brand_only_preset_after_the_brand(
        self, db_session: Session
    ) -> None:
        created = upsert_detected_filament_profile(
            db_session, {"material_brand": "OnlyBrand"}
        )

        assert created is not None
        assert created.name == "OnlyBrand"
        assert created.material_type is None

    def test_names_a_type_only_preset_after_the_type(self, db_session: Session) -> None:
        created = upsert_detected_filament_profile(db_session, {"material_type": "ABS"})

        assert created is not None
        assert created.name == "ABS"
        assert created.material_brand is None

    def test_fills_gaps_on_an_existing_preset(self, db_session: Session) -> None:
        existing = FilamentProfile(
            name="Generic PLA",
            material_type=None,
            material_brand="Generic PLA",
            cost_per_kg=None,
            notes=None,
        )
        db_session.add(existing)
        db_session.commit()
        db_session.refresh(existing)

        updated = upsert_detected_filament_profile(
            db_session,
            {
                "material_type": "PLA",
                "material_brand": "Generic PLA",
                "filament_weight_g": 10,
                "filament_cost": 0.5,
            },
        )

        assert updated is not None
        assert updated.id == existing.id
        assert updated.material_type == "PLA"
        assert updated.cost_per_kg == 50

    def test_backfills_a_brand_the_name_already_implied(
        self, db_session: Session
    ) -> None:
        existing = FilamentProfile(
            name="BrandX",
            material_type="PLA",
            material_brand=None,
            cost_per_kg=12.0,
            notes="kept",
        )
        db_session.add(existing)
        db_session.commit()
        db_session.refresh(existing)

        updated = upsert_detected_filament_profile(
            db_session, {"material_type": "PLA", "material_brand": "BrandX"}
        )

        assert updated is not None
        assert updated.material_brand == "BrandX"
        assert updated.cost_per_kg == 12.0

    def test_creates_nothing_without_a_material(self, db_session: Session) -> None:
        assert upsert_detected_filament_profile(db_session, {}) is None
        assert db_session.exec(select(FilamentProfile)).all() == []


class TestUpsertDetectedProfiles:
    def test_creates_the_printer_preset(self, db_session: Session) -> None:
        upsert_detected_profiles(
            db_session, {"material_type": "PETG", "printer_model": "Prusa MK4"}
        )

        assert (
            db_session.exec(
                select(PrinterProfile).where(
                    PrinterProfile.printer_model == "Prusa MK4"
                )
            ).first()
            is not None
        )

    def test_creates_the_filament_preset(self, db_session: Session) -> None:
        upsert_detected_profiles(
            db_session, {"material_type": "PETG", "printer_model": "Prusa MK4"}
        )

        assert (
            db_session.exec(
                select(FilamentProfile).where(FilamentProfile.material_type == "PETG")
            ).first()
            is not None
        )
