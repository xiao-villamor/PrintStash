"""Fleet maintenance log and window lifecycle endpoints."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Printer, PrinterStatus


def _printer(session: Session, name: str) -> Printer:
    printer = Printer(
        name=name,
        moonraker_url=f"http://{name.lower().replace(' ', '-')}",
        status=PrinterStatus.READY,
    )
    session.add(printer)
    session.commit()
    session.refresh(printer)
    return printer


def test_creates_a_maintenance_log_entry(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = _printer(db_session, "Log create")

    response = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log",
        headers=auth_headers,
        json={"category": "service", "note": "Created"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["note"] == "Created"


def test_lists_maintenance_log_entries(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = _printer(db_session, "Log list")
    created = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log",
        headers=auth_headers,
        json={"category": "service", "note": "Listed"},
    ).json()

    response = client.get(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [created["id"]]


def test_updates_a_maintenance_log_entry(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = _printer(db_session, "Log update")
    created = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log",
        headers=auth_headers,
        json={"category": "service", "note": "Before"},
    ).json()

    response = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log/{created['id']}",
        headers=auth_headers,
        json={"note": "After"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["note"] == "After"


def test_deletes_a_maintenance_log_entry(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = _printer(db_session, "Log delete")
    created = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log",
        headers=auth_headers,
        json={"category": "service", "note": "Delete"},
    ).json()

    response = client.delete(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log/{created['id']}",
        headers=auth_headers,
    )

    assert response.status_code == 204, response.text


def test_creates_a_maintenance_window(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = _printer(db_session, "Window create")
    now = utcnow()

    response = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
            "reason": "Service",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["reason"] == "Service"


def test_deletes_a_maintenance_window(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = _printer(db_session, "Window delete")
    now = utcnow()
    created = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
        },
    ).json()

    response = client.delete(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows/{created['id']}",
        headers=auth_headers,
    )

    assert response.status_code == 204, response.text
