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
    response = client.post(f"/api/v1/fleet/queue/{job.id}/retry", headers=auth_headers)

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
