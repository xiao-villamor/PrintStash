"""Gap-fill for app.services.printer_jobs: transfer_artifact's storage-error
branch, _dispatch_claimed's dependency/capability/readiness guards, and
run_fleet_scheduler's tick loop (dispatch -> sleep(0)-continue vs
task_queue wait, and surviving one bad tick).

test_fleet_api.py already covers the happy path and the generic
except-wraps-into-FAILED branch (a real connection failure to a fake host);
this file targets the specific guard clauses in between.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from app.db.models import (
    File,
    FileType,
    Model,
    Printer,
    PrinterPermission,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
    User,
)
from app.services import printer_jobs
from app.services.printer_jobs import (
    DispatchOutcomeUnknownError,
    PrinterJobError,
    transfer_artifact,
)
from app.services.printer_provider import (
    ProviderCapabilities,
    ProviderError,
)


def _gcode(session: Session, slug: str = "dispatch-cube") -> File:
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


# ---------------------------------------------------------------------------
# transfer_artifact
# ---------------------------------------------------------------------------


def test_transfer_artifact_wraps_download_failure_as_storage_error(
    tmp_path: Path,
) -> None:
    backend = AsyncMock()
    backend.exists = lambda _key: True
    backend.download_to_path = lambda *_a, **_kw: (_ for _ in ()).throw(
        OSError("disk full")
    )
    artifact = File(
        id=1,
        model_id=1,
        path="vault-data/x.gcode",
        original_filename="x.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=1,
        sha256="a" * 64,
    )

    async def _run() -> None:
        with pytest.raises(PrinterJobError, match="storage_error"):
            await transfer_artifact(
                backend, AsyncMock(), artifact, "x.gcode", start_print=True
            )

    asyncio.run(_run())


def test_transfer_artifact_raises_when_blob_missing() -> None:
    backend = AsyncMock()
    backend.exists = lambda _key: False
    artifact = File(
        id=1,
        model_id=1,
        path="vault-data/gone.gcode",
        original_filename="gone.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=1,
        sha256="a" * 64,
    )

    async def _run() -> None:
        with pytest.raises(PrinterJobError, match="file_blob_missing"):
            await transfer_artifact(
                backend, AsyncMock(), artifact, "gone.gcode", start_print=True
            )

    asyncio.run(_run())


def test_transfer_artifact_marks_provider_upload_timeout_as_outcome_unknown(
    tmp_path: Path,
) -> None:
    class Backend:
        def exists(self, _key: str) -> bool:
            return True

        def download_to_path(self, _key: str, target: Path) -> Path:
            target.write_bytes(b"G28\n")
            return target

    artifact = File(
        id=1,
        model_id=1,
        path="vault-data/x.gcode",
        original_filename="x.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=1,
        sha256="a" * 64,
    )
    provider = AsyncMock()
    provider.upload.side_effect = ProviderError("timed out", code="provider_timeout")

    async def _run() -> None:
        with pytest.raises(DispatchOutcomeUnknownError):
            await transfer_artifact(
                provider=provider,
                backend=Backend(),
                artifact=artifact,
                remote_filename="x.gcode",
                start_print=True,
                mark_outcome_unknown=True,
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# _dispatch_claimed guard clauses
# ---------------------------------------------------------------------------


def test_dispatch_claimed_raises_when_printer_missing(db_session: Session) -> None:
    printer = Printer(
        name="Vanishing", moonraker_url="http://vanish", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _gcode(db_session)
    job = PrintJob(
        printer_id=printer.id,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="x.gcode",
        state=PrintJobState.UPLOADING,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    db_session.delete(printer)
    db_session.commit()

    with pytest.raises(RuntimeError, match="queue_dependency_missing"):
        asyncio.run(printer_jobs._dispatch_claimed(job.id))  # noqa: SLF001


def test_dispatch_claimed_raises_when_job_has_no_printer(db_session: Session) -> None:
    artifact = _gcode(db_session)
    job = PrintJob(
        printer_id=None,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="x.gcode",
        state=PrintJobState.UPLOADING,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    with pytest.raises(RuntimeError, match="queue_job_not_found"):
        asyncio.run(printer_jobs._dispatch_claimed(job.id))  # noqa: SLF001


def _seeded_upload_job(db_session: Session) -> tuple[Printer, PrintJob]:
    printer = Printer(
        name="Capabilities", moonraker_url="http://caps", status=PrinterStatus.READY
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _gcode(db_session)
    job = PrintJob(
        printer_id=printer.id,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="x.gcode",
        state=PrintJobState.UPLOADING,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return printer, job


def test_dispatch_rechecks_printer_grant_after_enqueue(db_session: Session) -> None:
    printer = Printer(
        name="Revoked", moonraker_url="http://revoked", status=PrinterStatus.READY
    )
    user = User(username="revoked-user", hashed_password="unused", is_active=True)
    db_session.add_all([printer, user])
    db_session.commit()
    db_session.refresh(printer)
    db_session.refresh(user)
    artifact = _gcode(db_session, "revoked-cube")
    permission = PrinterPermission(
        printer_id=printer.id, user_id=user.id, role=PrinterRole.PRINT
    )
    db_session.add(permission)
    db_session.commit()
    job = PrintJob(
        printer_id=printer.id,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="revoked.gcode",
        state=PrintJobState.QUEUED,
        routing_strategy=RoutingStrategy.MANUAL,
        requested_by=user.id,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.delete(permission)
    db_session.commit()

    assert asyncio.run(printer_jobs.dispatch_next()) is None
    db_session.refresh(job)
    assert job.state == PrintJobState.QUEUED
    assert job.blocked_reason == "printer_access_revoked"


def test_dispatch_claimed_raises_when_provider_cannot_upload_or_start(
    db_session: Session,
) -> None:
    _printer, job = _seeded_upload_job(db_session)
    provider = AsyncMock()
    provider.capabilities = ProviderCapabilities(
        supported=frozenset()
    )  # no START/UPLOAD

    with patch("app.services.printer_jobs.get_provider_client", return_value=provider):
        with pytest.raises(ProviderError, match="operation_not_supported_for_provider"):
            asyncio.run(printer_jobs._dispatch_claimed(job.id))  # noqa: SLF001


def test_dispatch_claimed_raises_printer_not_ready_when_requires_ready_before_send(
    db_session: Session,
) -> None:
    _printer, job = _seeded_upload_job(db_session)
    provider = AsyncMock()
    from app.services.printer_provider import Capability

    provider.capabilities = ProviderCapabilities(
        supported=frozenset({Capability.START, Capability.UPLOAD}),
        requires_ready_before_send=True,
    )
    provider.query_status.return_value = {
        "result": {"status": {"print_stats": {"state": "printing"}}}
    }

    with patch("app.services.printer_jobs.get_provider_client", return_value=provider):
        with pytest.raises(ProviderError, match="printer_not_ready"):
            asyncio.run(printer_jobs._dispatch_claimed(job.id))  # noqa: SLF001


def test_dispatch_claimed_proceeds_when_ready_before_send_reports_idle(
    db_session: Session, tmp_path: Path
) -> None:
    _printer, job = _seeded_upload_job(db_session)
    provider = AsyncMock()
    from app.services.printer_provider import Capability

    provider.capabilities = ProviderCapabilities(
        supported=frozenset({Capability.START, Capability.UPLOAD}),
        requires_ready_before_send=True,
    )
    provider.query_status.return_value = {
        "result": {"status": {"print_stats": {"state": "idle"}}}
    }

    class _Backend:
        def exists(self, _key: str) -> bool:
            return True

        def download_to_path(self, _key: str, target: Path) -> Path:
            target.write_text("G28\n")
            return target

    with (
        patch("app.services.printer_jobs.get_provider_client", return_value=provider),
        patch("app.services.printer_jobs.get_backend", return_value=_Backend()),
    ):
        asyncio.run(printer_jobs._dispatch_claimed(job.id))  # noqa: SLF001

    provider.upload.assert_awaited_once()
    provider.start.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_fleet_scheduler tick loop
# ---------------------------------------------------------------------------


def test_run_fleet_scheduler_dispatches_then_waits_on_task_queue(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int | None] = []

    async def fake_dispatch_next() -> int | None:
        # First tick "dispatches" (truthy), every tick after reports nothing queued.
        result = 42 if not calls else None
        calls.append(result)
        return result

    class _NeverReadyQueue:
        # The real module-level task_queue's asyncio.Queue is a singleton
        # that can end up bound to a previous test's (now-closed) event
        # loop; stub it out so this test only exercises run_fleet_scheduler's
        # own tick logic, not that cross-loop hazard.
        async def dequeue(self):  # noqa: ANN201 - never resolves before the 2s timeout
            await asyncio.sleep(10)

    monkeypatch.setattr(printer_jobs, "dispatch_next", fake_dispatch_next)
    monkeypatch.setattr(printer_jobs, "task_queue", _NeverReadyQueue())
    printer_jobs.scheduler_status.running = False
    printer_jobs.scheduler_status.last_dispatch_at = None

    async def _run() -> None:
        task = asyncio.create_task(printer_jobs.run_fleet_scheduler())
        # Let it dispatch once, then fall into the task_queue.dequeue() wait
        # (2s timeout) at least once before cancelling.
        await asyncio.sleep(0.05)
        assert printer_jobs.scheduler_status.running is True
        assert printer_jobs.scheduler_status.last_dispatch_at is not None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert printer_jobs.scheduler_status.running is False
    assert len(calls) >= 2  # dispatched once, then at least one empty poll


def test_run_fleet_scheduler_survives_a_bad_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_dispatch_next() -> int | None:
        raise RuntimeError("simulated tick failure")

    class _NeverReadyQueue:
        # See test_run_fleet_scheduler_dispatches_then_waits_on_task_queue —
        # isolates this test from the real task_queue singleton's cross-loop hazard.
        async def dequeue(self):  # noqa: ANN201
            await asyncio.sleep(10)

    monkeypatch.setattr(printer_jobs, "dispatch_next", failing_dispatch_next)
    monkeypatch.setattr(printer_jobs, "task_queue", _NeverReadyQueue())
    printer_jobs.scheduler_status.running = False
    printer_jobs.scheduler_status.last_error = None

    async def _run() -> None:
        task = asyncio.create_task(printer_jobs.run_fleet_scheduler())
        await asyncio.sleep(0.05)
        assert printer_jobs.scheduler_status.last_error == "RuntimeError"
        assert printer_jobs.scheduler_status.running is True  # loop kept going
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert printer_jobs.scheduler_status.running is False
