"""Defends failed dispatch can be retried at the fleet API integration boundary.

A regression could enqueue, route, or retry work against the wrong printer state.
"""

from __future__ import annotations

from ._fleet_shared import (
    AsyncMock,
    FastAPI,
    File,
    FileType,
    Model,
    Path,
    Printer,
    PrinterPermission,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    Session,
    TestClient,
    User,
    _gcode,
    _provider_builder,
    _unused_provider_builder,
    asyncio,
    create_access_token,
    hash_password,
    patch,
    select,
    timedelta,
    utcnow,
)


def test_failed_dispatch_can_be_retried(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Retry", moonraker_url="http://retry", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()
    from app.services.printer_jobs import dispatch_next

    assert asyncio.run(dispatch_next(_unused_provider_builder)) == queued["id"]
    retried = client.post(
        f"/api/v1/fleet/queue/{queued['id']}/retry", headers=auth_headers
    )

    assert retried.status_code == 200
    assert retried.json()["state"] == "queued"
    assert retried.json()["error"] is None
    assert retried.json()["retryable"] is False


def test_ambiguous_live_dispatch_cannot_be_retried_automatically(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Ambiguous live dispatch",
        moonraker_url="http://retry",
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
    from app.services.printer_jobs import DispatchOutcomeUnknownError, dispatch_next

    with patch(
        "app.services.printer_jobs._dispatch_claimed",
        AsyncMock(side_effect=DispatchOutcomeUnknownError()),
    ):
        assert asyncio.run(dispatch_next(_unused_provider_builder)) == queued["id"]

    db_session.expire_all()
    failed = db_session.get(PrintJob, queued["id"])
    assert failed is not None
    assert failed.error == "dispatch_outcome_unknown"
    assert failed.retryable is False
    retry = client.post(
        f"/api/v1/fleet/queue/{queued['id']}/retry", headers=auth_headers
    )
    assert retry.status_code == 400
    assert retry.json()["detail"] == "queue_job_not_retryable"


def test_restart_reconciles_stranded_dispatch(db_session: Session) -> None:
    printer = Printer(
        name="Restart", moonraker_url="http://restart", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _gcode(db_session)
    job = PrintJob(
        printer_id=printer.id,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="restart.gcode",
        state=PrintJobState.UPLOADING,
        dispatch_claimed_at=utcnow(),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    from app.services.printer_jobs import reconcile_stranded_dispatches

    assert reconcile_stranded_dispatches() == 1
    db_session.expire_all()
    restored = db_session.get(PrintJob, job.id)
    assert restored is not None
    assert restored.state == PrintJobState.FAILED
    assert restored.error == "dispatch_outcome_unknown"
    assert restored.retryable is False


def test_ambiguous_restart_cannot_be_retried_automatically(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Ambiguous", moonraker_url="http://ambiguous", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    job = PrintJob(
        printer_id=printer.id,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="ambiguous.gcode",
        state=PrintJobState.UPLOADING,
        dispatch_claimed_at=utcnow(),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    from app.services.printer_jobs import reconcile_stranded_dispatches

    reconcile_stranded_dispatches()
    response = client.post(f"/api/v1/fleet/queue/{job.id}/retry", headers=auth_headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "queue_job_not_retryable"


def test_fleet_enqueue_notifies_task_queue(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Wake", moonraker_url="http://wake", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    enqueue = AsyncMock()
    app.state.task_queue.enqueue = enqueue

    response = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    )

    assert response.status_code == 201
    enqueue.assert_awaited_once()
    envelope = enqueue.await_args.args[0]
    assert envelope.kind == "fleet_dispatch"
    assert envelope.job_id == str(response.json()["id"])


def test_dispatched_job_reuses_same_row_through_completion(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="Lifecycle", moonraker_url="http://lifecycle", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
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
    from app.services.printer_hub import PrinterHub
    from app.services.printer_jobs import dispatch_next
    from app.services.printer_provider import capabilities_for_provider

    provider.capabilities = capabilities_for_provider(printer.provider)
    with (
        patch("app.services.printer_jobs.get_backend", return_value=Backend()),
    ):
        assert asyncio.run(dispatch_next(_provider_builder(provider))) == queued["id"]

    hub = PrinterHub()
    hub._sync_active_job_db(  # noqa: SLF001 - lifecycle integration seam
        printer.id,
        "printing",
        queued["remote_filename"],
        0.5,
        {},
    )
    hub._sync_active_job_db(  # noqa: SLF001 - lifecycle integration seam
        printer.id,
        "complete",
        queued["remote_filename"],
        1.0,
        {"total_duration": 120},
    )

    db_session.expire_all()
    rows = db_session.exec(
        select(PrintJob).where(PrintJob.remote_filename == queued["remote_filename"])
    ).all()
    assert len(rows) == 1
    assert rows[0].id == queued["id"]
    assert rows[0].state == PrintJobState.COMPLETED
    assert rows[0].actual_duration_s == 120


def test_queue_history_is_bounded_and_pageable(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    printer = Printer(
        name="History", moonraker_url="http://history", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    for index in range(12):
        db_session.add(
            PrintJob(
                printer_id=printer.id,
                file_id=artifact.id,
                model_id=artifact.model_id,
                remote_filename=f"history-{index}.gcode",
                state=PrintJobState.COMPLETED,
                finished_at=utcnow() + timedelta(seconds=index),
            )
        )
    db_session.commit()
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()

    response = client.get(
        "/api/v1/fleet/queue?history_limit=3&history_offset=3",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == queued["id"]
    assert [row["remote_filename"] for row in body[1:]] == [
        "history-8.gcode",
        "history-7.gcode",
        "history-6.gcode",
    ]


def test_queue_history_applies_rbac_before_pagination(
    client: TestClient,
    db_session: Session,
) -> None:
    visible = Printer(name="Visible", moonraker_url="http://visible")
    hidden = Printer(name="Hidden", moonraker_url="http://hidden")
    user = User(
        username="queue-viewer",
        hashed_password=hash_password("Password123"),
        is_active=True,
    )
    db_session.add(visible)
    db_session.add(hidden)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(visible)
    db_session.refresh(hidden)
    db_session.refresh(user)
    db_session.add(
        PrinterPermission(
            user_id=user.id,
            printer_id=visible.id,
            role=PrinterRole.VIEW,
        )
    )
    artifact = _gcode(db_session)
    now = utcnow()
    for index, printer in enumerate((hidden, visible, hidden, visible)):
        db_session.add(
            PrintJob(
                printer_id=printer.id,
                file_id=artifact.id,
                model_id=artifact.model_id,
                remote_filename=f"rbac-{index}.gcode",
                state=PrintJobState.COMPLETED,
                finished_at=now + timedelta(seconds=index),
            )
        )
    db_session.commit()
    token = create_access_token(
        user.id,
        user.username,
        scope="write",
        auth_version=user.auth_version,
    )

    response = client.get(
        "/api/v1/fleet/queue?history_limit=2",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert [row["remote_filename"] for row in response.json()] == [
        "rbac-3.gcode",
        "rbac-1.gcode",
    ]


def test_enqueue_404_for_missing_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": 99999, "strategy": "least_busy"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "file_not_found"


def test_enqueue_400_for_non_gcode_file(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = Model(name="Not gcode", slug="not-gcode", hash="c" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    stl = File(
        model_id=model.id,
        path="queue/cube.stl",
        original_filename="cube.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=10,
        sha256="d" * 64,
    )
    db_session.add(stl)
    db_session.commit()
    db_session.refresh(stl)

    resp = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": stl.id, "strategy": "least_busy"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "file_not_gcode"


def test_patch_queue_job_404_for_missing_job(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.patch(
        "/api/v1/fleet/queue/99999",
        headers=auth_headers,
        json={"queue_position": 1},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "queue_job_not_found"


def test_patch_queue_job_409_when_not_editable(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="Editable", moonraker_url="http://editable", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
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

    resp = client.patch(
        f"/api/v1/fleet/queue/{queued['id']}",
        headers=auth_headers,
        json={"queue_position": 1},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "queue_job_not_editable"


def test_patch_queue_job_409_on_stale_expected_updated_at(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="Stale", moonraker_url="http://stale", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()

    resp = client.patch(
        f"/api/v1/fleet/queue/{queued['id']}",
        headers=auth_headers,
        json={
            "queue_position": 1,
            "expected_updated_at": "2000-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "queue_job_changed"


def test_patch_queue_job_400_manual_strategy_requires_printer_id(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="Manual", moonraker_url="http://manual", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    queued = client.post(
        "/api/v1/fleet/queue",
        headers=auth_headers,
        json={"file_id": artifact.id, "strategy": "least_busy"},
    ).json()

    resp = client.patch(
        f"/api/v1/fleet/queue/{queued['id']}",
        headers=auth_headers,
        json={"strategy": "manual"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "printer_id_required"


def test_delete_queue_job_404_for_missing_job(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.delete("/api/v1/fleet/queue/99999", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "queue_job_not_found"


def test_retry_queue_job_404_for_missing_job(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post("/api/v1/fleet/queue/99999/retry", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "queue_job_not_found"


def test_retry_queue_job_400_when_not_retryable(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    printer = Printer(
        name="NotRetryable",
        moonraker_url="http://not-retryable",
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

    resp = client.post(
        f"/api/v1/fleet/queue/{queued['id']}/retry", headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "queue_job_not_retryable"


def test_patch_routing_404_for_missing_printer(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.patch(
        "/api/v1/fleet/printers/99999/routing",
        headers=auth_headers,
        json={"is_default": True},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "printer_not_found"
