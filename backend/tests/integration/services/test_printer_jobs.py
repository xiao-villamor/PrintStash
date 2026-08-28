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
    FileType,
    Printer,
    PrinterPermission,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
)
from app.services import printer_jobs
from app.services.printer_jobs import (
    DispatchOutcomeUnknownError,
    PrinterJobError,
    transfer_artifact,
)
from app.services.printer_provider import (
    PrinterProviderClient,
    ProviderCapabilities,
    ProviderError,
)
from tests.factories import (
    a_gcode_artifact,
    build_print_job,
    build_printer,
    detached_file,
    printer_config,
    user_config,
)


def _provider_builder(provider: PrinterProviderClient):
    return lambda _printer: provider


def _unused_provider_builder(_printer: Printer) -> PrinterProviderClient:
    raise AssertionError("provider construction should not be reached")


# ---------------------------------------------------------------------------
# transfer_artifact
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _dispatch_claimed guard clauses
# ---------------------------------------------------------------------------


def _seeded_upload_job(db_session: Session) -> tuple[Printer, PrintJob]:
    printer = build_printer(
        db_session,
        name="Capabilities",
        moonraker_url="http://caps",
        status=PrinterStatus.READY,
    )
    artifact = a_gcode_artifact(db_session, "Queue cube")
    job = build_print_job(
        db_session,
        artifact,
        printer_id=printer.id,
        remote_filename="x.gcode",
        state=PrintJobState.UPLOADING,
    )
    return printer, job


# ---------------------------------------------------------------------------
# run_fleet_scheduler tick loop
# ---------------------------------------------------------------------------


class TestTransferArtifact:
    def test_transfer_artifact_wraps_download_failure_as_storage_error(
        self,
        tmp_path: Path,
    ) -> None:
        backend = AsyncMock()
        backend.exists = lambda _key: True
        backend.download_to_path = lambda *_a, **_kw: (_ for _ in ()).throw(
            OSError("disk full")
        )
        artifact = detached_file(
            id=1,
            path="vault-data/x.gcode",
            original_filename="x.gcode",
            file_type=FileType.GCODE,
            sha256="a" * 64,
        )

        async def _run() -> None:
            with pytest.raises(PrinterJobError, match="storage_error"):
                await transfer_artifact(
                    backend, AsyncMock(), artifact, "x.gcode", start_print=True
                )

        asyncio.run(_run())

    def test_transfer_artifact_raises_when_blob_missing(self) -> None:
        backend = AsyncMock()
        backend.exists = lambda _key: False
        artifact = detached_file(
            id=1,
            path="vault-data/gone.gcode",
            original_filename="gone.gcode",
            file_type=FileType.GCODE,
            sha256="a" * 64,
        )

        async def _run() -> None:
            with pytest.raises(PrinterJobError, match="file_blob_missing"):
                await transfer_artifact(
                    backend, AsyncMock(), artifact, "gone.gcode", start_print=True
                )

        asyncio.run(_run())

    def test_transfer_artifact_marks_provider_upload_timeout_as_outcome_unknown(
        self,
        tmp_path: Path,
    ) -> None:
        class Backend:
            def exists(self, _key: str) -> bool:
                return True

            def download_to_path(self, _key: str, target: Path) -> Path:
                target.write_bytes(b"G28\n")
                return target

        artifact = detached_file(
            id=1,
            path="vault-data/x.gcode",
            original_filename="x.gcode",
            file_type=FileType.GCODE,
            sha256="a" * 64,
        )
        provider = AsyncMock()
        provider.upload.side_effect = ProviderError(
            "timed out", code="provider_timeout"
        )

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


class TestDispatchClaimed:
    def test_dispatch_claimed_raises_when_the_printer_row_cannot_be_loaded(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `queue_dependency_missing` guard, which a correct schema makes
        unreachable.

        This used to hard-delete the printer out from under a queued job. That is
        refused now — `print_jobs.printer_id` is a RESTRICT foreign key and the suite
        enforces foreign keys, as production does — and refusing it is right: the
        state is not reachable through any code path. The guard still earns its
        keep, because an installation upgraded from an older release is missing
        several of those constraints (see
        `tests/integration/db/migrations/test_models_versus_chain.py`), so on that
        schema the row really can vanish.

        Making the lookup return `None` is therefore the honest way to reach it:
        the behaviour under test is how dispatch reacts to a dependency it cannot
        load, not the database's ability to lose one.
        """
        printer = build_printer(
            db_session,
            name="Vanishing",
            moonraker_url="http://vanish",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        job = build_print_job(
            db_session,
            artifact,
            printer_id=printer.id,
            remote_filename="x.gcode",
            state=PrintJobState.UPLOADING,
        )
        real_get = Session.get

        def get(self, entity, ident, *args, **kwargs):
            if entity is Printer:
                return None
            return real_get(self, entity, ident, *args, **kwargs)

        monkeypatch.setattr(Session, "get", get)

        with pytest.raises(RuntimeError, match="queue_dependency_missing"):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _unused_provider_builder)
            )  # noqa: SLF001

    def test_dispatch_claimed_raises_when_job_has_no_printer(
        self, db_session: Session
    ) -> None:
        artifact = a_gcode_artifact(db_session, "Queue cube")
        job = build_print_job(
            db_session,
            artifact,
            printer_id=None,
            remote_filename="x.gcode",
            state=PrintJobState.UPLOADING,
        )

        with pytest.raises(RuntimeError, match="queue_job_not_found"):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _unused_provider_builder)
            )  # noqa: SLF001

    def test_dispatch_claimed_raises_when_provider_cannot_upload_or_start(
        self,
        db_session: Session,
    ) -> None:
        _printer, job = _seeded_upload_job(db_session)
        provider = AsyncMock()
        provider.capabilities = ProviderCapabilities(
            supported=frozenset()
        )  # no START/UPLOAD

        with pytest.raises(ProviderError, match="operation_not_supported_for_provider"):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _provider_builder(provider))
            )  # noqa: SLF001

    def test_dispatch_claimed_raises_printer_not_ready_when_requires_ready_before_send(
        self,
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

        with pytest.raises(ProviderError, match="printer_not_ready"):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _provider_builder(provider))
            )  # noqa: SLF001

    def test_dispatch_claimed_proceeds_when_ready_before_send_reports_idle(
        self, db_session: Session, tmp_path: Path
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
            patch("app.services.printer_jobs.get_backend", return_value=_Backend()),
        ):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _provider_builder(provider))
            )  # noqa: SLF001

        provider.upload.assert_awaited_once()
        provider.start.assert_awaited_once()


class TestRunFleetScheduler:
    def test_run_fleet_scheduler_dispatches_then_waits_on_task_queue(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int | None] = []

        async def fake_dispatch_next(_provider_builder) -> int | None:
            # First tick "dispatches" (truthy), every tick after reports nothing queued.
            result = 42 if not calls else None
            calls.append(result)
            return result

        class _NeverReadyQueue:
            async def dequeue(self):  # noqa: ANN201 - never resolves before the 2s timeout
                await asyncio.sleep(10)

        monkeypatch.setattr(printer_jobs, "dispatch_next", fake_dispatch_next)
        printer_jobs.scheduler_status.running = False
        printer_jobs.scheduler_status.last_dispatch_at = None

        async def _run() -> None:
            task = asyncio.create_task(
                printer_jobs.run_fleet_scheduler(
                    _NeverReadyQueue(), _unused_provider_builder
                )
            )
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
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def failing_dispatch_next(_provider_builder) -> int | None:
            raise RuntimeError("simulated tick failure")

        class _NeverReadyQueue:
            async def dequeue(self):  # noqa: ANN201
                await asyncio.sleep(10)

        monkeypatch.setattr(printer_jobs, "dispatch_next", failing_dispatch_next)
        printer_jobs.scheduler_status.running = False
        printer_jobs.scheduler_status.last_error = None

        async def _run() -> None:
            task = asyncio.create_task(
                printer_jobs.run_fleet_scheduler(
                    _NeverReadyQueue(), _unused_provider_builder
                )
            )
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


class TestPrinter:
    def test_dispatch_rechecks_printer_grant_after_enqueue(
        self, db_session: Session
    ) -> None:
        printer = printer_config(
            "Revoked", moonraker_url="http://revoked", status=PrinterStatus.READY
        )
        user = user_config("revoked-user")
        db_session.add_all([printer, user])
        db_session.commit()
        db_session.refresh(printer)
        db_session.refresh(user)
        artifact = a_gcode_artifact(db_session, "revoked-cube")
        permission = PrinterPermission(
            printer_id=printer.id, user_id=user.id, role=PrinterRole.PRINT
        )
        db_session.add(permission)
        db_session.commit()
        job = build_print_job(
            db_session,
            artifact,
            printer_id=printer.id,
            remote_filename="revoked.gcode",
            state=PrintJobState.QUEUED,
            routing_strategy=RoutingStrategy.MANUAL,
            requested_by=user.id,
        )
        db_session.delete(permission)
        db_session.commit()

        assert asyncio.run(printer_jobs.dispatch_next(_unused_provider_builder)) is None
        db_session.refresh(job)
        assert job.state == PrintJobState.QUEUED
        assert job.blocked_reason == "printer_access_revoked"
