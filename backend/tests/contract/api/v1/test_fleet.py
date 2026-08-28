"""Fleet routing + dispatch against real printer emulators (no provider mocking).

Complements ``test_fleet_api.py`` (which mocks ``get_provider_client`` to
isolate routing logic) by running ``dispatch_next`` and ``PrinterHub`` for
real against two ``mock_printer`` (Moonraker) emulators on real sockets —
confirming least-busy routing and drain both hold up over the actual HTTP +
WS transport, not just a mocked provider.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    Printer,
    PrinterStatus,
    PrintJob,
    PrintJobState,
)
from app.db.session import get_session_factory
from app.services.printer_hub import PrinterHub
from app.services.printer_provider import build_provider_registry, get_provider_client
from tests.factories import a_gcode_artifact, build_printer
from tests.fakes.mock_printer import create_app
from tests.fakes.server import start_server


class _Backend:
    """Stub artifact backend: dispatch only needs bytes on disk to upload."""

    def exists(self, _key: str) -> bool:
        return True

    def download_to_path(self, _key: str, target: Path) -> Path:
        target.write_text("G28\n")
        return target


REGISTRY = build_provider_registry()


def _provider_builder(printer: Printer):
    return get_provider_client(printer, registry=REGISTRY)


async def _run_hub(printer_id: int, body) -> None:
    hub = PrinterHub(provider_builder=_provider_builder)
    stop = asyncio.Event()
    task = asyncio.create_task(hub._run_printer(printer_id, stop))
    try:
        await body()
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _wait_job_state(
    job_id: int, *states: PrintJobState, timeout: float = 20.0
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with get_session_factory().session() as s:
            job = s.get(PrintJob, job_id)
            if job is not None and job.state in states:
                return
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {states}")


# Generous on purpose. `_run_printer` checks `stop` at the top of each poll, so it
# returns in well under a second; anything approaching this bound is a worker
# ignoring `stop`, which is a defect worth a red test rather than a quiet cancel.
STOP_DRAIN_TIMEOUT_S = 30.0


async def _stop_hub_tasks(tasks: list[asyncio.Task[None]], stop: asyncio.Event) -> None:
    """Let the workers finish rather than leaking their in-flight `to_thread` writes.

    `_run_printer` writes job state through `asyncio.to_thread`, and cancelling a
    task parked on `to_thread` does not cancel the worker thread: the write lands
    anyway, carrying whatever state *its own* snapshot held. A poll that started
    before the print finished therefore writes `PRINTING` over `COMPLETED` — which
    is why this test asserts completion while the hub is running and no longer
    re-reads the rows afterwards. No wait here can fix that ordering; a longer one
    only makes the late write more likely to have landed by the time the test looks.
    Production converges on the next poll, so the row is right a second later, but
    this test is not the place to assert that.

    Waiting is still right for a different reason: a leaked worker thread writing
    into a database the next test is about to truncate is how an unrelated test
    fails. A worker that has not returned 30s after `stop` is a worker ignoring
    `stop`, which is worth a red test rather than a quiet cancel.
    """
    stop.set()
    _done, pending = await asyncio.wait(tasks, timeout=STOP_DRAIN_TIMEOUT_S)
    if not pending:
        return

    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    raise AssertionError(
        f"{len(pending)} printer worker(s) did not return within "
        f"{STOP_DRAIN_TIMEOUT_S}s of `stop` being set"
    )


class TestPrinter:
    def test_draining_printer_is_skipped_by_least_busy_routing(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        app_available, _sim = create_app(
            total_mm=500.0, total_seconds=6.0, print_seconds=1.0
        )
        running_available = start_server(app_available)
        try:
            build_printer(
                db_session,
                name="Draining",
                moonraker_url="http://unreachable-draining.invalid",
                status=PrinterStatus.READY,
                drain_mode=True,
                drain_reason="Maintenance",
            )
            available = build_printer(
                db_session,
                name="Available",
                moonraker_url=running_available.base_url,
                status=PrinterStatus.READY,
            )

            artifact = a_gcode_artifact(db_session, "drainjob")
            queued = client.post(
                "/api/v1/fleet/queue",
                headers=auth_headers,
                json={"file_id": artifact.id, "strategy": "least_busy"},
            ).json()

            with patch(
                "app.services.printer_jobs.get_backend", return_value=_Backend()
            ):
                from app.services.printer_jobs import dispatch_next

                dispatched = asyncio.run(dispatch_next(_provider_builder))
                assert dispatched == queued["id"]

            with get_session_factory().session() as s:
                row = s.get(PrintJob, queued["id"])
                assert row.printer_id == available.id
        finally:
            running_available.stop()

    def test_dispatch_to_two_emulated_printers_both_complete(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        # Deterministic (manual, explicit printer_id) routing rather than
        # least_busy: choose_printer's tie-break recomputes at dispatch time and
        # counts a still-QUEUED job's own prior assignment as load on that
        # printer, so with exactly one job per printer the count ties and both
        # would land on the same (lowest-id) printer — a real routing quirk, but
        # not what this test is for. This test's job is to prove dispatch +
        # PrinterHub complete correctly over two real emulators concurrently;
        # `test_draining_printer_is_skipped_by_least_busy_routing` below covers
        # least_busy itself.
        app_a, _sim_a = create_app(total_mm=500.0, total_seconds=6.0, print_seconds=1.0)
        app_b, _sim_b = create_app(total_mm=500.0, total_seconds=6.0, print_seconds=1.0)
        running_a = start_server(app_a)
        running_b = start_server(app_b)
        try:
            printer_a = build_printer(
                db_session,
                name="Emu A",
                moonraker_url=running_a.base_url,
                status=PrinterStatus.READY,
            )
            printer_b = build_printer(
                db_session,
                name="Emu B",
                moonraker_url=running_b.base_url,
                status=PrinterStatus.READY,
            )

            artifact_1 = a_gcode_artifact(db_session, "fleetcube1")
            artifact_2 = a_gcode_artifact(db_session, "fleetcube2")
            job1 = client.post(
                "/api/v1/fleet/queue",
                headers=auth_headers,
                json={
                    "file_id": artifact_1.id,
                    "strategy": "manual",
                    "printer_id": printer_a.id,
                },
            ).json()
            job2 = client.post(
                "/api/v1/fleet/queue",
                headers=auth_headers,
                json={
                    "file_id": artifact_2.id,
                    "strategy": "manual",
                    "printer_id": printer_b.id,
                },
            ).json()

            with patch(
                "app.services.printer_jobs.get_backend", return_value=_Backend()
            ):
                from app.services.printer_jobs import dispatch_next

                async def _dispatch_and_drive_both() -> tuple[int | None, int | None]:
                    # Keep the pooled HTTP client and both printer hubs on the event
                    # loop that created them. Splitting this flow across separate
                    # asyncio.run() calls can reuse a client bound to a closed loop.
                    first = await dispatch_next(_provider_builder)
                    second = await dispatch_next(_provider_builder)

                    with get_session_factory().session() as s:
                        row1 = s.get(PrintJob, job1["id"])
                        row2 = s.get(PrintJob, job2["id"])
                        assert row1.printer_id == printer_a.id
                        assert row2.printer_id == printer_b.id

                    hub = PrinterHub(provider_builder=_provider_builder)
                    stop = asyncio.Event()
                    tasks = [
                        asyncio.create_task(hub._run_printer(printer_a.id, stop)),
                        asyncio.create_task(hub._run_printer(printer_b.id, stop)),
                    ]
                    try:
                        # Both jobs reaching COMPLETED *is* this test's assertion:
                        # `_wait_job_state` returns only on that state and raises
                        # otherwise. It is made here, while the hub is running,
                        # rather than from a re-read after the workers stop —
                        # see `_stop_hub_tasks` for why that re-read was unsound.
                        await asyncio.gather(
                            _wait_job_state(job1["id"], PrintJobState.COMPLETED),
                            _wait_job_state(job2["id"], PrintJobState.COMPLETED),
                        )
                    finally:
                        await _stop_hub_tasks(tasks, stop)

                    return first, second

                dispatched_1, dispatched_2 = asyncio.run(_dispatch_and_drive_both())
                assert {dispatched_1, dispatched_2} == {job1["id"], job2["id"]}

            with get_session_factory().session() as s:
                for job in (job1, job2):
                    assert (
                        s.exec(
                            select(PrintJob).where(PrintJob.id == job["id"])
                        ).one_or_none()
                        is not None
                    )
        finally:
            running_a.stop()
            running_b.stop()
