"""End-to-end pipeline test against the mock Moonraker + Spoolman service.

Boots the mock on a real socket, runs the real ``PrinterHub`` WS subscription
against it, and asserts the full chain: simulated print -> job COMPLETED ->
filament grams measured -> Spoolman spool decremented. No provider mocking.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.db.models import File, FileType, Model, Printer, PrintJob, PrintJobState
from app.db.session import get_session_factory
from app.services import runtime_config
from app.services.moonraker import MoonrakerClient, MoonrakerError
from app.services.printer_hub import PrinterHub
from tests.e2e.fakes.mock_printer import create_app
from tests.e2e.fakes.server import start_server


@pytest.fixture(autouse=True)
def _use_threaded_db(threaded_hub_db: None) -> None:
    """Runs the real PrinterHub against a genuinely concurrent-safe test DB.

    See ``threaded_hub_db`` in tests/conftest.py — this file drives real
    asyncio.to_thread DB writes racing the test's own main-thread reads.
    """


REMOTE = "demo.gcode"


def test_mock_enforces_moonraker_http_and_websocket_api_key() -> None:
    app, _state = create_app(api_key="secret")
    running = start_server(app)
    try:

        async def _run() -> None:
            assert (await MoonrakerClient(running.base_url, "secret").info())["result"]
            with pytest.raises(MoonrakerError, match="moonraker 401"):
                await MoonrakerClient(running.base_url, "wrong").info()

            statuses: list[dict] = []
            stop = asyncio.Event()

            async def on_status(status: dict) -> None:
                statuses.append(status)
                stop.set()

            await MoonrakerClient(running.base_url, "secret").subscribe(
                on_status, stop_event=stop
            )
            assert statuses

        asyncio.run(_run())
    finally:
        running.stop()


def _seed(db_session: Session, base_url: str) -> tuple[int, int]:
    model = Model(name="Mock", slug="mock-model", hash="z" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    f = File(
        model_id=model.id,
        path="/data/demo.gcode",
        original_filename=REMOTE,
        file_type=FileType.GCODE,
        version=1,
        size_bytes=100,
        sha256="y" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)

    printer = Printer(name="Mock", moonraker_url=base_url)
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    job = PrintJob(
        printer_id=printer.id,
        file_id=f.id,
        model_id=model.id,
        remote_filename=REMOTE,
        state=PrintJobState.STARTED,
        spool_id=1,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # Enable Spoolman + write-back, pointed at the mock's mounted Spoolman.
    runtime_config.set_spoolman_enabled(db_session, True)
    runtime_config.set_spoolman_write_enabled(db_session, True)
    runtime_config.set_spoolman_config(db_session, base_url=f"{base_url}/spoolman")

    return printer.id, job.id


async def _run_hub(printer_id: int, body) -> None:
    """Run the real ``PrinterHub`` WS loop for one printer while ``body`` drives it.

    ``body`` is an async callback that issues HTTP commands against the mock and
    waits on job state; the hub task is always torn down afterward.
    """
    hub = PrinterHub()
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
) -> PrintJobState:
    """Poll the DB until the job reaches one of ``states`` (or fail loudly)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with get_session_factory().session() as s:
                job = s.get(PrintJob, job_id)
                if job is not None and job.state in states:
                    return job.state
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {states}")


def test_send_print_completes_and_decrements_spoolman(db_session: Session) -> None:
    app, state = create_app(total_mm=1000.0, total_seconds=10.0, print_seconds=1.5)
    running = start_server(app)
    try:
        printer_id, job_id = _seed(db_session, running.base_url)
        start_weight = state.spools[1]["remaining_weight"]

        # Kick off the simulated print on the printer.
        resp = httpx.post(
            f"{running.base_url}/printer/print/start", params={"filename": REMOTE}
        )
        assert resp.status_code == 200

        async def _drive() -> None:
            await _run_hub(
                printer_id,
                lambda: _wait_job_state(job_id, PrintJobState.COMPLETED),
            )

        asyncio.run(_drive())

        with get_session_factory().session() as s:
            job = s.exec(select(PrintJob).where(PrintJob.id == job_id)).one()
            assert job.state == PrintJobState.COMPLETED
            assert job.filament_used_mm == 1000.0
            assert job.filament_used_g and job.filament_used_g > 0

        assert state.spools[1]["remaining_weight"] < start_weight
    finally:
        running.stop()


def test_pause_then_resume_runs_to_completion(db_session: Session) -> None:
    """Pause mid-print is reflected as PAUSED; resuming runs through to COMPLETED."""
    app, _state = create_app(total_mm=1000.0, total_seconds=10.0, print_seconds=4.0)
    running = start_server(app)
    try:
        printer_id, job_id = _seed(db_session, running.base_url)

        async def _drive() -> None:
            async with httpx.AsyncClient(base_url=running.base_url) as http:
                await http.post("/printer/print/start", params={"filename": REMOTE})

                async def body() -> None:
                    # Pause immediately; the hub's WS stream should report PAUSED.
                    await http.post("/printer/print/pause")
                    await _wait_job_state(job_id, PrintJobState.PAUSED)
                    # Resume and let the simulated print finish.
                    await http.post("/printer/print/resume")
                    await _wait_job_state(job_id, PrintJobState.COMPLETED)

                await _run_hub(printer_id, body)

        asyncio.run(_drive())

        with get_session_factory().session() as s:
            job = s.exec(select(PrintJob).where(PrintJob.id == job_id)).one()
            assert job.state == PrintJobState.COMPLETED
            assert job.finished_at is not None
    finally:
        running.stop()


def test_cancel_marks_job_cancelled_and_skips_spoolman(db_session: Session) -> None:
    """A cancelled print finishes as CANCELLED and writes no usage to Spoolman."""
    app, state = create_app(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
    running = start_server(app)
    try:
        printer_id, job_id = _seed(db_session, running.base_url)
        start_weight = state.spools[1]["remaining_weight"]

        async def _drive() -> None:
            async with httpx.AsyncClient(base_url=running.base_url) as http:
                await http.post("/printer/print/start", params={"filename": REMOTE})

                async def body() -> None:
                    # Let it actually start printing, then cancel mid-run.
                    await _wait_job_state(job_id, PrintJobState.PRINTING)
                    await http.post("/printer/print/cancel")
                    await _wait_job_state(job_id, PrintJobState.CANCELLED)

                await _run_hub(printer_id, body)

        asyncio.run(_drive())

        with get_session_factory().session() as s:
            job = s.exec(select(PrintJob).where(PrintJob.id == job_id)).one()
            assert job.state == PrintJobState.CANCELLED
            assert job.finished_at is not None

        # Spoolman write-back only fires on COMPLETED — a cancel must not decrement.
        assert state.spools[1]["remaining_weight"] == start_weight
    finally:
        running.stop()
