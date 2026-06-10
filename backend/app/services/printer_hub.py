"""Background worker: maintain one WS subscription per configured printer.

For each Printer row, we keep:
- a live snapshot of its Moonraker `printer.objects` (in memory)
- a writeback to DB columns (status, last_seen_at, last_error)
- a fan-out to any vault WebSocket clients subscribed to that printer

The hub is intentionally simple — Stage 4 will likely replace it with Redis pub/sub.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Set

from fastapi import Request, WebSocket
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import PrintJob, PrintJobState, Printer, PrinterStatus
from app.db.session import get_session_factory
from app.services.printer_provider import ProviderError, get_provider_client
from app.db.scopes import live

logger = get_logger(__name__)


# Map Moonraker `print_stats.state` -> coarse vault PrinterStatus.
#
# Note: `complete` and `cancelled` collapse to READY because they describe
# the *job* outcome, not the *printer* state — the machine is idle and ready
# for the next job. The finer-grained per-job lifecycle (COMPLETED/CANCELLED)
# is tracked separately on the PrintJob row in `_sync_active_job`.
_STATE_MAP: Dict[str, PrinterStatus] = {
    "standby": PrinterStatus.READY,
    "ready": PrinterStatus.READY,
    "printing": PrinterStatus.PRINTING,
    "paused": PrinterStatus.PAUSED,
    "complete": PrinterStatus.READY,
    "cancelled": PrinterStatus.READY,
    "error": PrinterStatus.ERROR,
    "shutdown": PrinterStatus.OFFLINE,
    "running": PrinterStatus.PRINTING,
    "idle": PrinterStatus.READY,
    "prepare": PrinterStatus.READY,
    "failed": PrinterStatus.ERROR,
}

_WEBHOOK_STATE_MAP: Dict[str, PrinterStatus] = {
    "ready": PrinterStatus.READY,
    "shutdown": PrinterStatus.OFFLINE,
    "error": PrinterStatus.ERROR,
}


def _derive_printer_status(snapshot: Dict[str, Any]) -> tuple[str, PrinterStatus]:
    """Derive coarse printer status from snapshot data.

    Prefer `print_stats.state` because it reflects active print lifecycle.
    Fall back to `webhooks.state` for idle/ready/offline/error states when
    Moonraker does not populate print_stats.
    """
    print_state = str(snapshot.get("print_stats", {}).get("state") or "").lower()
    if print_state:
        return print_state, _STATE_MAP.get(print_state, PrinterStatus.UNKNOWN)

    webhook_state = str(snapshot.get("webhooks", {}).get("state") or "").lower()
    if webhook_state:
        return webhook_state, _WEBHOOK_STATE_MAP.get(
            webhook_state, PrinterStatus.UNKNOWN
        )

    return "", PrinterStatus.UNKNOWN


class PrinterHub:
    def __init__(self) -> None:
        self.snapshots: Dict[int, Dict[str, Any]] = {}
        self.subscribers: Dict[int, Set[WebSocket]] = {}
        self.tasks: Dict[int, asyncio.Task] = {}
        self.stop_events: Dict[int, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    # -- WS subscriber registry --

    async def attach(self, printer_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self.subscribers.setdefault(printer_id, set()).add(ws)
        snap = self.snapshots.get(printer_id)
        if snap is not None:
            try:
                await ws.send_json(
                    {"type": "snapshot", "printer_id": printer_id, "data": snap}
                )
            except Exception:  # noqa: BLE001 — best-effort initial send; drop on failure
                pass

    async def detach(self, printer_id: int, ws: WebSocket) -> None:
        async with self._lock:
            subs = self.subscribers.get(printer_id)
            if subs and ws in subs:
                subs.remove(ws)

    async def _broadcast(self, printer_id: int, payload: Dict[str, Any]) -> None:
        async with self._lock:
            subs = list(self.subscribers.get(printer_id, ()))
        dead: list[WebSocket] = []
        for ws in subs:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.subscribers.get(printer_id, set()).discard(ws)

    # -- printer lifecycle --

    async def add_printer(self, printer_id: int) -> None:
        async with self._lock:
            if printer_id in self.tasks:
                return
            stop = asyncio.Event()
            self.stop_events[printer_id] = stop
            task = asyncio.create_task(
                self._run_printer(printer_id, stop), name=f"printer-{printer_id}"
            )
            self.tasks[printer_id] = task

    async def remove_printer(self, printer_id: int) -> None:
        async with self._lock:
            stop = self.stop_events.pop(printer_id, None)
            task = self.tasks.pop(printer_id, None)
            self.snapshots.pop(printer_id, None)
        if stop:
            stop.set()
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("printer hub: worker exit error for %s", printer_id)

    async def restart_printer(self, printer_id: int) -> None:
        await self.remove_printer(printer_id)
        await self.add_printer(printer_id)

    async def start_all(self) -> None:
        with get_session_factory().session() as session:
            ids = [
                p.id
                for p in session.exec(
                    select(Printer).where(live(Printer))  # type: ignore[union-attr]
                ).all()
                if p.id
            ]
        for pid in ids:
            await self.add_printer(pid)

    async def stop_all(self) -> None:
        async with self._lock:
            ids = list(self.tasks.keys())
        for pid in ids:
            await self.remove_printer(pid)

    # -- worker --

    async def _run_printer(self, printer_id: int, stop: asyncio.Event) -> None:
        # Load the printer row (re-load on each reconnect to pick up edits).
        while not stop.is_set():
            with get_session_factory().session() as session:
                printer = session.get(Printer, printer_id)
                if printer is None:
                    logger.info("printer worker[%s] gone; exiting", printer_id)
                    return
                try:
                    client = get_provider_client(printer)
                except ProviderError as exc:
                    self._mark_status(
                        printer_id,
                        PrinterStatus.ERROR,
                        error=f"{exc.code}: {exc.detail}",
                    )
                    await asyncio.sleep(5.0)
                    continue

            async def on_status(status: Dict[str, Any]) -> None:
                await self._handle_status(printer_id, status)

            # Bootstrap with a one-shot status query so we can:
            # 1) seed current state quickly on startup/reconfigure
            # 2) mark clear offline/error if transport/auth is broken
            try:
                initial = await client.query_status()
                initial_status = initial.get("result", {}).get("status", {})
                if isinstance(initial_status, dict) and initial_status:
                    await self._handle_status(printer_id, initial_status)
            except Exception as exc:  # noqa: BLE001 - provider-specific failures
                self._mark_status(printer_id, PrinterStatus.OFFLINE, error=str(exc))
                logger.warning(
                    "printer worker[%s] initial status query failed: %s",
                    printer_id,
                    exc,
                )
                await asyncio.sleep(5.0)
                continue

            try:
                await client.subscribe_status(on_status, stop_event=stop)
            except Exception as exc:  # noqa: BLE001 — last-ditch
                logger.exception(
                    "printer worker[%s] subscribe crash: %s", printer_id, exc
                )
                self._mark_status(printer_id, PrinterStatus.OFFLINE, error=str(exc))
                # back-off before reloading config
                try:
                    await asyncio.wait_for(stop.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass

    async def _handle_status(self, printer_id: int, status: Dict[str, Any]) -> None:
        # Merge into in-memory snapshot.
        snap = self.snapshots.setdefault(printer_id, {})
        for obj_name, fields in status.items():
            if not isinstance(fields, dict):
                continue
            existing = snap.setdefault(obj_name, {})
            existing.update(fields)

        # Compute coarse PrinterStatus + filename for DB writeback.
        print_stats = snap.get("print_stats", {})
        ms_state, vault_status = _derive_printer_status(snap)
        progress = float(snap.get("virtual_sdcard", {}).get("progress") or 0.0)
        filename = print_stats.get("filename") or None

        self._mark_status(printer_id, vault_status, error=None)
        await self._sync_active_job(
            printer_id, ms_state, filename, progress, print_stats
        )

        await self._broadcast(
            printer_id,
            {"type": "update", "printer_id": printer_id, "data": snap},
        )

    def _mark_status(
        self, printer_id: int, status: PrinterStatus, *, error: str | None
    ) -> None:
        try:
            with get_session_factory().session() as session:
                p = session.get(Printer, printer_id)
                if p is None:
                    return
                p.status = status
                p.last_seen_at = utcnow()
                p.last_error = error
                p.updated_at = utcnow()
                session.add(p)
                session.commit()
        except Exception:
            logger.exception("printer hub: failed to mark status for %s", printer_id)

    async def _sync_active_job(
        self,
        printer_id: int,
        ms_state: str,
        filename: str | None,
        progress: float,
        print_stats: Dict[str, Any],
    ) -> None:
        """Reflect Moonraker state onto the most-recent matching PrintJob row.

        If no matching PrintJob exists and the printer is actively printing
        or paused, a placeholder row with source="external" is created so
        externally-initiated jobs are captured in the vault history.
        """
        if not filename:
            return
        try:
            with get_session_factory().session() as session:
                job = session.exec(
                    select(PrintJob)
                    .where(
                        PrintJob.printer_id == printer_id,
                        PrintJob.remote_filename == filename,
                    )
                    .order_by(PrintJob.created_at.desc())  # type: ignore[attr-defined]
                ).first()

                if job is None:
                    # No vault-created job — check if printer is actively printing.
                    if ms_state in (
                        "printing",
                        "paused",
                        "complete",
                        "cancelled",
                        "error",
                    ):
                        sentinel_file_id, sentinel_model_id = _get_sentinel_ids(session)
                        job = PrintJob(
                            printer_id=printer_id,
                            file_id=sentinel_file_id,
                            model_id=sentinel_model_id,
                            remote_filename=filename,
                            source="external",
                        )
                        session.add(job)
                        session.commit()
                        session.refresh(job)
                        logger.info(
                            "captured external print job %s on printer %s (state=%s)",
                            filename,
                            printer_id,
                            ms_state,
                        )
                    else:
                        return

                new_state: PrintJobState
                if ms_state == "printing":
                    new_state = PrintJobState.PRINTING
                elif ms_state == "paused":
                    new_state = PrintJobState.PAUSED
                elif ms_state == "complete":
                    new_state = PrintJobState.COMPLETED
                elif ms_state == "cancelled":
                    new_state = PrintJobState.CANCELLED
                elif ms_state == "error":
                    new_state = PrintJobState.FAILED
                elif ms_state == "failed":
                    new_state = PrintJobState.FAILED
                else:
                    new_state = job.state

                changed = False
                if new_state != job.state:
                    job.state = new_state
                    changed = True
                if abs(progress - job.progress) > 1e-3:
                    job.progress = progress
                    changed = True
                if new_state == PrintJobState.PRINTING and job.started_at is None:
                    job.started_at = utcnow()
                    changed = True
                if (
                    new_state
                    in (
                        PrintJobState.COMPLETED,
                        PrintJobState.CANCELLED,
                        PrintJobState.FAILED,
                    )
                    and job.finished_at is None
                ):
                    job.finished_at = utcnow()
                    changed = True
                if changed:
                    job.updated_at = utcnow()
                    if new_state == PrintJobState.FAILED:
                        job.error = print_stats.get("message")
                    session.add(job)
                    session.commit()
        except Exception:
            logger.exception("printer hub: job sync failed for printer %s", printer_id)


def get_hub(request: Request) -> PrinterHub:
    """FastAPI dependency: returns the PrinterHub stored on app.state."""
    return request.app.state.printer_hub


def get_hub_from_ws(websocket: WebSocket) -> PrinterHub:
    """FastAPI dependency (WebSocket variant): returns the PrinterHub."""
    return websocket.app.state.printer_hub


def _get_sentinel_ids(session: Session) -> tuple[int, int]:
    """Return (file_id, model_id) of lazily-created external job sentinel rows."""
    from app.db.models import (
        File,
        FileType,
        Model,
        SENTINEL_FILE_HASH,
        SENTINEL_MODEL_HASH,
    )

    model = session.exec(select(Model).where(Model.hash == SENTINEL_MODEL_HASH)).first()
    if model is None:
        model = Model(
            name="__external__",
            slug="__external__",
            hash=SENTINEL_MODEL_HASH,
        )
        session.add(model)
        session.commit()
        session.refresh(model)

    f = session.exec(select(File).where(File.sha256 == SENTINEL_FILE_HASH)).first()
    if f is None:
        f = File(
            model_id=model.id,
            path="/dev/null",
            original_filename="__external__",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=0,
            sha256=SENTINEL_FILE_HASH,
        )
        session.add(f)
        session.commit()
        session.refresh(f)

    return int(f.id), int(model.id)
