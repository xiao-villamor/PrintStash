"""Import completed print history from a printer's own record (Moonraker).

Kept separate from ``print_results`` (which owns post-completion side effects
for jobs already tracked in the vault) — this module owns turning a printer's
history feed into new ``PrintJob`` rows.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, List

from sqlmodel import Session, select

from app.db.models import File, FileType, PrintJob, PrintJobState
from app.db.scopes import live
from app.db.session import get_session_factory
from app.schemas.models import ImportedPrintJobRead
from app.services import filament as filament_svc
from app.services import print_results
from app.services.moonraker import MoonrakerClient, MoonrakerError
from app.services.runtime_config import auto_mark_known_good_enabled

_STATE_MAP = {
    "completed": PrintJobState.COMPLETED,
    "cancelled": PrintJobState.CANCELLED,
    "error": PrintJobState.FAILED,
}


def _ts(t: float | None) -> datetime | None:
    return datetime.fromtimestamp(t, tz=timezone.utc) if t else None


async def import_print_jobs_from_printer(
    *,
    model_id: int,
    printer_id: int,
    moonraker_url: str,
    moonraker_api_key: str | None,
) -> List[ImportedPrintJobRead]:
    """Fetch a printer's print history and import entries matching this model's G-code files.

    Dedup compares filenames case-insensitively on both sides — printers
    (notably Moonraker after a firmware update) can report a filename with
    different casing across polls than what was originally uploaded, and a
    case-sensitive comparison would re-import the entire history.
    """
    client = MoonrakerClient(moonraker_url, moonraker_api_key)
    try:
        history = await client.get_print_history(limit=100)
    except MoonrakerError as exc:
        raise MoonrakerError(str(exc)) from exc

    return await asyncio.to_thread(
        _import_history,
        history,
        model_id=model_id,
        printer_id=printer_id,
    )


def _import_history(
    history: list[dict[str, Any]], *, model_id: int, printer_id: int
) -> List[ImportedPrintJobRead]:
    """Persist fetched history in a worker-owned synchronous session."""
    with get_session_factory().scoped_session() as session:
        return _import_history_with_session(
            session, history, model_id=model_id, printer_id=printer_id
        )


def _import_history_with_session(
    session: Session,
    history: list[dict[str, Any]],
    *,
    model_id: int,
    printer_id: int,
) -> List[ImportedPrintJobRead]:
    gcode_files = session.exec(
        select(File)
        .where(File.model_id == model_id)
        .where(live(File))
        .where(File.file_type == FileType.GCODE)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    filenames_to_file = {f.original_filename.lower(): f for f in gcode_files}

    existing_remote = {
        row.lower()
        for row in session.exec(
            select(PrintJob.remote_filename)
            .where(PrintJob.printer_id == printer_id)
            .where(PrintJob.model_id == model_id)
            .where(live(PrintJob))
        ).all()
    }

    results: List[ImportedPrintJobRead] = []
    newly_completed_file_ids: set[int] = set()
    for entry in history:
        fname = entry.get("filename", "")
        matched = filenames_to_file.get(fname.lower())
        already_imported = fname.lower() in existing_remote

        if matched and not already_imported:
            raw_status = entry.get("status", "completed")
            state = _STATE_MAP.get(raw_status, PrintJobState.COMPLETED)
            start_ts = entry.get("start_time")
            end_ts = entry.get("end_time")
            duration = entry.get("print_duration")
            used_mm = entry.get("filament_used")
            material = print_results.material_type_for_file(session, matched.id)
            job = PrintJob(
                printer_id=printer_id,
                file_id=matched.id,
                model_id=model_id,
                remote_filename=fname,
                state=state,
                source="printer_history",
                started_at=_ts(start_ts),
                finished_at=_ts(end_ts),
                actual_duration_s=int(duration) if duration else None,
                filament_used_mm=float(used_mm) if used_mm else None,
                filament_used_g=filament_svc.mm_to_grams(used_mm, material)
                if used_mm
                else None,
            )
            if state == PrintJobState.COMPLETED:
                job.filament_g_effective, job.cost = (
                    print_results.resolve_completion_cost(session, job)
                )
                newly_completed_file_ids.add(matched.id)

            session.add(job)
            existing_remote.add(fname.lower())

            results.append(
                ImportedPrintJobRead(
                    filename=fname,
                    status=raw_status,
                    print_duration=entry.get("print_duration"),
                    start_time=start_ts,
                    end_time=end_ts,
                    matched_file_id=matched.id,
                    imported=True,
                )
            )
        elif matched and already_imported:
            results.append(
                ImportedPrintJobRead(
                    filename=fname,
                    status=entry.get("status", ""),
                    matched_file_id=matched.id,
                    imported=False,
                )
            )

    session.commit()

    if newly_completed_file_ids and auto_mark_known_good_enabled(session):
        for file_id in newly_completed_file_ids:
            print_results.mark_known_good_if_eligible(session, file_id)

    return results
