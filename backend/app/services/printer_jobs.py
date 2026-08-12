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
from app.db.models import (
    CollectionRole,
    File,
    Model,
    Printer,
    PrinterRole,
    PrintJob,
    PrintJobState,
    User,
)
from app.db.session import get_session_factory
from app.services import fleet, printer_rbac, rbac
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
_CANDIDATE_BATCH_SIZE = 100


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


def _claim_next_sync() -> int | None:
    """Resolve and claim one job using a bounded, fixed-query scheduler batch."""
    claimed_at = utcnow()
    with get_session_factory().scoped_session() as session:
        candidates = list(
            session.exec(
                select(PrintJob)
                .where(
                    PrintJob.state == PrintJobState.QUEUED,
                    PrintJob.dispatch_claimed_at.is_(None),  # type: ignore[union-attr]
                )
                .order_by(
                    PrintJob.blocked_reason.is_not(None),  # type: ignore[union-attr]
                    PrintJob.queue_position,
                    PrintJob.created_at,
                    PrintJob.id,
                )
                .limit(_CANDIDATE_BATCH_SIZE)
            ).all()
        )
        if not candidates:
            return None

        routing = fleet.build_routing_snapshot(session)
        requester_ids = {
            int(row.requested_by) for row in candidates if row.requested_by is not None
        }
        file_ids = {int(row.file_id) for row in candidates}
        model_ids = {int(row.model_id) for row in candidates}
        users = {
            int(row.id): row
            for row in session.exec(
                select(User).where(User.id.in_(requester_ids))  # type: ignore[union-attr]
            ).all()
            if row.id is not None
        }
        artifacts = {
            int(row.id): row
            for row in session.exec(
                select(File).where(File.id.in_(file_ids))  # type: ignore[union-attr]
            ).all()
            if row.id is not None
        }
        models = {
            int(row.id): row
            for row in session.exec(
                select(Model).where(Model.id.in_(model_ids))  # type: ignore[union-attr]
            ).all()
            if row.id is not None
        }

        resolved: list[tuple[PrintJob, Printer | None, str | None]] = []
        selected_printer_ids: set[int] = set()
        for row in candidates:
            requested_printer_id = (
                row.printer_id if row.routing_strategy.value == "manual" else None
            )
            try:
                printer, blocked_reason = fleet.choose_printer(
                    session,
                    row.routing_strategy,
                    requested_printer_id,
                    snapshot=routing,
                )
            except fleet.FleetError as exc:
                printer, blocked_reason = None, exc.code
            if printer is not None and printer.id is not None:
                selected_printer_ids.add(int(printer.id))
            resolved.append((row, printer, blocked_reason))

        printer_roles = printer_rbac.effective_roles_for_user_printer_pairs(
            session,
            requester_ids,
            selected_printer_ids,
        )
        collection_ids = {
            int(model.collection_id)
            for model in models.values()
            if model.collection_id is not None
        }
        collection_roles = rbac.effective_roles_for_user_collection_pairs(
            session,
            requester_ids,
            collection_ids,
        )

        candidate: PrintJob | None = None
        pending_updates: list[dict[str, object]] = []
        for row, printer, blocked_reason in resolved:
            requester = users.get(int(row.requested_by)) if row.requested_by else None
            artifact = artifacts.get(int(row.file_id))
            model = models.get(int(row.model_id))
            if row.requested_by is not None and (
                requester is None
                or not requester.is_active
                or requester.deleted_at is not None
            ):
                blocked_reason = "requester_access_revoked"
            elif (
                requester is not None
                and printer is not None
                and not printer_rbac.role_allows(
                    PrinterRole.ADMIN
                    if requester.is_superuser
                    else printer_roles.get((int(requester.id), int(printer.id))),
                    PrinterRole.PRINT,
                )
            ):
                blocked_reason = "printer_access_revoked"
            elif requester is not None and (
                artifact is None
                or model is None
                or not rbac.role_allows(
                    CollectionRole.ADMIN
                    if requester.is_superuser
                    else collection_roles.get(
                        (int(requester.id), int(model.collection_id))
                    )
                    if model.collection_id is not None
                    else None,
                    CollectionRole.EDIT,
                )
            ):
                blocked_reason = "collection_access_revoked"
            assigned_id = printer.id if printer else None
            if row.printer_id != assigned_id or row.blocked_reason != blocked_reason:
                pending_updates.append(
                    {
                        "id": row.id,
                        "printer_id": assigned_id,
                        "printer_name": printer.name if printer else None,
                        "blocked_reason": blocked_reason,
                        "updated_at": claimed_at,
                    }
                )
            if blocked_reason is None and printer is not None:
                candidate = row
                break
        if pending_updates:
            # ORM dirty flushing can degrade to one UPDATE per row depending on
            # dialect/session state. Primary-key bulk mappings preserve a fixed
            # round-trip budget for the bounded scheduler batch.
            session.execute(update(PrintJob), pending_updates)
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
        return int(candidate.id)


async def dispatch_next() -> int | None:
    """Atomically claim and dispatch oldest eligible assigned fleet job."""
    job_id = await asyncio.to_thread(_claim_next_sync)
    if job_id is None:
        return None

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
        await asyncio.to_thread(_mark_dispatch_failed, job_id, code)
    return job_id


@dataclass(frozen=True)
class DispatchContext:
    printer: Printer
    artifact: File
    remote_filename: str


def _load_dispatch_context(job_id: int) -> DispatchContext:
    with get_session_factory().scoped_session() as session:
        job = session.get(PrintJob, job_id)
        if job is None or job.printer_id is None:
            raise RuntimeError("queue_job_not_found")
        printer = session.get(Printer, job.printer_id)
        artifact = session.get(File, job.file_id)
        if printer is None or artifact is None:
            raise RuntimeError("queue_dependency_missing")
        return DispatchContext(
            printer=printer,
            artifact=artifact,
            remote_filename=job.remote_filename,
        )


def _mark_dispatch_failed(job_id: int, code: str) -> None:
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


def _mark_dispatch_started(job_id: int, context: DispatchContext) -> None:
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
            printer_id=int(context.printer.id),
            file_id=int(context.artifact.id),
            remote_filename=context.remote_filename,
            size_bytes=context.artifact.size_bytes,
            sha256=context.artifact.sha256,
            matched_by="upload_history",
        )


async def _dispatch_claimed(job_id: int) -> None:
    context = await asyncio.to_thread(_load_dispatch_context, job_id)
    printer = context.printer
    artifact = context.artifact
    remote_filename = context.remote_filename

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

    await asyncio.to_thread(_mark_dispatch_started, job_id, context)


async def run_fleet_scheduler() -> None:
    from app.services.backup import begin_mutating_operation, end_mutating_operation

    scheduler_status.running = True
    try:
        while True:
            if not begin_mutating_operation():
                await asyncio.sleep(0.5)
                continue
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
            finally:
                end_mutating_operation()
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
