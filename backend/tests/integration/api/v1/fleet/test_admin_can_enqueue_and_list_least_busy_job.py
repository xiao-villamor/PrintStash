"""Defends admin can enqueue and list least busy job at the fleet API integration boundary.

A regression could enqueue, route, or retry work against the wrong printer state.
"""

from __future__ import annotations

from ._fleet_shared import (
    AsyncMock,
    Path,
    Printer,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
    Session,
    TestClient,
    _gcode,
    _provider_builder,
    _unused_provider_builder,
    asyncio,
    event,
    patch,
    select,
    threading,
    time,
    timedelta,
    utcnow,
)


def test_admin_can_enqueue_and_list_least_busy_job(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Farm A",
        moonraker_url="http://farm-a.local",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _gcode(db_session)

    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    )

    assert queued.status_code == 201
    assert queued.json()["state"] == "queued"
    assert queued.json()["printer_id"] == printer.id
    assert queued.json()["routing_strategy"] == "least_busy"

    response = client.get("/api/v1/fleet/queue", headers=auth_headers)
    assert response.status_code == 200
    assert [job["id"] for job in response.json()] == [queued.json()["id"]]


def test_default_routing_and_soft_drain_are_visible(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    first = Printer(
        name="First", moonraker_url="http://first", status=PrinterStatus.READY
    )
    second = Printer(
        name="Second", moonraker_url="http://second", status=PrinterStatus.READY
    )
    db_session.add(first)
    db_session.add(second)
    db_session.commit()
    db_session.refresh(first)
    db_session.refresh(second)

    configured = client.patch(
        f"/api/v1/fleet/printers/{first.id}/routing",
        headers=auth_headers,
        json={"is_default": True, "drain_mode": True, "drain_reason": "Service"},
    )
    assert configured.status_code == 200
    assert configured.json()["is_default"] is True
    assert configured.json()["drain_mode"] is True

    client.patch(
        f"/api/v1/fleet/printers/{second.id}/routing",
        headers=auth_headers,
        json={"is_default": True},
    )
    printers = client.get("/api/v1/printers", headers=auth_headers).json()
    assert {row["id"] for row in printers if row["is_default"]} == {second.id}

    client.patch(
        f"/api/v1/fleet/printers/{second.id}/routing",
        headers=auth_headers,
        json={"drain_mode": True, "drain_reason": "Nozzle"},
    )
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "default"},
    )
    assert queued.status_code == 201
    assert queued.json()["printer_id"] == second.id
    assert queued.json()["blocked_reason"] == "default_printer_unavailable"


def test_active_maintenance_blocks_routing_and_log_is_recorded(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Maintained", moonraker_url="http://maintained", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    now = utcnow()

    window = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": (now - timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(minutes=30)).isoformat(),
            "reason": "Nozzle replacement",
        },
    )
    assert window.status_code == 201

    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    )
    assert queued.status_code == 201
    assert queued.json()["printer_id"] is None
    assert queued.json()["blocked_reason"] == "no_eligible_printer"

    logged = client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log",
        headers=auth_headers,
        json={"category": "nozzle", "note": "Installed 0.4 mm hardened nozzle"},
    )
    assert logged.status_code == 201
    history = client.get(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log",
        headers=auth_headers,
    )
    assert history.status_code == 200
    assert history.json()[0]["note"] == "Installed 0.4 mm hardened nozzle"
    log_id = history.json()[0]["id"]
    edited = client.patch(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-log/{log_id}",
        headers=auth_headers,
        json={"note": "Installed and calibrated 0.4 mm hardened nozzle"},
    )
    assert edited.status_code == 200
    assert "calibrated" in edited.json()["note"]
    assert (
        client.delete(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-log/{log_id}",
            headers=auth_headers,
        ).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-windows/{window.json()['id']}",
            headers=auth_headers,
        ).status_code
        == 204
    )


def test_queued_jobs_can_be_reordered_and_deleted(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Queue", moonraker_url="http://queue", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    first = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()
    second = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()

    moved = client.patch(
        f"/api/v1/fleet/queue/{second['id']}",
        headers=auth_headers,
        json={"queue_position": 1},
    )
    assert moved.status_code == 200
    queue = client.get("/api/v1/fleet/queue", headers=auth_headers).json()
    assert [row["id"] for row in queue[:2]] == [second["id"], first["id"]]

    changed_lane = client.patch(
        f"/api/v1/fleet/queue/{first['id']}",
        headers=auth_headers,
        json={"priority": "rush"},
    )
    assert changed_lane.status_code == 200
    assert changed_lane.json()["queue_position"] == 1
    returned_to_lane = client.patch(
        f"/api/v1/fleet/queue/{first['id']}",
        headers=auth_headers,
        json={"priority": "normal"},
    )
    assert returned_to_lane.status_code == 200
    assert returned_to_lane.json()["queue_position"] == 2

    deleted = client.delete(f"/api/v1/fleet/queue/{first['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "cancelled"
    queue = client.get("/api/v1/fleet/queue", headers=auth_headers).json()
    assert first["id"] not in {row["id"] for row in queue}
    assert queue[0]["queue_position"] == 1


def test_scheduler_dispatches_queued_job_once(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    tmp_path: Path,
) -> None:
    printer = Printer(
        name="Dispatch", moonraker_url="http://dispatch", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()

    class Backend:
        def exists(self, _key: str) -> bool:
            return True

        def download_to_path(self, _key: str, target: Path) -> Path:
            target.write_text("G28\n")
            return target

    provider = AsyncMock()
    from app.services.printer_provider import capabilities_for_provider

    provider.capabilities = capabilities_for_provider(printer.provider)
    with (
        patch("app.services.printer_jobs.get_backend", return_value=Backend()),
    ):
        from app.services.printer_jobs import dispatch_next

        assert asyncio.run(dispatch_next(_provider_builder(provider))) == queued["id"]
        assert asyncio.run(dispatch_next(_provider_builder(provider))) is None

    job = client.get("/api/v1/fleet/queue", headers=auth_headers).json()[0]
    assert job["state"] == "started"
    assert job["dispatch_attempts"] == 1
    provider.upload.assert_awaited_once()
    provider.start.assert_awaited_once()


def test_scheduler_rechecks_drain_before_dispatch(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Drain", moonraker_url="http://drain", status=PrinterStatus.READY
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
    client.patch(
        f"/api/v1/fleet/printers/{printer.id}/routing",
        headers=auth_headers,
        json={"drain_mode": True, "drain_reason": "Cooling down"},
    )

    from app.services.printer_jobs import dispatch_next

    assert asyncio.run(dispatch_next(_unused_provider_builder)) is None
    job = next(
        row
        for row in client.get("/api/v1/fleet/queue", headers=auth_headers).json()
        if row["id"] == queued["id"]
    )
    assert job["state"] == "queued"
    assert job["blocked_reason"] == "printer_unavailable"


def test_scheduler_candidate_batch_has_a_fixed_query_budget(
    db_session: Session,
) -> None:
    printer = Printer(
        name="Offline batch",
        moonraker_url="http://offline-batch",
        status=PrinterStatus.OFFLINE,
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    for index in range(120):
        db_session.add(
            PrintJob(
                printer_id=printer.id,
                file_id=artifact.id,
                model_id=artifact.model_id,
                remote_filename=f"batch-{index}.gcode",
                state=PrintJobState.QUEUED,
                routing_strategy=RoutingStrategy.MANUAL,
                queue_position=index + 1,
            )
        )
    db_session.commit()
    from app.services.printer_jobs import _claim_next_sync

    statements: list[str] = []
    measured_thread = threading.get_ident()

    def _record(*args) -> None:  # noqa: ANN002
        # The engine is process-wide in the suite. Ignore unrelated provider
        # pollers that may still be finishing a worker-thread transaction.
        if threading.get_ident() == measured_thread:
            statements.append(args[2])

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        assert _claim_next_sync() is None
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    db_session.expire_all()
    blocked = db_session.exec(
        select(PrintJob).where(PrintJob.blocked_reason == "printer_unavailable")
    ).all()
    assert len(blocked) == 100
    assert len(statements) <= 14

    # Previously blocked rows sort after untouched rows, so later ticks cannot
    # starve candidates beyond the bounded first page.
    assert _claim_next_sync() is None
    db_session.expire_all()
    assert (
        len(
            db_session.exec(
                select(PrintJob).where(PrintJob.blocked_reason == "printer_unavailable")
            ).all()
        )
        == 120
    )


def test_dispatch_sql_does_not_block_the_event_loop(monkeypatch) -> None:
    from app.services import printer_jobs

    def _slow_claim() -> None:
        time.sleep(0.2)
        return None

    monkeypatch.setattr(printer_jobs, "_claim_next_sync", _slow_claim)

    async def _run() -> None:
        dispatch = asyncio.create_task(
            printer_jobs.dispatch_next(_unused_provider_builder)
        )
        started = time.monotonic()
        await asyncio.sleep(0.02)
        assert time.monotonic() - started < 0.1
        assert await dispatch is None

    asyncio.run(_run())


def test_fleet_summary_counts_queue_drain_and_maintenance(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Summary",
        moonraker_url="http://summary",
        status=PrinterStatus.READY,
        drain_mode=True,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    now = utcnow()
    client.post(
        f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
        headers=auth_headers,
        json={
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "ends_at": (now + timedelta(minutes=10)).isoformat(),
        },
    )
    artifact = _gcode(db_session)
    client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "manual", "printer_id": printer.id},
    )
    absorbed = PrintJob(
        printer_id=printer.id,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="absorbed.gcode",
        state=PrintJobState.PRINTING,
        source="external",
        dedupe_absorbed_at=utcnow(),
        dedupe_survivor_id=1,
    )
    db_session.add(absorbed)
    db_session.commit()
    from app.services.fleet import _active_counts, build_routing_snapshot

    assert build_routing_snapshot(db_session).active_counts == {printer.id: 1}
    assert _active_counts(db_session) == {printer.id: 1}

    summary = client.get("/api/v1/fleet/summary", headers=auth_headers)

    assert summary.status_code == 200
    payload = summary.json()
    assert {
        key: payload[key]
        for key in (
            "total_printers",
            "queued_jobs",
            "active_jobs",
            "draining_printers",
            "maintenance_printers",
            "attention_jobs",
        )
    } == {
        "total_printers": 1,
        "queued_jobs": 1,
        "active_jobs": 0,
        "draining_printers": 1,
        "maintenance_printers": 1,
        "attention_jobs": 1,
    }
    assert payload["printers"] == [
        {
            "printer_id": printer.id,
            "name": "Summary",
            "status": "ready",
            "progress": None,
            "group": None,
            "loaded_slots": [],
            "nozzle_diameter_mm": None,
            "current_job_id": None,
            "current_job_name": None,
            "current_priority": None,
            "next_job_id": payload["printers"][0]["next_job_id"],
            "next_job_name": payload["printers"][0]["next_job_name"],
            "next_priority": "normal",
            "drain_mode": True,
            "maintenance": True,
            "pending_operator_release": False,
        }
    ]


def test_absorbed_jobs_are_excluded_from_routing_counts(db_session: Session) -> None:
    printer = Printer(
        name="Count scope",
        moonraker_url="http://count-scope",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _gcode(db_session)
    db_session.add(
        PrintJob(
            printer_id=printer.id,
            file_id=artifact.id,
            model_id=artifact.model_id,
            remote_filename="absorbed.gcode",
            state=PrintJobState.PRINTING,
            source="external",
            dedupe_absorbed_at=utcnow(),
            dedupe_survivor_id=1,
        )
    )
    db_session.commit()

    from app.services.fleet import _active_counts, build_routing_snapshot

    assert build_routing_snapshot(db_session).active_counts == {}
    assert _active_counts(db_session) == {}
