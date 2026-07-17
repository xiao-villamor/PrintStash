from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import update
from sqlmodel import select

from app.core.logging import get_logger
from app.core.metrics import record_fleet_dispatch
from app.core.time import utcnow
from app.db.models import File, Printer, PrintJob, PrintJobState
from app.db.session import get_session_factory
from app.services import fleet
from app.services.printer_files import upsert_printer_file
from app.services.printer_provider import ProviderError, get_provider_client
from app.services.storage_backend import get_backend
from app.services.task_queue import task_queue

logger = get_logger(__name__)


@dataclass
class FleetSchedulerStatus:
    running: bool = False
    last_tick_at: datetime | None = None
    last_dispatch_at: datetime | None = None
    last_error: str | None = None


scheduler_status = FleetSchedulerStatus()


def scheduler_snapshot() -> dict[str, object]:
    return {
        "running": scheduler_status.running,
        "last_tick_at": scheduler_status.last_tick_at,
        "last_dispatch_at": scheduler_status.last_dispatch_at,
        "last_error": scheduler_status.last_error,
    }


class PrinterJobError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DispatchOutcomeUnknownError(PrinterJobError):
    """Provider I/O began, so retrying could duplicate a physical print."""

    def __init__(self) -> None:
        super().__init__("dispatch_outcome_unknown")


async def transfer_artifact(
    backend,
    provider,
    artifact: File,
    remote_filename: str,
    *,
    start_print: bool,
    mark_outcome_unknown: bool = False,
) -> None:
    """Single storage-to-provider transfer seam for immediate and queued sends."""
    if not await asyncio.to_thread(backend.exists, artifact.path):
        raise PrinterJobError("file_blob_missing")
    temp = tempfile.NamedTemporaryFile(
        prefix=f"print-{artifact.id}-",
        suffix=Path(artifact.original_filename).suffix or ".gcode",
        delete=False,
    )
    target = Path(temp.name)
    temp.close()
    try:
        try:
            local = await asyncio.to_thread(
                backend.download_to_path, artifact.path, target
            )
        except Exception as exc:
            raise PrinterJobError("storage_error") from exc
        try:
            await provider.upload(local, remote_filename)
            if start_print:
                await provider.start(remote_filename)
        except Exception as exc:
            # Upload and start are non-transactional remote operations. A
            # transport error can arrive after the printer accepted either
            # request, therefore automatic replay is unsafe.
            if mark_outcome_unknown:
                raise DispatchOutcomeUnknownError() from exc
            raise
    finally:
        target.unlink(missing_ok=True)


def reconcile_stranded_dispatches() -> int:
    """Fail fleet claims interrupted before provider outcome was known."""
    with get_session_factory().scoped_session() as session:
        rows = list(
            session.exec(
                select(PrintJob).where(
                    PrintJob.state == PrintJobState.UPLOADING,
                    PrintJob.dispatch_claimed_at.is_not(None),  # type: ignore[union-attr]
                )
            ).all()
        )
        now = utcnow()
        for row in rows:
            row.state = PrintJobState.FAILED
            # Upload/start is not transactional with our database. The printer
            # may already be printing even though the final DB write was lost,
            # so automatic retry could start the same job twice. Keep the row
            # visible for operator reconciliation, but never offer one-click
            # retry for an unknown provider outcome.
            row.error = "dispatch_outcome_unknown"
            row.retryable = False
            row.finished_at = now
            row.updated_at = now
            session.add(row)
        if rows:
            session.commit()
        return len(rows)


async def dispatch_next() -> int | None:
    """Atomically claim and dispatch oldest eligible assigned fleet job."""
    claimed_at = utcnow()
    with get_session_factory().scoped_session() as session:
        candidates = session.exec(
            select(PrintJob)
            .where(
                PrintJob.state == PrintJobState.QUEUED,
                PrintJob.dispatch_claimed_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(PrintJob.queue_position, PrintJob.created_at, PrintJob.id)
        ).all()
        candidate: PrintJob | None = None
        for row in candidates:
            requested_printer_id = (
                row.printer_id if row.routing_strategy.value == "manual" else None
            )
            printer, blocked_reason = fleet.choose_printer(
                session, row.routing_strategy, requested_printer_id
            )
            assigned_id = printer.id if printer else None
            if row.printer_id != assigned_id or row.blocked_reason != blocked_reason:
                row.printer_id = assigned_id
                row.printer_name = printer.name if printer else None
                row.blocked_reason = blocked_reason
                row.updated_at = utcnow()
                session.add(row)
            if blocked_reason is None and printer is not None:
                candidate = row
                break
        session.commit()
        if candidate is None or candidate.id is None:
            return None
        result = session.exec(
            update(PrintJob)
            .where(
                PrintJob.id == candidate.id,
                PrintJob.state == PrintJobState.QUEUED,
                PrintJob.dispatch_claimed_at.is_(None),  # type: ignore[union-attr]
            )
            .values(
                state=PrintJobState.UPLOADING,
                dispatch_claimed_at=claimed_at,
                dispatch_attempts=PrintJob.dispatch_attempts + 1,
                updated_at=claimed_at,
            )
        )
        session.commit()
        if result.rowcount != 1:  # type: ignore[attr-defined]
            return None
        job_id = candidate.id

    try:
        await _dispatch_claimed(job_id)
        record_fleet_dispatch("started")
    except Exception as exc:  # noqa: BLE001 - terminal state must always persist
        code = (
            exc.code
            if isinstance(exc, (ProviderError, PrinterJobError))
            else "provider_error"
        )
        logger.warning("fleet dispatch failed job=%s code=%s", job_id, code)
        record_fleet_dispatch("failed")
        with get_session_factory().scoped_session() as session:
            job = session.get(PrintJob, job_id)
            if job is not None:
                job.state = PrintJobState.FAILED
                job.error = code
                job.retryable = code != "dispatch_outcome_unknown"
                job.finished_at = utcnow()
                job.updated_at = utcnow()
                session.add(job)
                session.commit()
    return job_id


async def _dispatch_claimed(job_id: int) -> None:
    with get_session_factory().scoped_session() as session:
        job = session.get(PrintJob, job_id)
        if job is None or job.printer_id is None:
            raise RuntimeError("queue_job_not_found")
        printer = session.get(Printer, job.printer_id)
        artifact = session.get(File, job.file_id)
        if printer is None or artifact is None:
            raise RuntimeError("queue_dependency_missing")
        printer_id = printer.id
        remote_filename = job.remote_filename
        artifact_id = artifact.id
        artifact_size = artifact.size_bytes
        artifact_sha = artifact.sha256

    provider = get_provider_client(printer)
    if not provider.capabilities.can_upload or not provider.capabilities.can_start:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )
    if provider.capabilities.requires_ready_before_send:
        status = await provider.query_status()
        state = str(
            status.get("result", {})
            .get("status", {})
            .get("print_stats", {})
            .get("state", "")
        ).lower()
        if state not in {"standby", "ready", "idle", "complete", "cancelled"}:
            raise ProviderError("printer_not_ready", code="printer_not_ready")

    await transfer_artifact(
        get_backend(),
        provider,
        artifact,
        remote_filename,
        start_print=True,
        mark_outcome_unknown=True,
    )

    with get_session_factory().scoped_session() as session:
        job = session.get(PrintJob, job_id)
        if job is None:
            return
        job.state = PrintJobState.STARTED
        job.started_at = utcnow()
        job.error = None
        job.retryable = False
        job.blocked_reason = None
        job.updated_at = utcnow()
        session.add(job)
        session.commit()
        upsert_printer_file(
            session,
            printer_id=printer_id,  # type: ignore[arg-type]
            file_id=artifact_id,  # type: ignore[arg-type]
            remote_filename=remote_filename,
            size_bytes=artifact_size,
            sha256=artifact_sha,
            matched_by="upload_history",
        )


async def run_fleet_scheduler() -> None:
    scheduler_status.running = True
    try:
        while True:
            scheduler_status.last_tick_at = utcnow()
            try:
                dispatched = await dispatch_next()
                scheduler_status.last_error = None
                if dispatched is not None:
                    scheduler_status.last_dispatch_at = utcnow()
            except Exception as exc:  # noqa: BLE001 - survive one bad tick
                logger.exception("fleet scheduler tick failed")
                scheduler_status.last_error = exc.__class__.__name__
                dispatched = None
            if dispatched is not None:
                await asyncio.sleep(0)
                continue
            # Database remains source of truth; TaskQueue is a low-latency wake
            # transport. Timeout polling recovers queued work after restarts or
            # a lost in-memory notification without external dependencies.
            try:
                await asyncio.wait_for(task_queue.dequeue(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
    finally:
        scheduler_status.running = False
