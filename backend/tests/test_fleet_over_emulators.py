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
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    File,
    FileType,
    Model,
    Printer,
    PrinterStatus,
    PrintJob,
    PrintJobState,
)
from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    get_session_factory,
    override_session_factory,
)
from app.services.printer_hub import PrinterHub
from app.services.printer_provider import build_provider_registry, get_provider_client
from tests.e2e.fakes.mock_printer import create_app
from tests.e2e.fakes.server import start_server


@pytest.fixture(autouse=True)
def _use_file_backed_db(tmp_path: Path, threaded_hub_db: None) -> Iterator[None]:
    """Give concurrent PrinterHub workers production-like SQLite connections.

    Both emulated printers drive real ``asyncio.to_thread`` writes. A temporary
    on-disk database supports WAL, so readers and the two independent worker
    connections do not acquire shared-cache table locks. The dependency keeps
    fixture ordering and restores the suite's default factory on teardown.
    """
    del threaded_hub_db
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fleet-emulators.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    override_session_factory(SQLiteSessionFactory(engine))
    try:
        yield
    finally:
        engine.dispose()


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


def _gcode(session: Session, slug: str) -> File:
    model = Model(name=slug, slug=slug, hash=(slug * 64)[:64])
    session.add(model)
    session.commit()
    session.refresh(model)
    artifact = File(
        model_id=model.id,
        path=f"queue/{slug}.gcode",
        original_filename=f"{slug}.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=42,
        sha256=(slug * 64)[:64],
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


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


async def _stop_hub_tasks(tasks: list[asyncio.Task[None]], stop: asyncio.Event) -> None:
    """Drain in-flight DB syncs before cancelling emulator workers.

    Cancelling a task that is awaiting ``asyncio.to_thread`` does not cancel
    the worker thread. Under coverage that stale write can finish after the
    test has observed the terminal state and make the final assertion flaky.
    """
    stop.set()
    _done, pending = await asyncio.wait(tasks, timeout=2.0)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_dispatch_to_two_emulated_printers_both_complete(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
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
        printer_a = Printer(
            name="Emu A", moonraker_url=running_a.base_url, status=PrinterStatus.READY
        )
        printer_b = Printer(
            name="Emu B", moonraker_url=running_b.base_url, status=PrinterStatus.READY
        )
        db_session.add(printer_a)
        db_session.add(printer_b)
        db_session.commit()
        db_session.refresh(printer_a)
        db_session.refresh(printer_b)

        artifact_1 = _gcode(db_session, "fleetcube1")
        artifact_2 = _gcode(db_session, "fleetcube2")
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

        with patch("app.services.printer_jobs.get_backend", return_value=_Backend()):
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
                row = s.exec(select(PrintJob).where(PrintJob.id == job["id"])).one()
                assert row.state == PrintJobState.COMPLETED
    finally:
        running_a.stop()
        running_b.stop()


def test_draining_printer_is_skipped_by_least_busy_routing(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    app_available, _sim = create_app(
        total_mm=500.0, total_seconds=6.0, print_seconds=1.0
    )
    running_available = start_server(app_available)
    try:
        draining = Printer(
            name="Draining",
            moonraker_url="http://unreachable-draining.invalid",
            status=PrinterStatus.READY,
            drain_mode=True,
            drain_reason="Maintenance",
        )
        available = Printer(
            name="Available",
            moonraker_url=running_available.base_url,
            status=PrinterStatus.READY,
        )
        db_session.add(draining)
        db_session.add(available)
        db_session.commit()
        db_session.refresh(draining)
        db_session.refresh(available)

        artifact = _gcode(db_session, "drainjob")
        queued = client.post(
            "/api/v1/fleet/queue",
            headers=auth_headers,
            json={"file_id": artifact.id, "strategy": "least_busy"},
        ).json()

        with patch("app.services.printer_jobs.get_backend", return_value=_Backend()):
            from app.services.printer_jobs import dispatch_next

            dispatched = asyncio.run(dispatch_next(_provider_builder))
            assert dispatched == queued["id"]

        with get_session_factory().session() as s:
            row = s.get(PrintJob, queued["id"])
            assert row.printer_id == available.id
    finally:
        running_available.stop()
