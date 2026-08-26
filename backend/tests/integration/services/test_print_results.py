"""Tests for services.print_results.resolve_completion_cost."""

from __future__ import annotations

from sqlmodel import Session

from app.db.models import FilamentProfile, File, FileType, Metadata, Model, PrintJob
from app.services import print_results


def _seed_file(db_session: Session, *, sha: str) -> File:
    m = Model(name="M", slug=f"m-{sha}", hash=sha * 64)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    f = File(
        model_id=m.id,
        path=f"/data/{sha}.gcode",
        original_filename=f"{sha}.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=1,
        sha256=sha * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return f


def test_measured_values_win_over_slicer_estimate(db_session: Session):
    db_session.add(
        FilamentProfile(
            name="Hatchbox PLA",
            material_type="PLA",
            material_brand="Hatchbox",
            cost_per_kg=20.0,
        )
    )
    db_session.commit()
    f = _seed_file(db_session, sha="1")
    db_session.add(
        Metadata(file_id=f.id, material_type="PLA", material_brand="Hatchbox")
    )
    db_session.commit()

    job = PrintJob(
        file_id=f.id,
        model_id=f.model_id,
        remote_filename="x",
        filament_used_g=50.0,
        actual_duration_s=600,
    )
    grams, cost = print_results.resolve_completion_cost(db_session, job)
    assert (grams, cost) == (50.0, 1.0)


def test_falls_back_to_slicer_estimate_grams(db_session: Session):
    db_session.add(
        FilamentProfile(
            name="Hatchbox PLA",
            material_type="PLA",
            material_brand="Hatchbox",
            cost_per_kg=20.0,
        )
    )
    db_session.commit()
    f = _seed_file(db_session, sha="2")
    db_session.add(
        Metadata(
            file_id=f.id,
            material_type="PLA",
            material_brand="Hatchbox",
            filament_weight_g=30.0,
        )
    )
    db_session.commit()

    job = PrintJob(file_id=f.id, model_id=f.model_id, remote_filename="x")
    grams, cost = print_results.resolve_completion_cost(db_session, job)
    assert (grams, cost) == (30.0, 0.6)


def test_slicer_cost_used_when_no_profile_match(db_session: Session):
    f = _seed_file(db_session, sha="3")
    db_session.add(
        Metadata(
            file_id=f.id, material_type="ABS", filament_weight_g=10.0, filament_cost=3.5
        )
    )
    db_session.commit()

    job = PrintJob(file_id=f.id, model_id=f.model_id, remote_filename="x")
    _, cost = print_results.resolve_completion_cost(db_session, job)
    assert cost == 3.5


def test_spool_linked_profile_preferred_over_fuzzy_match(db_session: Session):
    db_session.add_all(
        [
            FilamentProfile(
                name="Fuzzy PLA",
                material_type="PLA",
                material_brand="Hatchbox",
                cost_per_kg=20.0,
            ),
            FilamentProfile(
                name="Spool profile",
                material_type="PLA",
                material_brand="Other",
                cost_per_kg=50.0,
                spoolman_filament_id=7,
            ),
        ]
    )
    db_session.commit()
    f = _seed_file(db_session, sha="4")
    db_session.add(
        Metadata(file_id=f.id, material_type="PLA", material_brand="Hatchbox")
    )
    db_session.commit()

    job = PrintJob(
        file_id=f.id,
        model_id=f.model_id,
        remote_filename="x",
        filament_used_g=100.0,
        spool_filament_id=7,
    )
    grams, cost = print_results.resolve_completion_cost(db_session, job)
    # 100g @ 50/kg via the exact spool profile, not the fuzzy 20/kg match.
    assert (grams, cost) == (100.0, 5.0)
