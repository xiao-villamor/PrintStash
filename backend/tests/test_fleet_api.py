from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    File,
    FileType,
    Model,
    Printer,
    PrinterStatus,
    PrintJob,
    PrintJobState,
)


def _gcode(session: Session) -> File:
    model = Model(name="Queue cube", slug="queue-cube", hash="a" * 64)
    session.add(model)
    session.commit()
    session.refresh(model)
    artifact = File(
        model_id=model.id,
        path="queue/cube.gcode",
        original_filename="cube.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=42,
        sha256="b" * 64,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


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


def test_queued_jobs_can_be_reordered_and_cancelled(
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

    cancelled = client.delete(
        f"/api/v1/fleet/queue/{first['id']}", headers=auth_headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"


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
        patch("app.services.printer_jobs.get_provider_client", return_value=provider),
    ):
        from app.services.printer_jobs import dispatch_next

        assert asyncio.run(dispatch_next()) == queued["id"]
        assert asyncio.run(dispatch_next()) is None

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

    assert asyncio.run(dispatch_next()) is None
    job = next(
        row
        for row in client.get("/api/v1/fleet/queue", headers=auth_headers).json()
        if row["id"] == queued["id"]
    )
    assert job["state"] == "queued"
    assert job["blocked_reason"] == "printer_unavailable"


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

    summary = client.get("/api/v1/fleet/summary", headers=auth_headers)

    assert summary.status_code == 200
    assert summary.json() == {
        "total_printers": 1,
        "queued_jobs": 1,
        "active_jobs": 0,
        "draining_printers": 1,
        "maintenance_printers": 1,
        "attention_jobs": 1,
    }


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

    assert asyncio.run(dispatch_next()) == queued["id"]
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
        assert asyncio.run(dispatch_next()) == queued["id"]

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
    response = client.post(
        f"/api/v1/fleet/queue/{job.id}/retry", headers=auth_headers
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "queue_job_not_retryable"


def test_fleet_enqueue_notifies_task_queue(
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

    with patch("app.api.v1.fleet.task_queue.enqueue", enqueue):
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
        patch("app.services.printer_jobs.get_provider_client", return_value=provider),
    ):
        assert asyncio.run(dispatch_next()) == queued["id"]

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
        select(PrintJob).where(
            PrintJob.remote_filename == queued["remote_filename"]
        )
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
