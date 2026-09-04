"""Background worker: maintain one WS subscription per configured printer.

For each Printer row, we keep:
- a live snapshot of its Moonraker `printer.objects` (in memory)
- a writeback to DB columns (status, last_seen_at, last_error)
- a fan-out to any vault WebSocket clients subscribed to that printer

The hub is intentionally simple — Stage 4 will likely replace it with Redis pub/sub.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import unquote, urlparse

from fastapi import Request, WebSocket
from printstash_core.printers import ArtifactCaptureClient
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    FileType,
    MaterialSlotState,
    MaterialSource,
    NotificationEventType,
    OperatorGateState,
    Printer,
    PrinterMaterialSlot,
    PrinterProvider,
    PrinterStatus,
    PrinterTool,
    PrintJob,
    PrintJobState,
)
from app.db.scopes import live
from app.db.session import SessionFactory, get_session_factory
from app.services import filament as filament_svc
from app.services import (
    gcode_parser,
    ingestion,
    notifications,
    print_results,
    runtime_config,
    thumbnail,
)
from app.services.backup import begin_mutating_operation, end_mutating_operation
from app.services.hashing import sha256_file
from app.services.printer_provider import (
    PrinterProviderClient,
    ProviderError,
)
from app.services.realtime import InProcessBus, RealtimeBus
from app.services.runtime_config import auto_mark_known_good_enabled
from app.services.spoolman import SpoolmanClient, SpoolmanError

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

# Terminal PrintJob states that emit a notification, mapped to their event.
# CANCELLED is split from FAILED so self-cancellations can be muted separately.
_TERMINAL_EVENT: Dict[PrintJobState, NotificationEventType] = {
    PrintJobState.COMPLETED: NotificationEventType.PRINT_COMPLETED,
    PrintJobState.FAILED: NotificationEventType.PRINT_FAILED,
    PrintJobState.CANCELLED: NotificationEventType.PRINT_CANCELLED,
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


# Re-write an unchanged printer status at most this often; keeps last_seen_at
# reasonably fresh without a DB commit per Moonraker status tick.
_STATUS_WRITE_INTERVAL_S = 30.0
_JOB_PROGRESS_WRITE_INTERVAL_S = 5.0
_JOB_SYNC_BREAKER_THRESHOLD = 3
_JOB_SYNC_BREAKER_MAX_DELAY_S = 300.0


def _reported_text(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None


def _reported_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _reported_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


_BAMBU_ID_FIELDS = (
    "external_task_id",
    "external_subtask_id",
    "external_project_id",
)


def _bambu_identity(values: Dict[str, Any] | PrintJob) -> dict[str, str]:
    """Return non-empty Bambu identity fields without erasing their types."""

    identity: dict[str, str] = {}
    for field in _BAMBU_ID_FIELDS:
        value = (
            values.get(field)
            if isinstance(values, dict)
            else getattr(values, field, None)
        )
        text = _reported_text(value)
        if text not in (None, "0"):
            identity[field] = text
    return identity


def _bambu_identity_matches(
    incoming: dict[str, str], candidate: dict[str, str]
) -> bool:
    """Match only same-typed identities and reject conflicting fields."""

    shared_fields = incoming.keys() & candidate.keys()
    return bool(shared_fields) and all(
        incoming[field] == candidate[field] for field in shared_fields
    )


def _bambu_project_task_transition(
    incoming: dict[str, str],
    candidate: PrintJob,
    filename: str,
    ms_state: str,
) -> bool:
    """Allow only an active project-only to task-only filename hand-off."""

    candidate_identity = _bambu_identity(candidate)
    if (
        candidate.source != "external"
        or candidate.remote_filename != filename
        or candidate.finished_at is not None
        or candidate.started_at is None
        or candidate.state not in (PrintJobState.PRINTING, PrintJobState.PAUSED)
        or ms_state not in ("printing", "paused")
        or set(candidate_identity) != {"external_project_id"}
        or set(incoming) != {"external_task_id"}
    ):
        return False
    # Equal serialized values across different typed fields are not evidence
    # of continuity; they must not bypass the typed matcher.
    return (
        candidate_identity["external_project_id"]
        != incoming["external_task_id"]
    )


class PrinterHub:
    def __init__(
        self,
        bus: RealtimeBus | None = None,
        *,
        session_factory: SessionFactory | None = None,
        provider_builder: Callable[[Printer], PrinterProviderClient] | None = None,
    ) -> None:
        self.snapshots: Dict[int, Dict[str, Any]] = {}
        # Runtime composition always supplies both adapters.  The defaults
        # retain direct construction for extensions and focused tests; make
        # them required once those callers use the composition root too.
        self.bus: RealtimeBus = bus if bus is not None else InProcessBus()
        self._session_factory = (
            session_factory if session_factory is not None else get_session_factory()
        )
        self._provider_builder = provider_builder
        self.tasks: Dict[int, asyncio.Task] = {}
        self.stop_events: Dict[int, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        # printer_id -> (status, error, monotonic time of last DB write)
        self._last_status_write: Dict[int, tuple[PrinterStatus, str | None, float]] = {}
        # printer_id -> (remote_filename, PrintJob.id) of the job currently
        # tracked for that printer, so each status tick (several/sec) can skip
        # the PrintJob lookup query and go straight to a PK get(). Falls back
        # to the query whenever the cache misses or the cached row is stale.
        self._active_job_cache: Dict[int, tuple[str, int]] = {}
        # printer_id -> (consecutive failures, retry-after monotonic timestamp)
        self._job_sync_breakers: Dict[int, tuple[int, float]] = {}
        # Status callbacks may arrive concurrently from MQTT/HTTP worker
        # threads. Serialize the DB reconciliation seam per printer so two
        # initial callbacks cannot both create external placeholder rows.
        self._job_sync_db_locks: Dict[int, threading.Lock] = {}
        # printer_id -> (filename, state, progress, monotonic time) for DB write coalescing
        self._last_job_sync_write: Dict[int, tuple[str, str, float, float]] = {}
        self._capture_tasks: Dict[tuple[int, int], asyncio.Task] = {}

    @staticmethod
    def _channel(printer_id: int) -> str:
        return f"printer:{printer_id}"

    # -- WS subscriber registry --

    async def attach(self, printer_id: int, ws: WebSocket) -> None:
        await self.bus.subscribe(self._channel(printer_id), ws)
        snap = self.snapshots.get(printer_id)
        if snap is not None:
            try:
                await ws.send_json(
                    {"type": "snapshot", "printer_id": printer_id, "data": snap}
                )
            except Exception:  # noqa: BLE001 — best-effort initial send; drop on failure
                pass

    async def detach(self, printer_id: int, ws: WebSocket) -> None:
        await self.bus.unsubscribe(self._channel(printer_id), ws)

    async def _broadcast(self, printer_id: int, payload: Dict[str, Any]) -> None:
        await self.bus.publish(self._channel(printer_id), payload)

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
            self._last_status_write.pop(printer_id, None)
            self._job_sync_breakers.pop(printer_id, None)
            self._last_job_sync_write.pop(printer_id, None)
            capture_tasks = [
                self._capture_tasks.pop(key)
                for key in list(self._capture_tasks)
                if key[0] == printer_id
            ]
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
        for capture_task in capture_tasks:
            capture_task.cancel()
        if capture_tasks:
            await asyncio.gather(*capture_tasks, return_exceptions=True)

    async def restart_printer(self, printer_id: int) -> None:
        await self.remove_printer(printer_id)
        await self.add_printer(printer_id)

    async def start_all(self) -> None:
        with self._session_factory.session() as session:
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
        reconnect_delay = 1.0
        while not stop.is_set():
            with self._session_factory.session() as session:
                printer = session.get(Printer, printer_id)
                if printer is None:
                    logger.info("printer worker[%s] gone; exiting", printer_id)
                    return
                try:
                    if self._provider_builder is None:
                        raise RuntimeError(
                            "PrinterHub requires a provider builder from the composition root"
                        )
                    client = self._provider_builder(printer)
                except ProviderError as exc:
                    await self._mark_status(
                        printer_id,
                        PrinterStatus.ERROR,
                        error=f"{exc.code}: {exc.detail}",
                    )
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30.0)
                    continue

            async def on_status(
                status: Dict[str, Any], provider_client: Any = client
            ) -> None:
                await self._handle_status(printer_id, status, client=provider_client)

            # Bootstrap with a one-shot status query so we can:
            # 1) seed current state quickly on startup/reconfigure
            # 2) mark clear offline/error if transport/auth is broken
            try:
                initial = await client.query_status()
                initial_status = initial.get("result", {}).get("status", {})
                if isinstance(initial_status, dict) and initial_status:
                    await self._handle_status(printer_id, initial_status, client=client)
            except Exception as exc:  # noqa: BLE001 - provider-specific failures
                await self._mark_status(
                    printer_id, PrinterStatus.OFFLINE, error=str(exc)
                )
                logger.warning(
                    "printer worker[%s] initial status query failed: %s",
                    printer_id,
                    exc,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)
                continue

            try:
                await client.subscribe_status(on_status, stop_event=stop)
                reconnect_delay = 1.0
            except Exception as exc:  # noqa: BLE001 — last-ditch
                logger.exception(
                    "printer worker[%s] subscribe crash: %s", printer_id, exc
                )
                await self._mark_status(
                    printer_id, PrinterStatus.OFFLINE, error=str(exc)
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)

    async def _handle_status(
        self, printer_id: int, status: Dict[str, Any], *, client: Any | None = None
    ) -> None:
        # Merge into in-memory snapshot.
        snap = self.snapshots.setdefault(printer_id, {})
        for obj_name, fields in status.items():
            if not isinstance(fields, dict):
                continue
            existing = snap.setdefault(obj_name, {})
            existing.update(fields)

        # Compute coarse PrinterStatus + filename for DB writeback.
        print_stats = snap.get("print_stats", {})
        sync_print_stats = dict(print_stats)
        if (
            client is not None
            and isinstance(client, ArtifactCaptureClient)
            and settings.bambu_external_capture_max_mb > 0
        ):
            sync_print_stats["_capture_available"] = True
        ms_state, vault_status = _derive_printer_status(snap)
        progress = float(snap.get("virtual_sdcard", {}).get("progress") or 0.0)
        filename = print_stats.get("filename") or None

        material_slots = status.get("material_slots")
        material_tools = status.get("material_tools")
        if isinstance(material_slots, list) or isinstance(material_tools, list):
            enriched = await self._enrich_material_slots(
                printer_id, material_slots if isinstance(material_slots, list) else []
            )
            if begin_mutating_operation():
                try:
                    await asyncio.to_thread(
                        self._sync_material_state_db,
                        printer_id,
                        enriched,
                        material_tools if isinstance(material_tools, list) else None,
                    )
                except Exception:
                    logger.exception(
                        "printer hub: material-state sync failed for %s", printer_id
                    )
                finally:
                    end_mutating_operation()

        await self._mark_status(printer_id, vault_status, error=None)
        capture = await self._sync_active_job(
            printer_id, ms_state, filename, progress, sync_print_stats
        )
        if (
            capture is not None
            and client is not None
            and isinstance(client, ArtifactCaptureClient)
        ):
            job_id, remote_path = capture
            key = (printer_id, job_id)
            if key not in self._capture_tasks:
                task = asyncio.create_task(
                    self._capture_external_artifact(
                        printer_id, job_id, remote_path, client
                    ),
                    name=f"bambu-capture-{printer_id}-{job_id}",
                )
                self._capture_tasks[key] = task
                task.add_done_callback(
                    lambda _task, capture_key=key: self._capture_tasks.pop(
                        capture_key, None
                    )
                )

        await self._broadcast(
            printer_id,
            {"type": "update", "printer_id": printer_id, "data": snap},
        )

    def _spoolman_config(self) -> tuple[str, str | None] | None:
        with self._session_factory.session() as session:
            if not runtime_config.spoolman_enabled(session):
                return None
            config = runtime_config.spoolman_config(session)
            base_url = config.get("base_url")
            if not base_url:
                return None
            return str(base_url), config.get("api_key")

    async def _enrich_material_slots(
        self, printer_id: int, slots: list[object]
    ) -> list[dict[str, Any]]:
        normalized = [dict(row) for row in slots if isinstance(row, dict)]
        spool_ids = {
            int(row["external_spool_id"])
            for row in normalized
            if isinstance(row.get("external_spool_id"), int)
        }
        if not spool_ids:
            return normalized
        config = await asyncio.to_thread(self._spoolman_config)
        if config is None:
            return normalized
        client = SpoolmanClient(config[0], config[1])
        resolved: dict[int, dict[str, Any]] = {}
        for spool_id in spool_ids:
            try:
                resolved[spool_id] = await client.get_spool(spool_id)
            except SpoolmanError:
                logger.info(
                    "printer hub: Moonraker spool %s could not be resolved for printer %s",
                    spool_id,
                    printer_id,
                )
        for row in normalized:
            spool_id = row.get("external_spool_id")
            spool = resolved.get(spool_id) if isinstance(spool_id, int) else None
            if not spool:
                continue
            filament = spool.get("filament")
            filament = filament if isinstance(filament, dict) else {}
            vendor = filament.get("vendor")
            vendor = vendor if isinstance(vendor, dict) else {}
            row.update(
                {
                    "material_type": filament.get("material"),
                    "material_brand": vendor.get("name"),
                    "color_hex": filament.get("color_hex"),
                    "spool_id": spool_id,
                    "spool_name": spool.get("name") or f"Spool {spool_id}",
                    "spool_filament_id": filament.get("id"),
                }
            )
        return normalized

    def _sync_material_state_db(
        self,
        printer_id: int,
        slots: list[dict[str, Any]],
        tools: list[object] | None = None,
    ) -> None:
        with self._session_factory.session() as session:
            printer = session.get(Printer, printer_id)
            if printer is None or not printer.provider_material_sync_enabled:
                return
            source = (
                MaterialSource.BAMBU_AMS
                if printer.provider == PrinterProvider.BAMBU_LAN
                else MaterialSource.MOONRAKER_SPOOLMAN
            )
            existing = {
                row.slot_key: row
                for row in session.exec(
                    select(PrinterMaterialSlot).where(
                        PrinterMaterialSlot.printer_id == printer_id,
                        PrinterMaterialSlot.source == source,
                    )
                ).all()
            }
            now = utcnow()
            seen: set[str] = set()
            for item in slots:
                slot_key = str(item.get("slot_key") or "").strip()
                if not slot_key:
                    continue
                seen.add(slot_key)
                row = existing.get(slot_key) or PrinterMaterialSlot(
                    printer_id=printer_id,
                    slot_key=slot_key,
                    label=str(item.get("label") or slot_key),
                    source=source,
                )
                raw_state = str(item.get("state") or "unknown").lower()
                row.label = str(item.get("label") or slot_key)
                row.tool_key = str(item["tool_key"]) if item.get("tool_key") else None
                row.state = (
                    MaterialSlotState(raw_state)
                    if raw_state in {state.value for state in MaterialSlotState}
                    else MaterialSlotState.UNKNOWN
                )
                row.material_type = (
                    str(item["material_type"]).strip()
                    if item.get("material_type")
                    else None
                )
                row.material_brand = (
                    str(item["material_brand"]).strip()
                    if item.get("material_brand")
                    else None
                )
                color = str(item.get("color_hex") or "").strip().upper().lstrip("#")
                row.color_hex = (
                    f"#{color[:6]}"
                    if len(color) >= 6
                    and all(char in "0123456789ABCDEF" for char in color[:6])
                    else None
                )
                row.spool_id = (
                    item.get("spool_id")
                    if isinstance(item.get("spool_id"), int)
                    else None
                )
                row.spool_name = (
                    str(item["spool_name"]) if item.get("spool_name") else None
                )
                row.spool_filament_id = (
                    item.get("spool_filament_id")
                    if isinstance(item.get("spool_filament_id"), int)
                    else None
                )
                row.observed_at = now
                row.updated_at = now
                session.add(row)
            for slot_key, row in existing.items():
                if slot_key not in seen:
                    session.delete(row)
            if tools is not None:
                existing_tools = {
                    row.tool_key: row
                    for row in session.exec(
                        select(PrinterTool).where(
                            PrinterTool.printer_id == printer_id,
                            PrinterTool.source == source,
                        )
                    ).all()
                }
                seen_tools: set[str] = set()
                for item in tools:
                    if not isinstance(item, dict):
                        continue
                    tool_key = str(item.get("tool_key") or "").strip()
                    if not tool_key:
                        continue
                    seen_tools.add(tool_key)
                    tool = existing_tools.get(tool_key) or PrinterTool(
                        printer_id=printer_id,
                        tool_key=tool_key,
                        label=str(item.get("label") or tool_key),
                        source=source,
                    )
                    raw_nozzle = item.get("nozzle_diameter_mm")
                    tool.label = str(item.get("label") or tool_key)
                    tool.nozzle_diameter_mm = (
                        float(raw_nozzle)
                        if isinstance(raw_nozzle, (int, float))
                        and not isinstance(raw_nozzle, bool)
                        and raw_nozzle > 0
                        else None
                    )
                    tool.observed_at = now
                    tool.updated_at = now
                    session.add(tool)
                for tool_key, tool in existing_tools.items():
                    if tool_key not in seen_tools:
                        session.delete(tool)
            session.commit()

    async def _mark_status(
        self, printer_id: int, status: PrinterStatus, *, error: str | None
    ) -> None:
        # Moonraker pushes status updates several times a second; only hit the
        # DB when something changed or the heartbeat interval elapsed, and run
        # the sync commit in a worker thread to keep the event loop free.
        now = time.monotonic()
        last = self._last_status_write.get(printer_id)
        if (
            last is not None
            and last[0] == status
            and last[1] == error
            and now - last[2] < _STATUS_WRITE_INTERVAL_S
        ):
            return
        if not begin_mutating_operation():
            return
        self._last_status_write[printer_id] = (status, error, now)
        try:
            try:
                await asyncio.to_thread(self._mark_status_db, printer_id, status, error)
            except Exception:
                logger.exception(
                    "printer hub: failed to mark status for %s", printer_id
                )
        finally:
            end_mutating_operation()

    def _mark_status_db(
        self, printer_id: int, status: PrinterStatus, error: str | None
    ) -> None:
        with self._session_factory.session() as session:
            p = session.get(Printer, printer_id)
            if p is None:
                return
            prev_status = p.status
            p.status = status
            p.last_seen_at = utcnow()
            p.last_error = error
            p.updated_at = utcnow()
            session.add(p)
            # Edge-trigger the offline event: only when transitioning *into*
            # OFFLINE from a previously-live status. Skipping UNKNOWN avoids
            # spurious alerts on startup/first-connect, and equality skips the
            # heartbeat re-write path that re-persists an unchanged status.
            if status == PrinterStatus.OFFLINE and prev_status not in (
                PrinterStatus.OFFLINE,
                PrinterStatus.UNKNOWN,
            ):
                notifications.enqueue_for_event(
                    session,
                    NotificationEventType.PRINTER_OFFLINE,
                    printer_id=printer_id,
                )
            session.commit()

    async def _sync_active_job(
        self,
        printer_id: int,
        ms_state: str,
        filename: str | None,
        progress: float,
        print_stats: Dict[str, Any],
    ) -> tuple[int, str] | None:
        """Reflect Moonraker state onto the most-recent matching PrintJob row.

        If no matching PrintJob exists and the printer is actively printing
        or paused, a placeholder row with source="external" is created so
        externally-initiated jobs are captured in the vault history.
        """
        if not filename:
            return None
        now = time.monotonic()
        breaker = self._job_sync_breakers.get(printer_id)
        if breaker is not None and now < breaker[1]:
            return None
        last = self._last_job_sync_write.get(printer_id)
        if (
            last is not None
            and last[0] == filename
            and last[1] == ms_state
            and now - last[3] < _JOB_PROGRESS_WRITE_INTERVAL_S
            and not print_stats.get("external_artifact_path")
        ):
            return None
        if not begin_mutating_operation():
            return None
        try:
            capture = await asyncio.to_thread(
                self._sync_active_job_db,
                printer_id,
                ms_state,
                filename,
                progress,
                print_stats,
            )
            self._job_sync_breakers.pop(printer_id, None)
            self._last_job_sync_write[printer_id] = (
                filename,
                ms_state,
                progress,
                now,
            )
            return capture
        except Exception:
            failures = (breaker[0] if breaker is not None else 0) + 1
            delay = 0.0
            if failures >= _JOB_SYNC_BREAKER_THRESHOLD:
                delay = min(
                    30.0 * (2 ** (failures - _JOB_SYNC_BREAKER_THRESHOLD)),
                    _JOB_SYNC_BREAKER_MAX_DELAY_S,
                )
            self._job_sync_breakers[printer_id] = (failures, now + delay)
            logger.exception("printer hub: job sync failed for printer %s", printer_id)
            return None
        finally:
            end_mutating_operation()

    def _sync_active_job_db(
        self,
        printer_id: int,
        ms_state: str,
        filename: str,
        progress: float,
        print_stats: Dict[str, Any],
    ) -> tuple[int, str] | None:
        lock = self._job_sync_db_locks.setdefault(printer_id, threading.Lock())
        with lock:
            return self._sync_active_job_db_locked(
                printer_id, ms_state, filename, progress, print_stats
            )

    def _sync_active_job_db_locked(
        self,
        printer_id: int,
        ms_state: str,
        filename: str,
        progress: float,
        print_stats: Dict[str, Any],
    ) -> tuple[int, str] | None:
        with self._session_factory.session() as session:
            job = None
            printer = session.get(Printer, printer_id)
            bambu_printer = (
                printer is not None and printer.provider == PrinterProvider.BAMBU_LAN
            )
            provider_job_id = next(
                (
                    text
                    for value in (
                        print_stats.get("external_task_id"),
                        print_stats.get("external_subtask_id"),
                        print_stats.get("external_project_id"),
                    )
                    if (text := _reported_text(value)) not in (None, "0")
                ),
                None,
            )
            cached = self._active_job_cache.get(printer_id)
            incoming_identity = _bambu_identity(print_stats)
            if cached is not None:
                cached_job = session.get(PrintJob, cached[1])
                # Keep identity continuity ahead of the printer's transient
                # provider_job_id. Bambu commonly emits project-only and
                # task-only reports for one run; the same filename is the
                # bounded transition fallback while a terminal tick still
                # closes the row normally.
                if (
                    cached_job is not None
                    and cached_job.dedupe_absorbed_at is None
                    and cached_job.finished_at is None
                    and (
                        (
                            not incoming_identity
                            and cached_job.remote_filename == filename
                            and (not bambu_printer or cached_job.source == "vault")
                        )
                        or (
                            incoming_identity
                            and (
                                _bambu_identity_matches(
                                    incoming_identity, _bambu_identity(cached_job)
                                )
                                or _bambu_project_task_transition(
                                    incoming_identity, cached_job, filename, ms_state
                                )
                            )
                        )
                    )
                ):
                    job = cached_job

            if job is None:
                rows = session.exec(
                    select(PrintJob)
                    .where(PrintJob.printer_id == printer_id, live(PrintJob))
                    .order_by(PrintJob.created_at.desc())  # type: ignore[attr-defined]
                ).all()
                # Identity matching is set-based rather than provider_job_id
                # matching: any task/subtask/project overlap is one job.
                for candidate in rows:
                    candidate_identity = _bambu_identity(candidate)
                    if _bambu_identity_matches(incoming_identity, candidate_identity):
                        job = candidate
                        break
                # A project-only -> task-only transition has no overlapping
                # value. Match only that active external row by the same
                # reported filename; filename alone is never an identity.
                if job is None:
                    for candidate in rows:
                        if _bambu_project_task_transition(
                            incoming_identity, candidate, filename, ms_state
                        ):
                            job = candidate
                            break
                # Provider-neutral reports (Moonraker, OctoPrint, PrusaLink,
                # and manual vault dispatch) do not carry a typed Bambu
                # identity. Reconcile them to the active row for the same
                # filename so a vault job is updated instead of creating an
                # external sentinel row. A filename is never allowed to
                # override a non-empty Bambu identity above.
                if job is None and not incoming_identity:
                    for candidate in rows:
                        if (
                            candidate.remote_filename == filename
                            and candidate.finished_at is None
                            and candidate.dedupe_absorbed_at is None
                            and (not bambu_printer or candidate.source == "vault")
                        ):
                            job = candidate
                            break
                # A repeated provider-neutral terminal report arrives after
                # the active row has already been finished. Select the latest
                # matching history row so the terminal guard below makes the
                # update idempotent. Printing/paused reports intentionally do
                # not use this path and create a fresh reprint row.
                if (
                    job is None
                    and not incoming_identity
                    and ms_state
                    in (
                        "complete",
                        "cancelled",
                        "error",
                        "failed",
                    )
                ):
                    for candidate in rows:
                        if (
                            candidate.remote_filename == filename
                            and candidate.finished_at is not None
                            and candidate.dedupe_absorbed_at is None
                            and (not bambu_printer or candidate.source == "vault")
                        ):
                            job = candidate
                            break

            # A finished job is history, not the live print — its state never
            # moves again. When the printer starts a *new* run of the same
            # file (a fresh printing/paused tick), don't revive the finished
            # row — fall through to create a new one. Any other tick for a
            # finished job (a terminal state that disagrees with what's
            # already recorded, or a stale/delayed poll response racing
            # behind the one that already closed it out) is a no-op: nothing
            # should ever flip a job's state back off of a terminal one.
            if job is not None and job.finished_at is not None:
                if ms_state not in ("printing", "paused"):
                    return None
                job = None
                self._active_job_cache.pop(printer_id, None)

            if job is None:
                # Keep creation and the first observed provider state in one
                # transaction. Previously a terminal cancellation committed a
                # default QUEUED row first, so concurrent dashboards briefly
                # counted a phantom active job before the terminal writeback.
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
                        provider_job_id=provider_job_id,
                        artifact_evidence="metadata_only",
                    )
                    session.add(job)
                    session.flush()
                    logger.info(
                        "captured external print job %s on printer %s (state=%s)",
                        filename,
                        printer_id,
                        ms_state,
                    )
                else:
                    return None

            assert job.id is not None
            self._active_job_cache[printer_id] = (filename, job.id)

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
            reported_fields = {
                "external_display_name": _reported_text(
                    print_stats.get("external_display_name")
                ),
                "external_task_id": _reported_text(print_stats.get("external_task_id")),
                "external_subtask_id": _reported_text(
                    print_stats.get("external_subtask_id")
                ),
                "external_project_id": _reported_text(
                    print_stats.get("external_project_id")
                ),
                "external_profile_id": _reported_text(
                    print_stats.get("external_profile_id")
                ),
                "external_gcode_file": _reported_text(
                    print_stats.get("external_gcode_file")
                ),
                "external_plate_index": _reported_int(
                    print_stats.get("external_plate_index")
                ),
                "external_current_layer": _reported_int(
                    print_stats.get("external_current_layer")
                ),
                "external_total_layers": _reported_int(
                    print_stats.get("external_total_layers")
                ),
                "external_nozzle_diameter": _reported_float(
                    print_stats.get("external_nozzle_diameter")
                ),
            }
            for field_name, value in reported_fields.items():
                if value is None:
                    continue
                current = getattr(job, field_name)
                if field_name in _BAMBU_ID_FIELDS:
                    current_text = _reported_text(current)
                    if current_text not in (None, "0") and current_text != value:
                        # Identity matching above rejects this case. Keep the
                        # guard here so a future selection path cannot
                        # overwrite an established typed identity.
                        continue
                if current != value:
                    setattr(job, field_name, value)
                    changed = True
            if provider_job_id and job.provider_job_id != provider_job_id:
                job.provider_job_id = provider_job_id
                changed = True
            capture_path = _reported_text(
                print_stats.get("external_artifact_path")
                or print_stats.get("external_gcode_file")
            )
            capture: tuple[int, str] | None = None
            if (
                job.source == "external"
                and capture_path
                and print_stats.get("_capture_available") is True
                and job.artifact_evidence in ("metadata_only", "capture_failed")
                and (
                    job.artifact_evidence != "capture_failed"
                    or print_stats.get("external_artifact_path")
                )
            ):
                job.artifact_evidence = "capture_pending"
                job.artifact_capture_error = None
                job.artifact_capture_error_code = None
                job.artifact_capture_error_message = None
                changed = True
                assert job.id is not None
                capture = (job.id, capture_path)
            if new_state != job.state:
                job.state = new_state
                changed = True
            if abs(progress - job.progress) > 1e-3:
                job.progress = progress
                changed = True
            if new_state == PrintJobState.PRINTING and job.started_at is None:
                job.started_at = utcnow()
                changed = True
            just_finished = False
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
                just_finished = True
                # Capture the measured outcome once, on the finishing tick.
                duration = print_stats.get("total_duration") or print_stats.get(
                    "print_duration"
                )
                if duration:
                    job.actual_duration_s = int(duration)
                used_mm = print_stats.get("filament_used")
                if used_mm:
                    job.filament_used_mm = float(used_mm)
                    material = print_results.material_type_for_file(
                        session, job.file_id
                    )
                    # When a synced spool was selected, prefer its real
                    # diameter/density over the static per-material table.
                    linked = print_results.linked_profile_for_spool(
                        session, job.spool_filament_id
                    )
                    job.filament_used_g = filament_svc.mm_to_grams(
                        float(used_mm),
                        material,
                        diameter_mm=(
                            linked.diameter_mm
                            if linked and linked.diameter_mm
                            else filament_svc.DEFAULT_DIAMETER_MM
                        ),
                        density_g_cm3=linked.density_g_cm3 if linked else None,
                    )
                if new_state == PrintJobState.COMPLETED:
                    (
                        job.filament_g_effective,
                        job.cost,
                    ) = print_results.resolve_completion_cost(session, job)
                    printer = session.get(Printer, printer_id)
                    if (
                        job.source == "vault"
                        and printer is not None
                        and printer.operator_release_required
                    ):
                        job.operator_gate_state = OperatorGateState.PENDING
            if changed:
                job.updated_at = utcnow()
                if new_state == PrintJobState.FAILED:
                    job.error = print_stats.get("message")
                session.add(job)
                # Enqueue the terminal-state notification in the *same*
                # transaction as the job writeback (transactional outbox).
                # ``just_finished`` guarantees this fires exactly once per job.
                if just_finished:
                    event_type = _TERMINAL_EVENT.get(new_state)
                    if event_type is not None:
                        # job.id is already assigned (existing row, or the
                        # external placeholder committed above).
                        notifications.enqueue_for_event(
                            session,
                            event_type,
                            printer_id=printer_id,
                            job=job,
                        )
                session.commit()

            # Auto-mark the printed revision known_good after a successful print.
            if (
                just_finished
                and new_state == PrintJobState.COMPLETED
                and job.source == "vault"
                and auto_mark_known_good_enabled(session)
            ):
                print_results.mark_known_good_if_eligible(session, job.file_id)

            # Write measured consumption back to Spoolman, once, on completion.
            # No-ops unless a spool was selected and grams were measured; runs
            # after the job is committed so a Spoolman outage never blocks it.
            if just_finished and new_state == PrintJobState.COMPLETED:
                print_results.record_spool_usage(session, job)
            return capture

    async def _capture_external_artifact(
        self, printer_id: int, job_id: int, remote_path: str, client: Any
    ) -> None:
        """Best-effort Bambu cache recovery, outside the MQTT callback thread."""

        max_mb = settings.bambu_external_capture_max_mb
        if max_mb <= 0:
            await asyncio.to_thread(
                self._mark_capture_failed,
                job_id,
                "external_artifact_capture_disabled",
                "External artifact capture is disabled by configuration.",
            )
            return
        try:
            with tempfile.TemporaryDirectory(prefix="printstash-bambu-") as tmp:
                leaf = Path(unquote(urlparse(remote_path).path)).name or "external.3mf"
                staged = Path(tmp) / leaf
                await client.download_artifact(
                    remote_path, staged, max_bytes=max_mb * 1024 * 1024
                )
                await asyncio.to_thread(
                    self._persist_external_artifact, job_id, staged, leaf
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - metadata-only is a valid outcome
            detail = getattr(exc, "action_code", None) or getattr(
                exc, "code", "artifact_capture_failed"
            )
            message = str(exc) or detail
            logger.info(
                "external artifact capture unavailable for printer=%s job=%s: %s",
                printer_id,
                job_id,
                detail,
            )
            await asyncio.to_thread(self._mark_capture_failed, job_id, detail, message)

    def _persist_external_artifact(
        self, job_id: int, staged: Path, filename: str
    ) -> None:
        lowered = filename.lower()
        file_type = FileType.THREE_MF if lowered.endswith(".3mf") else FileType.GCODE
        blob_hash = sha256_file(staged)
        meta = gcode_parser.parse(staged) if file_type == FileType.GCODE else {}
        thumb_bytes = thumbnail.extract(staged) if file_type == FileType.GCODE else None
        with self._session_factory.session() as session:
            job = session.get(PrintJob, job_id)
            if job is None or job.source != "external":
                return
            display = (
                job.external_display_name or Path(filename).stem or "External print"
            )
            model, _created = ingestion.resolve_or_create_model(
                session,
                dedup_hash=blob_hash,
                model_name=display,
            )
            file_row = ingestion.persist_artifact(
                session,
                model=model,
                staged_path=staged,
                original_filename=filename,
                file_type=file_type,
                blob_hash=blob_hash,
                meta=meta,
                thumb_bytes=thumb_bytes,
                overwrite_thumbnail=False,
                ingestion_key=f"bambu-job-{job_id}",
            )
            job = session.get(PrintJob, job_id)
            if job is None:
                return
            assert file_row.id is not None
            job.file_id = file_row.id
            job.model_id = file_row.model_id
            job.artifact_evidence = (
                "project_archived"
                if file_type == FileType.THREE_MF
                else "gcode_archived"
            )
            job.artifact_capture_error = None
            job.artifact_capture_error_code = None
            job.artifact_capture_error_message = None
            job.updated_at = utcnow()
            session.add(job)
            session.commit()

    def _mark_capture_failed(
        self, job_id: int, error: str, message: str | None = None
    ) -> None:
        with self._session_factory.session() as session:
            job = session.get(PrintJob, job_id)
            if job is None or job.artifact_evidence not in (
                "capture_pending",
                "capture_failed",
            ):
                return
            job.artifact_evidence = "capture_failed"
            job.artifact_capture_error = error[:1024]
            job.artifact_capture_error_code = error[:128]
            job.artifact_capture_error_message = (message or error)[:1024]
            job.updated_at = utcnow()
            session.add(job)
            session.commit()


def get_hub(request: Request) -> PrinterHub:
    """FastAPI dependency: returns the PrinterHub stored on app.state."""
    return request.app.state.printer_hub


def get_hub_from_ws(websocket: WebSocket) -> PrinterHub:
    """FastAPI dependency (WebSocket variant): returns the PrinterHub."""
    return websocket.app.state.printer_hub


def _get_sentinel_ids(session: Session) -> tuple[int, int]:
    """Return (file_id, model_id) of lazily-created external job sentinel rows."""
    from app.db.models import (
        SENTINEL_FILE_HASH,
        SENTINEL_MODEL_HASH,
        File,
        FileType,
        Model,
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
    assert model.id is not None

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

    assert f.id is not None
    return f.id, model.id
