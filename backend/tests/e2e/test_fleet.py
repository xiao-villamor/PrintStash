"""E2E: fleet queue + dispatch, driven through the real HTTP API over real
printer emulators (no provider mocking).

Printers are seeded directly on ``e2e_db`` (registering through
``POST /api/v1/printers`` requires ``app.state.printer_hub``, which only
exists once the real lifespan has run -- the same reason
``test_e2e_notifications.py`` seeds printers directly). Everything downstream
of that -- enqueue, dispatch, drain/maintenance blocking, history, restart
reconciliation -- goes through the real ``/api/v1/fleet`` API and the real
``dispatch_next``/``PrinterHub`` service entrypoints, exactly like the rest of
this E2E layer drives ``notifications.dispatch_due()`` directly instead of a
background loop.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.time import utcnow
from app.db.models import (
    ArtifactMaterialRequirement,
    Metadata,
    Printer,
    PrinterStatus,
    PrintJob,
    PrintJobState,
)
from app.services.printer_hub import PrinterHub
from app.services.printer_jobs import dispatch_next, reconcile_stranded_dispatches
from app.services.printer_provider import build_provider_registry, get_provider_client
from tests.factories import (
    a_gcode_artifact,
    build_print_job,
    build_printer,
    printer_config,
)
from tests.fakes.mock_printer import create_app
from tests.fakes.server import start_server

REGISTRY = build_provider_registry()


def _provider_builder(printer: Printer):
    return get_provider_client(printer, registry=REGISTRY)


class _Backend:
    """Stub artifact backend: dispatch only needs bytes on disk to upload."""

    def exists(self, _key: str) -> bool:
        return True

    def download_to_path(self, _key: str, target: Path) -> Path:
        target.write_text("G28\n")
        return target


async def _wait_job_state(
    e2e_db, job_id: int, *states: PrintJobState, timeout: float = 20.0
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        e2e_db.expire_all()
        job = e2e_db.get(PrintJob, job_id)
        if job is not None and job.state in states:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {states}")


class TestFleetOverRealApi:
    """The fleet's whole surface driven the way the frontend drives it.

    Every other fleet test calls a service or a router in-process. These run the
    real app over ASGI against real emulators, so what they add is the parts no
    unit boundary covers: that the endpoints compose into a workflow an operator
    can actually complete, and that a restart in the middle of one recovers."""

    @pytest.mark.asyncio
    async def test_the_queue_can_be_managed_through_the_real_api(
        self, api, superuser_headers, e2e_db
    ):
        printer = build_printer(
            e2e_db,
            name="Queue editor",
            moonraker_url="http://queue-editor.invalid",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(e2e_db, "queue-edit")

        first = (
            await api.post(
                "/api/v1/fleet/queue",
                headers=superuser_headers,
                json={
                    "file_id": artifact.id,
                    "strategy": "manual",
                    "printer_id": printer.id,
                },
            )
        ).json()
        second = (
            await api.post(
                "/api/v1/fleet/queue",
                headers=superuser_headers,
                json={
                    "file_id": artifact.id,
                    "strategy": "manual",
                    "printer_id": printer.id,
                },
            )
        ).json()

        edited = await api.patch(
            f"/api/v1/fleet/queue/{second['id']}",
            headers=superuser_headers,
            json={
                "queue_position": 1,
                "priority": "rush",
                "expected_updated_at": second["updated_at"],
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["priority"] == "rush"

        deleted = await api.delete(
            f"/api/v1/fleet/queue/{first['id']}", headers=superuser_headers
        )
        assert deleted.status_code == 200, deleted.text
        rows = (await api.get("/api/v1/fleet/queue", headers=superuser_headers)).json()
        assert [row["id"] for row in rows] == [second["id"]]

        await api.delete(
            f"/api/v1/fleet/queue/{second['id']}", headers=superuser_headers
        )
        summary = (
            await api.get("/api/v1/fleet/summary", headers=superuser_headers)
        ).json()
        assert summary["queued_jobs"] == 0
        assert summary["active_jobs"] == 0
        assert (
            await api.get("/api/v1/fleet/queue", headers=superuser_headers)
        ).json() == []

    @pytest.mark.asyncio
    async def test_two_printers_complete_their_jobs_through_the_real_api(
        self, api, superuser_headers, e2e_db
    ):
        app_a, _sim_a = create_app(total_mm=500.0, total_seconds=6.0, print_seconds=1.0)
        app_b, _sim_b = create_app(total_mm=500.0, total_seconds=6.0, print_seconds=1.0)
        running_a = start_server(app_a)
        running_b = start_server(app_b)
        try:
            printer_a = printer_config(
                "Emu A", moonraker_url=running_a.base_url, status=PrinterStatus.READY
            )
            printer_b = build_printer(
                e2e_db,
                name="Emu B",
                moonraker_url=running_b.base_url,
                status=PrinterStatus.READY,
            )
            e2e_db.add(printer_a)
            e2e_db.commit()
            e2e_db.refresh(printer_a)
            e2e_db.refresh(printer_b)

            artifact_1 = a_gcode_artifact(e2e_db, "fleetcube1")
            artifact_2 = a_gcode_artifact(e2e_db, "fleetcube2")

            job1 = (
                await api.post(
                    "/api/v1/fleet/queue",
                    headers=superuser_headers,
                    json={
                        "file_id": artifact_1.id,
                        "strategy": "manual",
                        "printer_id": printer_a.id,
                    },
                )
            ).json()
            job2 = (
                await api.post(
                    "/api/v1/fleet/queue",
                    headers=superuser_headers,
                    json={
                        "file_id": artifact_2.id,
                        "strategy": "manual",
                        "printer_id": printer_b.id,
                    },
                )
            ).json()

            with patch(
                "app.services.printer_jobs.get_backend", return_value=_Backend()
            ):
                dispatched_1 = await dispatch_next(_provider_builder)
                dispatched_2 = await dispatch_next(_provider_builder)
                assert {dispatched_1, dispatched_2} == {job1["id"], job2["id"]}

            hub = PrinterHub(provider_builder=_provider_builder)
            stop = asyncio.Event()
            tasks = [
                asyncio.create_task(hub._run_printer(printer_a.id, stop)),
                asyncio.create_task(hub._run_printer(printer_b.id, stop)),
            ]
            try:
                await asyncio.gather(
                    _wait_job_state(e2e_db, job1["id"], PrintJobState.COMPLETED),
                    _wait_job_state(e2e_db, job2["id"], PrintJobState.COMPLETED),
                )
            finally:
                stop.set()
                for t in tasks:
                    t.cancel()
                for t in tasks:
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

            # History + summary are read back through the real API, not the DB.
            queue = (
                await api.get("/api/v1/fleet/queue", headers=superuser_headers)
            ).json()
            completed_ids = {row["id"] for row in queue if row["state"] == "completed"}
            assert {job1["id"], job2["id"]} <= completed_ids

            summary = (
                await api.get("/api/v1/fleet/summary", headers=superuser_headers)
            ).json()
            assert summary["active_jobs"] == 0
            assert summary["queued_jobs"] == 0
            assert summary["total_printers"] == 2
        finally:
            running_a.stop()
            running_b.stop()

    @pytest.mark.asyncio
    async def test_drain_mode_blocks_routing_via_api(
        self, api, superuser_headers, e2e_db
    ):
        app_available, _sim = create_app(
            total_mm=500.0, total_seconds=6.0, print_seconds=1.0
        )
        running = start_server(app_available)
        try:
            draining = printer_config(
                "Draining",
                moonraker_url="http://unreachable-draining.invalid",
                status=PrinterStatus.READY,
            )
            available = build_printer(
                e2e_db,
                name="Available",
                moonraker_url=running.base_url,
                status=PrinterStatus.READY,
            )
            e2e_db.add(draining)
            e2e_db.commit()
            e2e_db.refresh(draining)
            e2e_db.refresh(available)

            # Enter drain through the real routing endpoint.
            drain_resp = await api.patch(
                f"/api/v1/fleet/printers/{draining.id}/routing",
                headers=superuser_headers,
                json={"drain_mode": True, "drain_reason": "Nozzle swap"},
            )
            assert drain_resp.status_code == 200, drain_resp.text
            assert drain_resp.json()["drain_mode"] is True

            artifact = a_gcode_artifact(e2e_db, "drainjob")
            queued = (
                await api.post(
                    "/api/v1/fleet/queue",
                    headers=superuser_headers,
                    json={"file_id": artifact.id, "strategy": "least_busy"},
                )
            ).json()

            with patch(
                "app.services.printer_jobs.get_backend", return_value=_Backend()
            ):
                dispatched = await dispatch_next(_provider_builder)
                assert dispatched == queued["id"]

            row = (
                await api.get("/api/v1/fleet/queue", headers=superuser_headers)
            ).json()
            matched = next(r for r in row if r["id"] == queued["id"])
            assert matched["printer_id"] == available.id

            summary = (
                await api.get("/api/v1/fleet/summary", headers=superuser_headers)
            ).json()
            assert summary["draining_printers"] == 1
        finally:
            running.stop()

    @pytest.mark.asyncio
    async def test_maintenance_window_blocks_manual_routing_via_api(
        self, api, superuser_headers, e2e_db
    ):
        printer = build_printer(
            e2e_db,
            name="Under maintenance",
            moonraker_url="http://unreachable-maint.invalid",
            status=PrinterStatus.READY,
        )

        now = utcnow()
        window = await api.post(
            f"/api/v1/fleet/printers/{printer.id}/maintenance-windows",
            headers=superuser_headers,
            json={
                "starts_at": (now - timedelta(minutes=5)).isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
                "reason": "Bed releveling",
            },
        )
        assert window.status_code == 201, window.text

        artifact = a_gcode_artifact(e2e_db, "maintenancejob")
        queued = (
            await api.post(
                "/api/v1/fleet/queue",
                headers=superuser_headers,
                json={
                    "file_id": artifact.id,
                    "strategy": "manual",
                    "printer_id": printer.id,
                },
            )
        ).json()

        dispatched = await dispatch_next(_provider_builder)
        assert dispatched is None

        row = next(
            r
            for r in (
                await api.get("/api/v1/fleet/queue", headers=superuser_headers)
            ).json()
            if r["id"] == queued["id"]
        )
        assert row["state"] == "queued"
        assert row["blocked_reason"] == "printer_unavailable"

        summary = (
            await api.get("/api/v1/fleet/summary", headers=superuser_headers)
        ).json()
        assert summary["maintenance_printers"] == 1

    @pytest.mark.asyncio
    async def test_a_restart_reconciles_a_stranded_dispatch(
        self, api, superuser_headers, e2e_db
    ):
        printer = build_printer(
            e2e_db,
            name="Restart",
            moonraker_url="http://restart.invalid",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(e2e_db, "restartjob")

        job = build_print_job(
            e2e_db,
            artifact,
            printer_id=printer.id,
            remote_filename="restart.gcode",
            state=PrintJobState.UPLOADING,
            dispatch_claimed_at=utcnow(),
        )

        # Simulate the app restarting mid-dispatch: the reconciler runs at boot.
        assert reconcile_stranded_dispatches() == 1

        row = (await api.get("/api/v1/fleet/queue", headers=superuser_headers)).json()
        matched = next(r for r in row if r["id"] == job.id)
        assert matched["state"] == "failed"
        assert matched["retryable"] is False

        # An ambiguous outcome (provider may already be printing) must never be
        # auto-retried through the API.
        retry = await api.post(
            f"/api/v1/fleet/queue/{job.id}/retry", headers=superuser_headers
        )
        assert retry.status_code == 400
        assert retry.json()["detail"] == "queue_job_not_retryable"

    @pytest.mark.asyncio
    async def test_material_mismatch_override_then_multi_printer_batch_via_real_api(
        self, api, superuser_headers, e2e_db
    ):
        printers = [
            printer_config(
                name,
                moonraker_url=f"http://{name.lower()}.invalid",
                status=PrinterStatus.READY,
                group="material-farm",
            )
            for name in ("PLA A", "PLA B", "ABS")
        ]
        e2e_db.add_all(printers)
        e2e_db.commit()
        for printer in printers:
            e2e_db.refresh(printer)
        artifact = a_gcode_artifact(e2e_db, "material-batch")
        e2e_db.add(
            Metadata(file_id=artifact.id, material_type="PLA", nozzle_diameter_mm=0.4)
        )
        e2e_db.add(
            ArtifactMaterialRequirement(
                file_id=artifact.id,
                tool_index=0,
                material_type="PLA",
                color_hex="#FF0000",
            )
        )
        e2e_db.commit()

        for printer, material in zip(printers, ("PLA", "PLA", "ABS"), strict=True):
            response = await api.put(
                f"/api/v1/printers/{printer.id}/material-state/manual",
                headers=superuser_headers,
                json={
                    "tools": [
                        {
                            "tool_key": "tool0",
                            "label": "Tool 0",
                            "nozzle_diameter_mm": 0.4,
                        }
                    ],
                    "slots": [
                        {
                            "slot_key": "feed",
                            "label": "Feed",
                            "tool_key": "tool0",
                            "state": "loaded",
                            "material_type": material,
                            "color_hex": "#0000FF",
                        }
                    ],
                },
            )
            assert response.status_code == 200, response.text

        mismatch = await api.post(
            "/api/v1/fleet/queue",
            headers=superuser_headers,
            json={
                "file_id": artifact.id,
                "strategy": "manual",
                "printer_id": printers[2].id,
            },
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["detail"] == "material_mismatch_confirmation_required"

        override = await api.post(
            "/api/v1/fleet/queue",
            headers=superuser_headers,
            json={
                "file_id": artifact.id,
                "strategy": "manual",
                "printer_id": printers[2].id,
                "compatibility_policy": "allow_mismatch",
            },
        )
        assert override.status_code == 201, override.text
        assert override.json()["material_override_at"] is not None

        batch = await api.post(
            "/api/v1/fleet/batches",
            headers=superuser_headers,
            json={
                "file_id": artifact.id,
                "quantity": 2,
                "strategy": "least_busy",
                "target_group": "material-farm",
                "priority": "rush",
            },
        )
        assert batch.status_code == 201, batch.text
        body = batch.json()
        assert body["quantity"] == 2
        assert {job["printer_id"] for job in body["jobs"]} == {
            printers[0].id,
            printers[1].id,
        }
        assert [job["copy_index"] for job in body["jobs"]] == [1, 2]
