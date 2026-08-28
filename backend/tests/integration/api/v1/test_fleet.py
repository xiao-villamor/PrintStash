"""Sending a job to a farm of printers, and every reason a printer is skipped.

The fleet endpoints decide which physical machine a print goes to. Getting that
wrong wastes filament at best and starts a print on a machine somebody is
servicing at worst, so most of this file is about the states that make a printer
*ineligible* — drain mode, an active maintenance window, a material the loaded
spool cannot do, an operator gate that has not been released.

Two properties are asserted repeatedly because they are what make a scheduler
safe rather than merely correct:

**Dispatch happens once.** A job claimed by two workers is a job printed twice.
The claim is a conditional write, and the scheduler re-checks eligibility *after*
claiming — a printer put into drain between selection and dispatch must not
receive the job it was already chosen for.

**The scheduler does not block the event loop.** It runs inside the API process,
so a query budget and a threadpool boundary are part of its contract: a fleet
sweep that blocks stalls every request in flight, which looks like the whole
application hanging rather than like a slow scheduler.

Queue order is the third theme. `queue_position` is what the scheduler reads, and
reordering, priority lanes and deletion all have to leave a total order behind.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    CollectionRole,
    FileType,
    Model,
    OperatorGateState,
    Printer,
    PrinterPermission,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
    User,
)
from app.services import fleet
from app.services.auth import create_access_token
from app.services.printer_provider import PrinterProviderClient
from tests.factories import (
    a_gcode_artifact,
    build_collection,
    build_file,
    build_material_requirement,
    build_material_slot,
    build_metadata,
    build_model,
    build_print_job,
    build_printer,
    build_printer_tool,
    build_user,
    grant_collection_role,
    printer_config,
)


def _provider_builder(provider: PrinterProviderClient):
    return lambda _printer: provider


def _unused_provider_builder(_printer: Printer) -> PrinterProviderClient:
    raise AssertionError("provider construction should not be reached")


class TestCreateQueueJobRouting:
    """Where a queued job lands when the caller does not name a printer."""

    def test_routes_a_job_with_no_named_printer_to_the_least_busy_one(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session,
            name="Farm A",
            moonraker_url="http://farm-a.local",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")

        queued = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": artifact.id, "strategy": "least_busy"},
        )

        assert queued.status_code == 201, queued.text
        assert queued.json()["printer_id"] == printer.id
        assert queued.json()["routing_strategy"] == "least_busy"
        assert queued.json()["state"] == "queued"

    def test_shows_the_job_it_queued_in_the_queue(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        build_printer(
            db_session,
            name="Farm A",
            moonraker_url="http://farm-a.local",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        queued = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": artifact.id, "strategy": "least_busy"},
        ).json()

        response = client.get("/api/v1/fleet/queue", headers=auth_headers)

        # A job accepted with a 201 and then absent from the queue is the worst
        # of both: the operator believes it is scheduled and nothing will run it.
        assert response.status_code == 200, response.text
        assert [job["id"] for job in response.json()] == [queued["id"]]

    def test_reports_the_default_printer_with_its_drain_state(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        # Built in this order because the *first* printer registered becomes the
        # fleet default, which is what this test asserts moves.
        first = build_printer(
            db_session,
            name="First",
            moonraker_url="http://first",
            status=PrinterStatus.READY,
        )
        second = build_printer(
            db_session,
            name="Second",
            moonraker_url="http://second",
            status=PrinterStatus.READY,
        )

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
        artifact = a_gcode_artifact(db_session, "Queue cube")
        queued = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": artifact.id, "strategy": "default"},
        )
        assert queued.status_code == 201
        assert queued.json()["printer_id"] == second.id
        assert queued.json()["blocked_reason"] == "default_printer_unavailable"

    def test_blocks_routing_onto_a_printer_inside_a_maintenance_window(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session,
            name="Maintained",
            moonraker_url="http://maintained",
            status=PrinterStatus.READY,
        )
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

        artifact = a_gcode_artifact(db_session, "Queue cube")
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

    def test_absorbed_jobs_are_excluded_from_routing_counts(
        self, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="Count scope",
            moonraker_url="http://count-scope",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        build_print_job(
            db_session,
            artifact,
            printer=printer,
            remote_filename="absorbed.gcode",
            state=PrintJobState.PRINTING,
            source="external",
            dedupe_absorbed_at=utcnow(),
            dedupe_survivor_id=1,
        )

        from app.services.fleet import _active_counts, build_routing_snapshot

        assert build_routing_snapshot(db_session).active_counts == {}
        assert _active_counts(db_session) == {}

    def test_enqueue_404_for_missing_file(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": 99999, "strategy": "least_busy"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "file_not_found"

    def test_enqueue_400_for_non_gcode_file(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = build_model(
            db_session, name="Not gcode", slug="not-gcode", hash="c" * 64
        )
        stl = build_file(
            db_session,
            model,
            path="queue/cube.stl",
            filename="cube.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=10,
            sha256="d" * 64,
        )

        resp = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": stl.id, "strategy": "least_busy"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "file_not_gcode"

    def test_enqueue_rejects_binary_gcode(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = build_model(
            db_session, name="Binary gcode", slug="binary-gcode", hash="e" * 64
        )
        bgcode = build_file(
            db_session,
            model,
            path="queue/cube.bgcode",
            filename="cube.bgcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=10,
            sha256="f" * 64,
        )

        resp = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": bgcode.id, "strategy": "least_busy"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "binary_gcode_not_printable"


class TestListQueueJobs:
    """Reading and reordering the queue."""

    def test_keeps_a_total_queue_order_through_a_reorder_then_a_delete(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        build_printer(
            db_session,
            name="Queue",
            moonraker_url="http://queue",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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

        deleted = client.delete(
            f"/api/v1/fleet/queue/{first['id']}", headers=auth_headers
        )
        assert deleted.status_code == 200
        assert deleted.json()["state"] == "cancelled"
        queue = client.get("/api/v1/fleet/queue", headers=auth_headers).json()
        assert first["id"] not in {row["id"] for row in queue}
        assert queue[0]["queue_position"] == 1

    def test_returns_the_requested_page_of_terminal_history(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session,
            name="History",
            moonraker_url="http://history",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        for index in range(12):
            build_print_job(
                db_session,
                artifact,
                printer=printer,
                remote_filename=f"history-{index}.gcode",
                state=PrintJobState.COMPLETED,
                finished_at=utcnow() + timedelta(seconds=index),
            )
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
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        visible = printer_config("Visible", moonraker_url="http://visible")
        hidden = printer_config("Hidden", moonraker_url="http://hidden")
        user = build_user(
            db_session, username="queue-viewer", password="Password123", active=True
        )
        db_session.add(visible)
        db_session.add(hidden)
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
        artifact = a_gcode_artifact(db_session, "Queue cube")
        now = utcnow()
        for index, printer in enumerate((hidden, visible, hidden, visible)):
            build_print_job(
                db_session,
                artifact,
                printer=printer,
                remote_filename=f"rbac-{index}.gcode",
                state=PrintJobState.COMPLETED,
                finished_at=now + timedelta(seconds=index),
            )
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


class TestQueueScheduler:
    """The loop that actually sends a queued job to a printer."""

    def test_scheduler_dispatches_queued_job_once(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        tmp_path: Path,
    ) -> None:
        printer = build_printer(
            db_session,
            name="Dispatch",
            moonraker_url="http://dispatch",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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

            assert (
                asyncio.run(dispatch_next(_provider_builder(provider))) == queued["id"]
            )
            assert asyncio.run(dispatch_next(_provider_builder(provider))) is None

        job = client.get("/api/v1/fleet/queue", headers=auth_headers).json()[0]
        assert job["state"] == "started"
        assert job["dispatch_attempts"] == 1
        provider.upload.assert_awaited_once()
        provider.start.assert_awaited_once()

    def test_scheduler_rechecks_drain_before_dispatch(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session,
            name="Drain",
            moonraker_url="http://drain",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        queued = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={
                "file_id": artifact.id,
                "strategy": "manual",
                "printer_id": printer.id,
            },
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

    def test_dispatched_job_reuses_same_row_through_completion(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session,
            name="Lifecycle",
            moonraker_url="http://lifecycle",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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
            assert (
                asyncio.run(dispatch_next(_provider_builder(provider))) == queued["id"]
            )

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

    def test_scheduler_candidate_batch_has_a_fixed_query_budget(
        self,
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session,
            name="Offline batch",
            moonraker_url="http://offline-batch",
            status=PrinterStatus.OFFLINE,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        for index in range(120):
            build_print_job(
                db_session,
                artifact,
                printer=printer,
                remote_filename=f"batch-{index}.gcode",
                state=PrintJobState.QUEUED,
                routing_strategy=RoutingStrategy.MANUAL,
                queue_position=index + 1,
            )
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
                    select(PrintJob).where(
                        PrintJob.blocked_reason == "printer_unavailable"
                    )
                ).all()
            )
            == 120
        )

    def test_dispatch_sql_does_not_block_the_event_loop(self, monkeypatch) -> None:
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

    def test_failed_dispatch_can_be_retried(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        build_printer(
            db_session,
            name="Retry",
            moonraker_url="http://retry",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        build_printer(
            db_session,
            name="Ambiguous live dispatch",
            moonraker_url="http://retry",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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

    def test_restart_reconciles_stranded_dispatch(self, db_session: Session) -> None:
        printer = build_printer(
            db_session,
            name="Restart",
            moonraker_url="http://restart",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        job = build_print_job(
            db_session,
            artifact,
            printer_id=printer.id,
            remote_filename="restart.gcode",
            state=PrintJobState.UPLOADING,
            dispatch_claimed_at=utcnow(),
        )

        from app.services.printer_jobs import reconcile_stranded_dispatches

        assert reconcile_stranded_dispatches() == 1
        db_session.expire_all()
        restored = db_session.get(PrintJob, job.id)
        assert restored is not None
        assert restored.state == PrintJobState.FAILED
        assert restored.error == "dispatch_outcome_unknown"
        assert restored.retryable is False

    def test_ambiguous_restart_cannot_be_retried_automatically(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session,
            name="Ambiguous",
            moonraker_url="http://ambiguous",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        job = build_print_job(
            db_session,
            artifact,
            printer_id=printer.id,
            remote_filename="ambiguous.gcode",
            state=PrintJobState.UPLOADING,
            dispatch_claimed_at=utcnow(),
        )

        from app.services.printer_jobs import reconcile_stranded_dispatches

        reconcile_stranded_dispatches()
        response = client.post(
            f"/api/v1/fleet/queue/{job.id}/retry", headers=auth_headers
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "queue_job_not_retryable"


def _user_headers(
    db_session: Session, username: str, *, is_superuser: bool = False
) -> dict[str, str]:
    user = build_user(
        db_session,
        username=username,
        password="Password123",
        active=True,
        superuser=is_superuser,
    )
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


def _grant_printer(
    db_session: Session, username: str, printer: Printer, role: PrinterRole
) -> None:
    user = db_session.exec(select(User).where(User.username == username)).one()
    db_session.add(PrinterPermission(user_id=user.id, printer_id=printer.id, role=role))
    db_session.commit()


def _fleet_printer(session: Session, name: str) -> Printer:
    """A registered printer, for the endpoints that need one named in the body."""
    row = build_printer(
        session, name=name, moonraker_url=f"http://{name.lower().replace(' ', '-')}"
    )
    return row


class TestCheckCompatibility:
    """`POST /fleet/compatibility` — can these printers print this file?"""

    def test_reports_a_file_that_does_not_exist(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        printer = _fleet_printer(db_session, "Compatibility target")

        response = client.post(
            "/api/v1/fleet/compatibility",
            headers=auth_headers,
            json={"file_id": 999_999, "printer_ids": [printer.id]},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "file_not_found"

    def test_reports_a_file_whose_model_is_in_the_trash(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        artifact = a_gcode_artifact(db_session, "Queue cube")
        model = db_session.get(Model, artifact.model_id)
        assert model is not None
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        printer = _fleet_printer(db_session, "Trashed model target")

        response = client.post(
            "/api/v1/fleet/compatibility",
            headers=auth_headers,
            json={"file_id": artifact.id, "printer_ids": [printer.id]},
        )

        # The bytes are still there; the library row is not, so neither is the file.
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "file_not_found"

    def test_reports_a_printer_whose_material_state_cannot_be_read(
        self, client: TestClient, auth_headers, db_session: Session, monkeypatch
    ) -> None:
        from app.api.v1 import fleet as fleet_api

        artifact = a_gcode_artifact(db_session, "Queue cube")

        def unreadable(*_args: object, **_kwargs: object):
            raise fleet_api.materials.MaterialStateError("printer_not_found")

        monkeypatch.setattr(fleet_api.materials, "compatibility_report", unreadable)
        printer = _fleet_printer(db_session, "Unreadable state")

        response = client.post(
            "/api/v1/fleet/compatibility",
            headers=auth_headers,
            json={"file_id": artifact.id, "printer_ids": [printer.id]},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_not_found"

    def test_reports_a_compatible_verdict_for_the_printers_it_was_given(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        printer = _fleet_printer(db_session, "Compatibility ready")
        artifact = a_gcode_artifact(db_session, "Queue cube")
        build_metadata(
            db_session, artifact, material_type="PLA", nozzle_diameter_mm=0.4
        )
        build_material_requirement(db_session, artifact, material_type="PLA")
        build_printer_tool(db_session, printer)
        build_material_slot(db_session, printer, material_type="PLA")

        response = client.post(
            "/api/v1/fleet/compatibility",
            headers=auth_headers,
            json={"file_id": artifact.id, "printer_ids": [printer.id]},
        )

        # The endpoint the operator's "where can I print this?" picker calls, so
        # a verdict that never reaches the response is the same as no feature.
        assert response.status_code == 200, response.text
        assert response.json()["printers"][0]["verdict"] == "compatible"


class TestCreateBatch:
    """`POST /fleet/batches` — queue N copies of one file across the fleet."""

    def test_refuses_an_automatic_strategy_from_a_non_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers = _user_headers(db_session, "batch-member")
        artifact = a_gcode_artifact(db_session, "Queue cube")

        response = client.post(
            "/api/v1/fleet/batches",
            headers=headers,
            json={"file_id": artifact.id, "quantity": 1, "strategy": "least_busy"},
        )

        # Automatic routing can pick any printer in the fleet, including ones the
        # caller has no rights on, so it is a superuser-only strategy.
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "printer_permission_denied"

    def test_refuses_a_manual_batch_with_no_printer_from_a_non_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers = _user_headers(db_session, "batch-no-printer")
        artifact = a_gcode_artifact(db_session, "Queue cube")

        response = client.post(
            "/api/v1/fleet/batches",
            headers=headers,
            json={"file_id": artifact.id, "quantity": 1, "strategy": "manual"},
        )

        assert response.status_code in (400, 403, 422), response.text

    def test_refuses_a_printer_the_caller_may_not_print_on(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers = _user_headers(db_session, "batch-viewer")
        artifact = a_gcode_artifact(db_session, "Queue cube")
        printer = build_printer(
            db_session, name="Not mine", moonraker_url="http://notmine.local:7125"
        )
        _grant_printer(db_session, "batch-viewer", printer, PrinterRole.VIEW)

        response = client.post(
            "/api/v1/fleet/batches",
            headers=headers,
            json={
                "file_id": artifact.id,
                "quantity": 1,
                "strategy": "manual",
                "printer_id": printer.id,
            },
        )

        assert response.status_code == 403, response.text

    def test_checks_the_printer_role_even_for_a_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers = _user_headers(db_session, "batch-admin", is_superuser=True)
        artifact = a_gcode_artifact(db_session, "Queue cube")

        response = client.post(
            "/api/v1/fleet/batches",
            headers=headers,
            json={
                "file_id": artifact.id,
                "quantity": 1,
                "strategy": "manual",
                "printer_id": 999_999,
            },
        )

        # A superuser naming a printer still goes through the same lookup, so an
        # id that does not exist is a 404 rather than a batch queued into nothing.
        assert response.status_code == 404, response.text

    def test_refuses_a_batch_the_loaded_material_cannot_print(
        self, client: TestClient, auth_headers, db_session: Session, monkeypatch
    ) -> None:
        from app.api.v1 import fleet as fleet_api

        artifact = a_gcode_artifact(db_session, "Queue cube")

        def mismatch(*_args: object, **_kwargs: object):
            raise fleet_api.fleet.FleetError("material_mismatch_confirmation_required")

        monkeypatch.setattr(fleet_api.fleet, "create_batch", mismatch)

        response = client.post(
            "/api/v1/fleet/batches",
            headers=auth_headers,
            json={"file_id": artifact.id, "quantity": 1, "strategy": "least_busy"},
        )

        # 409, not 400: the request is well formed and an override would accept it.
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "material_mismatch_confirmation_required"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, db_session: Session
    ) -> None:
        artifact = a_gcode_artifact(db_session, "Queue cube")

        response = client.post(
            "/api/v1/fleet/batches",
            json={"file_id": artifact.id, "quantity": 1, "strategy": "least_busy"},
        )

        assert response.status_code == 401, response.text

    def test_queues_one_job_per_requested_copy(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        _fleet_printer(db_session, "Batch target")
        artifact = a_gcode_artifact(db_session, "Queue cube")

        response = client.post(
            "/api/v1/fleet/batches",
            headers=auth_headers,
            json={"file_id": artifact.id, "quantity": 2, "strategy": "least_busy"},
        )

        assert response.status_code == 201, response.text
        assert len(response.json()["jobs"]) == 2


class TestCreateQueueJob:
    def test_create_queue_job_403_for_non_superuser_non_manual_strategy(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers = _user_headers(db_session, "queue-member")
        artifact = a_gcode_artifact(db_session, "Queue cube")

        resp = client.post(
            "/api/v1/fleet/queue",
            headers=headers,
            json={"file_id": artifact.id, "strategy": "least_busy"},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"

    def test_create_queue_job_allows_non_superuser_with_printer_role(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="MemberManual",
            moonraker_url="http://member-manual",
            status=PrinterStatus.READY,
        )

        collection = build_collection(
            db_session, name="Member vault", slug="member-vault", path="member-vault"
        )
        model = build_model(
            db_session,
            name="Member cube",
            slug="member-cube",
            hash="9" * 64,
            collection_id=collection.id,
        )
        artifact = build_file(
            db_session,
            model,
            path="queue/member-cube.gcode",
            filename="member-cube.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=42,
            sha256="8" * 64,
        )

        headers = _user_headers(db_session, "manual-member")
        _grant_printer(db_session, "manual-member", printer, PrinterRole.PRINT)
        member = db_session.exec(
            select(User).where(User.username == "manual-member")
        ).one()
        grant_collection_role(db_session, member, collection, CollectionRole.EDIT)

        resp = client.post(
            "/api/v1/fleet/queue",
            headers=headers,
            json={
                "file_id": artifact.id,
                "strategy": "manual",
                "printer_id": printer.id,
            },
        )

        assert resp.status_code == 201
        assert resp.json()["printer_id"] == printer.id


class TestQueueError:
    def test_queue_error_maps_fleet_queue_job_not_found_to_404(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        monkeypatch,
    ) -> None:
        build_printer(
            db_session,
            name="QueueErrorMapping",
            moonraker_url="http://queue-error-mapping",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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


class TestRequireQueueJobRole:
    def test_require_queue_job_role_returns_unassigned_job_for_superuser(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        artifact = a_gcode_artifact(db_session, "Queue cube")
        queued = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": artifact.id, "strategy": "default"},
        ).json()
        assert queued["printer_id"] is None  # no default printer configured

        resp = client.delete(
            f"/api/v1/fleet/queue/{queued['id']}", headers=auth_headers
        )

        assert resp.status_code == 200
        assert resp.json()["state"] == "cancelled"
        assert client.get("/api/v1/fleet/queue", headers=auth_headers).json() == []

    def test_require_queue_job_role_404_for_non_superuser_on_unassigned_job(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        artifact = a_gcode_artifact(db_session, "Queue cube")
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


class TestPatchQueueJob:
    def test_patch_queue_job_404_for_missing_job(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.patch(
            "/api/v1/fleet/queue/99999",
            headers=auth_headers,
            json={"queue_position": 1},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "queue_job_not_found"

    def test_patch_queue_job_409_when_not_editable(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        build_printer(
            db_session,
            name="Editable",
            moonraker_url="http://editable",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        build_printer(
            db_session,
            name="Stale",
            moonraker_url="http://stale",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        build_printer(
            db_session,
            name="Manual",
            moonraker_url="http://manual",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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

    def test_patch_queue_job_strategy_change_reroutes(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        first = printer_config(
            "RerouteA",
            moonraker_url="http://reroute-a",
            status=PrinterStatus.READY,
        )
        second = build_printer(
            db_session,
            name="RerouteB",
            moonraker_url="http://reroute-b",
            status=PrinterStatus.READY,
        )
        db_session.add_all([first, second])
        db_session.commit()
        db_session.refresh(first)
        db_session.refresh(second)
        artifact = a_gcode_artifact(db_session, "Queue cube")
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

    def test_patch_queue_job_403_for_non_superuser_strategy_change(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="MemberStrategy",
            moonraker_url="http://member-strategy",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")

        admin_headers = _user_headers(db_session, "strategy-admin", is_superuser=True)
        queued = client.post(
            "/api/v1/fleet/queue",
            headers=admin_headers,
            json={
                "file_id": artifact.id,
                "strategy": "manual",
                "printer_id": printer.id,
            },
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


class TestDeleteQueueJob:
    def test_delete_queue_job_404_for_missing_job(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.delete("/api/v1/fleet/queue/99999", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "queue_job_not_found"

    def test_delete_queue_job_409_when_not_editable(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        build_printer(
            db_session,
            name="DeleteNotEditable",
            moonraker_url="http://delete-not-editable",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        queued = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": artifact.id, "strategy": "least_busy"},
        ).json()

        job = db_session.get(PrintJob, queued["id"])
        job.state = PrintJobState.PRINTING
        db_session.add(job)
        db_session.commit()

        resp = client.delete(
            f"/api/v1/fleet/queue/{queued['id']}", headers=auth_headers
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "queue_job_not_editable"


class TestRetryQueueJob:
    def test_retry_queue_job_404_for_missing_job(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post("/api/v1/fleet/queue/99999/retry", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "queue_job_not_found"

    def test_retry_queue_job_400_when_not_retryable(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        build_printer(
            db_session,
            name="NotRetryable",
            moonraker_url="http://not-retryable",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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

    def test_retry_queue_job_404_when_manual_printer_soft_deleted(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="RetryPrinterGone",
            moonraker_url="http://retry-printer-gone",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        queued = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={
                "file_id": artifact.id,
                "strategy": "manual",
                "printer_id": printer.id,
            },
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


class TestGetMaintenanceWindows:
    def test_get_maintenance_windows_404_for_missing_printer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/fleet/printers/99999/maintenance-windows", headers=auth_headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"

    def test_list_maintenance_windows_returns_created_window(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="WindowList",
            moonraker_url="http://window-list",
            status=PrinterStatus.READY,
        )
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


class TestPostMaintenanceWindow:
    def test_post_maintenance_window_404_for_missing_printer(
        self, client: TestClient, auth_headers: dict[str, str]
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


class TestPatchMaintenanceWindow:
    def test_patch_maintenance_window_404_for_missing_window(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="WindowMissing",
            moonraker_url="http://window-missing",
            status=PrinterStatus.READY,
        )

        resp = client.patch(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-windows/99999",
            headers=auth_headers,
            json={"reason": "Nope"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "maintenance_window_not_found"

    def test_patch_maintenance_window_updates_fields(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="WindowEdit",
            moonraker_url="http://window-edit",
            status=PrinterStatus.READY,
        )
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
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="WindowInvalid",
            moonraker_url="http://window-invalid",
            status=PrinterStatus.READY,
        )
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


class TestGetMaintenanceLog:
    def test_get_maintenance_log_404_for_missing_printer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/fleet/printers/99999/maintenance-log", headers=auth_headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"


class TestPostMaintenanceLog:
    def test_post_maintenance_log_404_for_missing_printer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/api/v1/fleet/printers/99999/maintenance-log",
            headers=auth_headers,
            json={"category": "nozzle", "note": "test"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"


class TestPatchMaintenanceLog:
    def test_patch_maintenance_log_404_for_missing_log(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="LogMissing",
            moonraker_url="http://log-missing",
            status=PrinterStatus.READY,
        )

        resp = client.patch(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-log/99999",
            headers=auth_headers,
            json={"note": "Nope"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "maintenance_log_not_found"


class TestPrinter:
    def test_patch_routing_404_for_missing_printer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.patch(
            "/api/v1/fleet/printers/99999/routing",
            headers=auth_headers,
            json={"is_default": True},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"

    def test_enqueue_manual_strategy_unknown_printer_404(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        artifact = a_gcode_artifact(db_session, "Queue cube")
        resp = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": artifact.id, "strategy": "manual", "printer_id": 99999},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"

    def test_enqueue_default_strategy_without_default_printer_is_blocked(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        build_printer(
            db_session,
            name="NoDefault",
            moonraker_url="http://no-default",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")

        resp = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": artifact.id, "strategy": "default"},
        )
        assert resp.status_code == 201
        assert resp.json()["printer_id"] is None
        assert resp.json()["blocked_reason"] == "default_printer_missing"


class TestFleet:
    def test_summarises_every_state_that_makes_a_printer_unavailable(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session,
            name="Summary",
            moonraker_url="http://summary",
            status=PrinterStatus.READY,
            drain_mode=True,
        )
        now = utcnow()
        client.post(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
            headers=auth_headers,
            json={
                "starts_at": (now - timedelta(minutes=1)).isoformat(),
                "ends_at": (now + timedelta(minutes=10)).isoformat(),
            },
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={
                "file_id": artifact.id,
                "strategy": "manual",
                "printer_id": printer.id,
            },
        )
        build_print_job(
            db_session,
            artifact,
            printer_id=printer.id,
            remote_filename="absorbed.gcode",
            state=PrintJobState.PRINTING,
            source="external",
            dedupe_absorbed_at=utcnow(),
            dedupe_survivor_id=1,
        )
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

    def test_fleet_enqueue_notifies_task_queue(
        self,
        app: FastAPI,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        build_printer(
            db_session,
            name="Wake",
            moonraker_url="http://wake",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
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

    def test_patch_routing_maps_fleet_error_to_404(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        monkeypatch,
    ) -> None:
        printer = build_printer(
            db_session,
            name="RoutingRace",
            moonraker_url="http://routing-race",
            status=PrinterStatus.READY,
        )

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

    def test_maps_a_fleet_error_from_the_maintenance_routes_to_a_404(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        monkeypatch,
    ) -> None:
        printer = build_printer(
            db_session,
            name="MaintenanceRace",
            moonraker_url="http://maintenance-race",
            status=PrinterStatus.READY,
        )
        now = utcnow()

        def _raise(*_args, **_kwargs):
            raise fleet.FleetError("printer_not_found")

        monkeypatch.setattr(fleet, "list_maintenance_windows", _raise)
        resp = client.get(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
            headers=auth_headers,
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


class TestPatch:
    def test_patch_routing_clears_drain_reason_when_disabling_drain(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="DrainClear",
            moonraker_url="http://drain-clear",
            status=PrinterStatus.READY,
        )

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


class TestDelete:
    def test_delete_maintenance_window_404_for_missing_window(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="WindowDeleteMissing",
            moonraker_url="http://window-delete-missing",
            status=PrinterStatus.READY,
        )

        resp = client.delete(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-windows/99999",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "maintenance_window_not_found"

    def test_delete_maintenance_log_404_for_missing_log(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="LogDeleteMissing",
            moonraker_url="http://log-delete-missing",
            status=PrinterStatus.READY,
        )

        resp = client.delete(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-log/99999",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "maintenance_log_not_found"


class TestOperatorDecision:
    """`POST /fleet/queue/{job_id}/operator-decision` — the human between jobs."""

    def test_releases_a_gate_the_operator_has_cleared(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        printer = _fleet_printer(db_session, "Release target")
        artifact = a_gcode_artifact(db_session, "Queue cube")
        job = build_print_job(
            db_session,
            artifact,
            printer=printer,
            remote_filename="release.gcode",
            state=PrintJobState.COMPLETED,
            operator_gate_state=OperatorGateState.PENDING,
        )

        response = client.post(
            f"/api/v1/fleet/queue/{job.id}/operator-decision",
            headers=auth_headers,
            json={"action": "release"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["operator_gate_state"] == "released"

    def test_refuses_to_answer_the_same_gate_twice(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        printer = _fleet_printer(db_session, "Release target")
        artifact = a_gcode_artifact(db_session, "Queue cube")
        job = build_print_job(
            db_session,
            artifact,
            printer=printer,
            remote_filename="release.gcode",
            state=PrintJobState.COMPLETED,
            operator_gate_state=OperatorGateState.PENDING,
        )
        client.post(
            f"/api/v1/fleet/queue/{job.id}/operator-decision",
            headers=auth_headers,
            json={"action": "release"},
        )

        response = client.post(
            f"/api/v1/fleet/queue/{job.id}/operator-decision",
            headers=auth_headers,
            json={"action": "release"},
        )

        # A double-click, or two operators disagreeing. Letting the second answer
        # win would silently undo a hold somebody made deliberately.
        assert response.status_code == 409, response.text
