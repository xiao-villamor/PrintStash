from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import PrinterProfile
from app.services.profile_detection import upsert_detected_printer_profile


def test_printer_profile_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/printer-profiles",
        headers=auth_headers,
        json={
            "name": "Voron 2.4",
            "printer_model": "Voron 2.4 350 Klipper",
            "slicer_name": "OrcaSlicer",
            "nozzle_diameter_mm": 0.4,
        },
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Voron 2.4"

    updated = client.patch(
        f"/api/v1/printer-profiles/{created.json()['id']}",
        headers=auth_headers,
        json={"notes": "Garage enclosed printer", "nozzle_diameter_mm": 0.6},
    )
    assert updated.status_code == 200
    assert updated.json()["notes"] == "Garage enclosed printer"
    assert updated.json()["nozzle_diameter_mm"] == 0.6

    listed = client.get("/api/v1/printer-profiles", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["printer_model"] == "Voron 2.4 350 Klipper"


def test_detected_printer_profile_does_not_overwrite_manual_values(
    db_session: Session,
) -> None:
    profile = PrinterProfile(
        name="Voron",
        printer_model="Voron 2.4 350 Klipper",
        slicer_name="ManualSlicer",
        nozzle_diameter_mm=0.6,
    )
    db_session.add(profile)
    db_session.commit()

    upsert_detected_printer_profile(
        db_session,
        {
            "printer_model": "Voron 2.4 350 Klipper",
            "slicer_name": "OrcaSlicer",
            "nozzle_diameter_mm": 0.4,
        },
    )

    rows = db_session.exec(select(PrinterProfile)).all()
    assert len(rows) == 1
    assert rows[0].slicer_name == "ManualSlicer"
    assert rows[0].nozzle_diameter_mm == 0.6


def test_detected_printer_profile_uses_full_preset_name(
    db_session: Session,
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

    rows = db_session.exec(select(PrinterProfile)).all()
    assert len(rows) == 1
    assert rows[0].name == "Ender-3 V3 SE 0.4 nozzle"
    assert rows[0].printer_model == "Ender-3 V3 SE"


def test_detected_printer_profile_upgrades_default_name_to_preset(
    db_session: Session,
) -> None:
    # Auto-created before the preset name was parsed: name == bare model.
    db_session.add(PrinterProfile(name="Ender-3 V3 SE", printer_model="Ender-3 V3 SE"))
    db_session.commit()

    upsert_detected_printer_profile(
        db_session,
        {
            "printer_model": "Ender-3 V3 SE",
            "printer_preset_name": "Ender-3 V3 SE 0.4 nozzle",
        },
    )

    rows = db_session.exec(select(PrinterProfile)).all()
    assert len(rows) == 1
    assert rows[0].name == "Ender-3 V3 SE 0.4 nozzle"


def test_detected_printer_profile_keeps_user_renamed_profile(
    db_session: Session,
) -> None:
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

    rows = db_session.exec(select(PrinterProfile)).all()
    assert len(rows) == 1
    assert rows[0].name == "My garage Ender"


def test_detected_printer_profile_backfills_missing_fields_on_existing(
    db_session: Session,
) -> None:
    """An existing profile matched by name with empty model/slicer/nozzle gets
    those fields filled in from freshly parsed slicer metadata."""
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


def test_detected_printer_profile_returns_none_without_model(
    db_session: Session,
) -> None:
    assert upsert_detected_printer_profile(db_session, {}) is None


def test_upsert_detected_profiles_creates_both(db_session: Session) -> None:
    from app.services.profile_detection import upsert_detected_profiles

    upsert_detected_profiles(
        db_session,
        {"material_type": "PETG", "printer_model": "Prusa MK4"},
    )
    assert (
        db_session.exec(
            select(PrinterProfile).where(PrinterProfile.printer_model == "Prusa MK4")
        ).first()
        is not None
    )


def test_printer_profile_create_duplicate_name_conflict(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    payload = {"name": "Dup Printer", "printer_model": "Ender-3"}
    first = client.post(
        "/api/v1/printer-profiles", headers=auth_headers, json=payload
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/printer-profiles", headers=auth_headers, json=payload
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "printer_profile_already_exists"


def test_printer_profile_update_not_found(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.patch(
        "/api/v1/printer-profiles/999",
        headers=auth_headers,
        json={"notes": "x"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_profile_not_found"


def test_printer_profile_update_rename_conflict_and_field_edits(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    a = PrinterProfile(name="Printer A", printer_model="A")
    b = PrinterProfile(name="Printer B", printer_model="B")
    db_session.add(a)
    db_session.add(b)
    db_session.commit()
    db_session.refresh(a)
    db_session.refresh(b)

    conflict = client.patch(
        f"/api/v1/printer-profiles/{b.id}",
        headers=auth_headers,
        json={"name": "Printer A"},
    )
    assert conflict.status_code == 409

    ok = client.patch(
        f"/api/v1/printer-profiles/{b.id}",
        headers=auth_headers,
        json={
            "name": "Printer B Renamed",
            "printer_model": "  Prusa MK4  ",
            "slicer_name": "  PrusaSlicer  ",
        },
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["name"] == "Printer B Renamed"
    assert body["printer_model"] == "Prusa MK4"
    assert body["slicer_name"] == "PrusaSlicer"


def test_printer_profile_delete_success_and_not_found(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    profile = PrinterProfile(name="Deletable Printer", printer_model="Ender-3")
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    profile_id = profile.id

    resp = client.delete(
        f"/api/v1/printer-profiles/{profile_id}", headers=auth_headers
    )
    assert resp.status_code == 204
    db_session.expire_all()
    assert db_session.get(PrinterProfile, profile_id) is None

    missing = client.delete(
        f"/api/v1/printer-profiles/{profile_id}", headers=auth_headers
    )
    assert missing.status_code == 404
