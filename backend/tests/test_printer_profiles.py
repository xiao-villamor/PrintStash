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
