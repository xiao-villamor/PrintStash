"""Defends get maintenance windows 404 for missing printer at the fleet API integration boundary.

A regression could enqueue, route, or retry work against the wrong printer state.
"""

from __future__ import annotations

from ._fleet_shared import (
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    Printer,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    Session,
    TestClient,
    User,
    _gcode,
    _grant_printer,
    _user_headers,
    fleet,
    select,
    timedelta,
    utcnow,
)


def test_get_maintenance_windows_404_for_missing_printer(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get(
        "/api/v1/fleet/printers/99999/maintenance-windows", headers=auth_headers
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"


def test_post_maintenance_window_404_for_missing_printer(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    now = utcnow()
    resp = client.post(
        "/api/v1/fleet/printers/99999/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(minutes=30)).isoformat(),
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"


def test_patch_maintenance_window_404_for_missing_window(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="WindowMissing",
        moonraker_url="http://window-missing",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    resp = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows/99999",
        headers=auth_headers,
        json={"reason": "Nope"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "maintenance_window_not_found"


def test_delete_maintenance_window_404_for_missing_window(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="WindowDeleteMissing",
        moonraker_url="http://window-delete-missing",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    resp = client.delete(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows/99999",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "maintenance_window_not_found"


def test_get_maintenance_log_404_for_missing_printer(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get(
        "/api/v1/fleet/printers/99999/maintenance-log", headers=auth_headers
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"


def test_post_maintenance_log_404_for_missing_printer(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/fleet/printers/99999/maintenance-log",
        headers=auth_headers,
        json={"category": "nozzle", "note": "test"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"


def test_patch_maintenance_log_404_for_missing_log(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="LogMissing",
        moonraker_url="http://log-missing",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    resp = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log/99999",
        headers=auth_headers,
        json={"note": "Nope"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "maintenance_log_not_found"


def test_delete_maintenance_log_404_for_missing_log(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="LogDeleteMissing",
        moonraker_url="http://log-delete-missing",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    resp = client.delete(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log/99999",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "maintenance_log_not_found"


def test_enqueue_manual_strategy_unknown_printer_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    artifact = _gcode(db_session)
    resp = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "manual", "printer_id": 99999},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"


def test_enqueue_default_strategy_without_default_printer_is_blocked(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="NoDefault", moonraker_url="http://no-default", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)

    resp = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "default"},
    )
    assert resp.status_code == 201
    assert resp.json()["printer_id"] is None
    assert resp.json()["blocked_reason"] == "default_printer_missing"


def test_enqueue_rejects_binary_gcode(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = Model(name="Binary gcode", slug="binary-gcode", hash="e" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    bgcode = File(
        model_id=model.id,
        path="queue/cube.bgcode",
        original_filename="cube.bgcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=10,
        sha256="f" * 64,
    )
    db_session.add(bgcode)
    db_session.commit()
    db_session.refresh(bgcode)

    resp = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": bgcode.id, "strategy": "least_busy"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "binary_gcode_not_printable"


def test_patch_queue_job_strategy_change_reroutes(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    first = Printer(
        name="RerouteA", moonraker_url="http://reroute-a", status=PrinterStatus.READY
    )
    second = Printer(
        name="RerouteB", moonraker_url="http://reroute-b", status=PrinterStatus.READY
    )
    db_session.add_all([first, second])
    db_session.commit()
    db_session.refresh(first)
    db_session.refresh(second)
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={
            "file_id": artifact.id,
            "strategy": "manual",
            "printer_id": first.id,
        },
    ).json()
    assert queued["printer_id"] == first.id

    resp = client.patch(
        f"/api/v1/fleet/queue/{queued['id']}",
        headers=auth_headers,
        json={"strategy": "manual", "printer_id": second.id},
    )
    assert resp.status_code == 200
    assert resp.json()["printer_id"] == second.id
    assert resp.json()["routing_strategy"] == "manual"


def test_patch_routing_clears_drain_reason_when_disabling_drain(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="DrainClear",
        moonraker_url="http://drain-clear",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    on = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/routing",
        headers=auth_headers,
        json={"drain_mode": True, "drain_reason": "Filament swap"},
    )
    assert on.status_code == 200
    assert on.json()["drain_reason"] == "Filament swap"

    off = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/routing",
        headers=auth_headers,
        json={"drain_mode": False},
    )
    assert off.status_code == 200
    assert off.json()["drain_mode"] is False
    assert off.json()["drain_reason"] is None


def test_list_maintenance_windows_returns_created_window(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="WindowList",
        moonraker_url="http://window-list",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    now = utcnow()
    created = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(minutes=30)).isoformat(),
            "reason": "Cleaning",
        },
    )
    assert created.status_code == 201

    listed = client.get(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["reason"] == "Cleaning"


def test_patch_maintenance_window_updates_fields(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="WindowEdit",
        moonraker_url="http://window-edit",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    now = utcnow()
    created = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(minutes=30)).isoformat(),
        },
    ).json()

    # Both bounds are supplied tz-aware so the comparison never falls back to
    # `row.starts_at`/`row.ends_at` read back from the DB (SQLite loses the
    # tzinfo on round-trip; see genuine bug note below).
    resp = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows/{created['id']}",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
            "reason": "Extended",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["reason"] == "Extended"


def test_patch_maintenance_window_invalid_range_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="WindowInvalid",
        moonraker_url="http://window-invalid",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    now = utcnow()
    created = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(minutes=30)).isoformat(),
        },
    ).json()

    # Both bounds supplied tz-aware, same reason as above, to isolate the
    # `ends_at <= starts_at` check from the naive/aware DB round-trip bug.
    resp = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows/{created['id']}",
        headers=auth_headers,
        json={
            "starts_at": now.isoformat(),
            "ends_at": (now - timedelta(hours=1)).isoformat(),
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "maintenance_window_invalid"


def test_create_queue_job_403_for_non_superuser_non_manual_strategy(
    client: TestClient, db_session: Session
) -> None:
    headers = _user_headers(db_session, "queue-member")
    artifact = _gcode(db_session)

    resp = client.post(
        "/api/v1/fleet/queue",
        headers=headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "printer_permission_denied"


def test_create_queue_job_allows_non_superuser_with_printer_role(
    client: TestClient, db_session: Session
) -> None:
    printer = Printer(
        name="MemberManual",
        moonraker_url="http://member-manual",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    collection = Collection(
        name="Member vault", slug="member-vault", path="member-vault"
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    model = Model(
        name="Member cube",
        slug="member-cube",
        hash="9" * 64,
        collection_id=collection.id,
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    artifact = File(
        model_id=model.id,
        path="queue/member-cube.gcode",
        original_filename="member-cube.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=42,
        sha256="8" * 64,
    )
    db_session.add(artifact)
    db_session.commit()
    db_session.refresh(artifact)

    headers = _user_headers(db_session, "manual-member")
    _grant_printer(db_session, "manual-member", printer, PrinterRole.PRINT)
    member = db_session.exec(select(User).where(User.username == "manual-member")).one()
    db_session.add(
        CollectionPermission(
            user_id=member.id, collection_id=collection.id, role=CollectionRole.EDIT
        )
    )
    db_session.commit()

    resp = client.post(
        "/api/v1/fleet/queue",
        headers=headers,
        json={"file_id": artifact.id, "strategy": "manual", "printer_id": printer.id},
    )

    assert resp.status_code == 201
    assert resp.json()["printer_id"] == printer.id


def test_queue_error_maps_fleet_queue_job_not_found_to_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session, monkeypatch
) -> None:
    printer = Printer(
        name="QueueErrorMapping",
        moonraker_url="http://queue-error-mapping",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()

    # _require_queue_job_role already guarantees the job/printer exist by the
    # time the router reaches fleet.retry_queue_job, so a "queue_job_not_found"
    # FleetError from the service layer itself is otherwise unreachable through
    # the HTTP API. Force it to exercise the router's `_queue_error` 404 branch.
    def _raise(*_args, **_kwargs):
        raise fleet.FleetError("queue_job_not_found")

    monkeypatch.setattr(fleet, "retry_queue_job", _raise)
    job = db_session.get(PrintJob, queued["id"])
    job.state = PrintJobState.FAILED
    job.retryable = True
    db_session.add(job)
    db_session.commit()

    resp = client.post(
        f"/api/v1/fleet/queue/{queued['id']}/retry", headers=auth_headers
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "queue_job_not_found"


def test_delete_queue_job_409_when_not_editable(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="DeleteNotEditable",
        moonraker_url="http://delete-not-editable",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()

    job = db_session.get(PrintJob, queued["id"])
    job.state = PrintJobState.PRINTING
    db_session.add(job)
    db_session.commit()

    resp = client.delete(f"/api/v1/fleet/queue/{queued['id']}", headers=auth_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "queue_job_not_editable"
