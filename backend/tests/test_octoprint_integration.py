"""End-to-end pipeline test against the mock OctoPrint service.

Boots the mock on a real socket, runs the real ``PrinterHub`` polling loop
against it, and asserts the full chain: simulated print -> job COMPLETED.
No provider mocking — exercises ``OctoPrintClient`` for real over HTTP.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.db.models import (
    File,
    FileType,
    Model,
    Printer,
    PrinterProvider,
    PrintJob,
    PrintJobState,
)
from app.db.session import get_session_factory
from app.services.printer_hub import PrinterHub
from app.services.printer_provider import get_provider_client
from tests.e2e.fakes.mock_octoprint import create_app
from tests.e2e.fakes.server import start_server


@pytest.fixture(autouse=True)
def _use_threaded_db(threaded_hub_db: None) -> None:
    """Runs the real PrinterHub against a genuinely concurrent-safe test DB.

    See ``threaded_hub_db`` in tests/conftest.py — this file drives real
    asyncio.to_thread DB writes racing the test's own main-thread reads.
    """


REMOTE = "demo.gcode"
API_KEY = "octo-test-key"


def _seed(db_session: Session, base_url: str) -> tuple[int, int]:
    model = Model(name="Mock", slug="mock-octo-model", hash="o" * 64)
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
        sha256="p" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)

    printer = Printer(
        name="Mock OctoPrint",
        provider=PrinterProvider.OCTOPRINT,
        octoprint_url=base_url,
        octoprint_api_key=API_KEY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    job = PrintJob(
        printer_id=printer.id,
        file_id=f.id,
        model_id=model.id,
        remote_filename=REMOTE,
        state=PrintJobState.STARTED,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    return printer.id, job.id


async def _run_hub(printer_id: int, body) -> None:
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


def test_send_print_completes(db_session: Session) -> None:
    app, sim = create_app(
        total_mm=1000.0, total_seconds=10.0, print_seconds=1.5, api_key=API_KEY
    )
    running = start_server(app)
    try:
        printer_id, job_id = _seed(db_session, running.base_url)

        async def _drive() -> None:
            with get_session_factory().session() as session:
                provider = get_provider_client(session.get(Printer, printer_id))
            await provider.start(REMOTE)
            await _run_hub(
                printer_id, lambda: _wait_job_state(job_id, PrintJobState.COMPLETED)
            )

        asyncio.run(_drive())

        with get_session_factory().session() as s:
            job = s.exec(select(PrintJob).where(PrintJob.id == job_id)).one()
            assert job.state == PrintJobState.COMPLETED
    finally:
        running.stop()


def test_pause_then_resume_runs_to_completion(db_session: Session) -> None:
    app, sim = create_app(
        total_mm=1000.0, total_seconds=10.0, print_seconds=4.0, api_key=API_KEY
    )
    running = start_server(app)
    try:
        printer_id, job_id = _seed(db_session, running.base_url)

        async def _drive() -> None:
            with get_session_factory().session() as session:
                provider = get_provider_client(session.get(Printer, printer_id))
            await provider.start(REMOTE)

            async def body() -> None:
                await provider.pause()
                await _wait_job_state(job_id, PrintJobState.PAUSED)
                await provider.resume()
                await _wait_job_state(job_id, PrintJobState.COMPLETED)

            await _run_hub(printer_id, body)

        asyncio.run(_drive())

        with get_session_factory().session() as s:
            job = s.exec(select(PrintJob).where(PrintJob.id == job_id)).one()
            assert job.state == PrintJobState.COMPLETED
    finally:
        running.stop()


def test_cancel_marks_job_cancelled(db_session: Session) -> None:
    app, sim = create_app(
        total_mm=1000.0, total_seconds=10.0, print_seconds=5.0, api_key=API_KEY
    )
    running = start_server(app)
    try:
        printer_id, job_id = _seed(db_session, running.base_url)

        async def _drive() -> None:
            with get_session_factory().session() as session:
                provider = get_provider_client(session.get(Printer, printer_id))
            await provider.start(REMOTE)

            async def body() -> None:
                await _wait_job_state(job_id, PrintJobState.PRINTING)
                await provider.cancel()
                await _wait_job_state(job_id, PrintJobState.CANCELLED)

            await _run_hub(printer_id, body)

        asyncio.run(_drive())

        with get_session_factory().session() as s:
            job = s.exec(select(PrintJob).where(PrintJob.id == job_id)).one()
            assert job.state == PrintJobState.CANCELLED
    finally:
        running.stop()


def test_invalid_api_key_raises_authentication_error(db_session: Session) -> None:
    import pytest

    from app.services.printer_provider import ProviderError, get_provider_client

    app, _sim = create_app(total_mm=1000.0, total_seconds=10.0, api_key=API_KEY)
    running = start_server(app)
    try:
        printer = Printer(
            name="Bad key",
            provider=PrinterProvider.OCTOPRINT,
            octoprint_url=running.base_url,
            octoprint_api_key="wrong-key",
        )
        client = get_provider_client(printer)

        async def _query() -> None:
            with pytest.raises(ProviderError) as exc_info:
                await client.query_status()
            assert exc_info.value.code == "provider_authentication_failed"

        asyncio.run(_query())
    finally:
        running.stop()
