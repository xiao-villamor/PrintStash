"""Defends retry queue job 404 when manual printer soft deleted at the fleet API integration boundary.

A regression could enqueue, route, or retry work against the wrong printer state.
"""

from __future__ import annotations

from ._fleet_shared import (
    Printer,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    Session,
    TestClient,
    _gcode,
    _grant_printer,
    _user_headers,
    fleet,
    timedelta,
    utcnow,
)


def test_retry_queue_job_404_when_manual_printer_soft_deleted(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="RetryPrinterGone",
        moonraker_url="http://retry-printer-gone",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "manual", "printer_id": printer.id},
    ).json()

    job = db_session.get(PrintJob, queued["id"])
    job.state = PrintJobState.FAILED
    job.retryable = True
    db_session.add(job)
    printer.deleted_at = utcnow()
    db_session.add(printer)
    db_session.commit()

    resp = client.post(
        f"/api/v1/fleet/queue/{queued['id']}/retry", headers=auth_headers
    )

    # _require_queue_job_role validates job.printer_id via printer_rbac before
    # the retry route ever calls fleet.retry_queue_job, so a soft-deleted
    # printer 404s here rather than reaching the service layer.
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"


def test_require_queue_job_role_returns_unassigned_job_for_superuser(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "default"},
    ).json()
    assert queued["printer_id"] is None  # no default printer configured

    resp = client.delete(f"/api/v1/fleet/queue/{queued['id']}", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["state"] == "cancelled"
    assert client.get("/api/v1/fleet/queue", headers=auth_headers).json() == []


def test_require_queue_job_role_404_for_non_superuser_on_unassigned_job(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "default"},
    ).json()
    assert queued["printer_id"] is None

    member = _user_headers(db_session, "unassigned-member")
    resp = client.delete(f"/api/v1/fleet/queue/{queued['id']}", headers=member)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "queue_job_not_found"


def test_patch_queue_job_403_for_non_superuser_strategy_change(
    client: TestClient, db_session: Session
) -> None:
    printer = Printer(
        name="MemberStrategy",
        moonraker_url="http://member-strategy",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _gcode(db_session)

    admin_headers = _user_headers(db_session, "strategy-admin", is_superuser=True)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=admin_headers,
        json={"file_id": artifact.id, "strategy": "manual", "printer_id": printer.id},
    ).json()

    member_headers = _user_headers(db_session, "strategy-member")
    _grant_printer(db_session, "strategy-member", printer, PrinterRole.PRINT)

    resp = client.patch(
        f"/api/v1/fleet/queue/{queued['id']}",
        headers=member_headers,
        json={"strategy": "least_busy"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "printer_permission_denied"


def test_patch_routing_maps_fleet_error_to_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session, monkeypatch
) -> None:
    printer = Printer(
        name="RoutingRace",
        moonraker_url="http://routing-race",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    def _raise(*_args, **_kwargs):
        raise fleet.FleetError("printer_not_found")

    monkeypatch.setattr(fleet, "update_routing", _raise)

    resp = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/routing",
        headers=auth_headers,
        json={"is_default": True},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"


def test_maintenance_window_and_log_routes_map_fleet_error_to_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session, monkeypatch
) -> None:
    printer = Printer(
        name="MaintenanceRace",
        moonraker_url="http://maintenance-race",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    now = utcnow()

    def _raise(*_args, **_kwargs):
        raise fleet.FleetError("printer_not_found")

    monkeypatch.setattr(fleet, "list_maintenance_windows", _raise)
    resp = client.get(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows", headers=auth_headers
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"

    monkeypatch.setattr(fleet, "create_maintenance_window", _raise)
    resp = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(minutes=5)).isoformat(),
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"

    monkeypatch.setattr(fleet, "list_maintenance_log", _raise)
    resp = client.get(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log", headers=auth_headers
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"

    monkeypatch.setattr(fleet, "create_maintenance_log", _raise)
    resp = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log",
        headers=auth_headers,
        json={"category": "nozzle", "note": "test"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"
