"""Model-view profile usage is counted from persisted live metadata."""

from __future__ import annotations

from sqlmodel import Session

from app.db.models import (
    FilamentProfile,
    File,
    FileType,
    Metadata,
    Model,
    PrinterProfile,
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


class TestFilamentProfileUsage:
    def test_counts_live_files_matching_each_profile(self, db_session: Session) -> None:
        db_session.add_all(_profiles())
        model = Model(name="m", slug="m", hash="b" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        f1 = File(
            model_id=model.id,
            path="a.gcode",
            original_filename="a.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="1" * 64,
        )
        f2 = File(
            model_id=model.id,
            path="b.gcode",
            original_filename="b.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="2" * 64,
            version=2,
        )
        db_session.add(f1)
        db_session.add(f2)
        db_session.commit()
        db_session.refresh(f1)
        db_session.refresh(f2)
        db_session.add(
            Metadata(file_id=f1.id, material_type="PLA", material_brand="Hatchbox")
        )
        db_session.add(
            Metadata(file_id=f2.id, material_type="PLA", material_brand="Hatchbox")
        )
        db_session.commit()

        usage = mv.filament_profile_usage(db_session)

        hatchbox = next(
            p
            for p in db_session.exec(
                __import__("sqlmodel")
                .select(FilamentProfile)
                .where(FilamentProfile.name == "Hatchbox PLA")
            ).all()
        )
        assert usage[hatchbox.id] == 2


class TestPrinterProfileUsage:
    def test_counts_live_files_matching_printer_model_or_preset_name(
        self, db_session: Session
    ) -> None:
        profile = PrinterProfile(name="Voron 2.4 350", printer_model="Voron 2.4")
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)
        model = Model(name="pm", slug="pm", hash="c" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        f1 = File(
            model_id=model.id,
            path="a.gcode",
            original_filename="a.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="3" * 64,
        )
        db_session.add(f1)
        db_session.commit()
        db_session.refresh(f1)
        db_session.add(Metadata(file_id=f1.id, printer_model="Voron 2.4"))
        db_session.commit()

        usage = mv.printer_profile_usage(db_session)

        assert usage[profile.id] == 1

    def test_blank_printer_model_is_skipped(self, db_session: Session) -> None:
        model = Model(name="pm2", slug="pm2", hash="d" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        f1 = File(
            model_id=model.id,
            path="a.gcode",
            original_filename="a.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="4" * 64,
        )
        db_session.add(f1)
        db_session.commit()
        db_session.refresh(f1)
        db_session.add(Metadata(file_id=f1.id, printer_model=None))
        db_session.commit()

        # No exception, and nothing counted for an unmatched/blank model.
        assert mv.printer_profile_usage(db_session) == {}


class TestMetadataReadLoadsProfiles:
    def test_loads_profiles_itself_when_not_provided(self, db_session: Session) -> None:
        db_session.add_all(_profiles())
        db_session.commit()
        md = Metadata(
            file_id=1,
            material_type="PLA",
            material_brand="Hatchbox",
            filament_weight_g=100.0,
        )

        result = mv.metadata_read(db_session, md)

        assert result.filament_cost == 2.0
