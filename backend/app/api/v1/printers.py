"""Printer management, send-to-print, live WS status, and farm dashboard (Stage 3 / The Hub)."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.core.http import get_or_404
from app.core.logging import get_logger
from app.core.security import require_auth
from app.core.time import utcnow
from app.db.models import (
    File,
    FileType,
    Model,
    PrintJob,
    PrintJobState,
    Printer,
    PrinterFile,
    PrinterProvider,
)
from app.db.session import get_session
from app.schemas.printers import (
    PrinterCapabilities,
    PrinterCreate,
    PrinterFileRead,
    PrinterRead,
    PrinterUpdate,
    PrintJobRead,
    SendToPrinter,
    StartPrinterFile,
)
from app.services.printer_hub import (
    PrinterHub,
    _get_sentinel_ids,
    get_hub,
    get_hub_from_ws,
)
from app.services.printer_provider import (
    ProviderError,
    capabilities_for_provider,
    get_provider_client,
)
from app.services.printer_files import (
    list_printer_files,
    sync_printer_files,
    upsert_printer_file,
)
from app.services.storage_backend import get_backend

logger = get_logger(__name__)

router = APIRouter(prefix="/printers", tags=["printers"])


def _validate_provider_config(p: Printer) -> None:
    if p.provider == PrinterProvider.MOONRAKER and not p.moonraker_url:
        raise HTTPException(status_code=400, detail="moonraker_url_required")
    if p.provider == PrinterProvider.BAMBU_LAN:
        if not p.bambu_host:
            raise HTTPException(status_code=400, detail="bambu_host_required")
        if not p.bambu_serial:
            raise HTTPException(status_code=400, detail="bambu_serial_required")
        if not p.bambu_access_code:
            raise HTTPException(status_code=400, detail="bambu_access_code_required")


def _to_read(p: Printer) -> PrinterRead:
    caps = capabilities_for_provider(p.provider)
    return PrinterRead(
        id=p.id,  # type: ignore[arg-type]
        name=p.name,
        provider=p.provider,
        moonraker_url=p.moonraker_url,
        has_api_key=bool(p.api_key),
        bambu_host=p.bambu_host,
        bambu_serial=p.bambu_serial,
        has_bambu_access_code=bool(p.bambu_access_code),
        capabilities=PrinterCapabilities(**caps.__dict__),
        notes=p.notes,
        group=p.group,
        status=p.status,
        last_seen_at=p.last_seen_at,
        last_error=p.last_error,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _to_printer_file_read(
    row: PrinterFile,
    *,
    printer_name: str | None = None,
    file_row: File | None = None,
    model_row: Model | None = None,
) -> PrinterFileRead:
    return PrinterFileRead(
        id=row.id,  # type: ignore[arg-type]
        printer_id=row.printer_id,
        printer_name=printer_name,
        file_id=row.file_id,
        model_id=file_row.model_id if file_row else None,
        model_name=model_row.name if model_row else None,
        original_filename=file_row.original_filename if file_row else None,
        remote_filename=row.remote_filename,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        matched_by=row.matched_by,
        modified_at=row.modified_at,
        last_seen_at=row.last_seen_at,
        missing_since=row.missing_since,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Printer CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=List[PrinterRead], summary="List printers")
def list_printers(
    group: Optional[str] = Query(default=None, description="Filter by printer group"),
    session: Session = Depends(get_session),
) -> List[PrinterRead]:
    stmt = select(Printer).order_by(Printer.name)
    stmt = stmt.where(Printer.deleted_at.is_(None))  # type: ignore[union-attr]
    if group is not None:
        stmt = stmt.where(Printer.group == group)
    return [_to_read(p) for p in session.exec(stmt).all()]


@router.get(
    "/dashboard",
    summary="Aggregated farm health summary",
    description=(
        "Returns counts per PrinterStatus plus total printers, active print jobs, "
        "and a per-group breakdown."
    ),
)
def farm_dashboard(session: Session = Depends(get_session)) -> dict:
    printers = session.exec(select(Printer)).all()
    status_counts: dict[str, int] = {}
    group_counts: dict[str, dict[str, int]] = {}

    for p in printers:
        s = p.status.value
        status_counts[s] = status_counts.get(s, 0) + 1
        g = p.group or "__ungrouped"
        if g not in group_counts:
            group_counts[g] = {}
        group_counts[g][s] = group_counts[g].get(s, 0) + 1

    active_jobs = session.exec(
        select(PrintJob).where(
            PrintJob.state.in_(
                [
                    PrintJobState.QUEUED,
                    PrintJobState.STARTED,
                    PrintJobState.PRINTING,
                    PrintJobState.PAUSED,
                    PrintJobState.UPLOADING,
                ]
            )
        )
    ).all()

    return {
        "total_printers": len(printers),
        "status_counts": status_counts,
        "active_jobs": len(active_jobs),
        "groups": [
            {"name": name, "count": sum(counts.values()), "status_counts": counts}
            for name, counts in sorted(group_counts.items())
        ],
    }


@router.get("/{printer_id}", response_model=PrinterRead, summary="Get a printer")
def get_printer(
    printer_id: int, session: Session = Depends(get_session)
) -> PrinterRead:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    if p.deleted_at is not None:
        raise HTTPException(status_code=404, detail="printer_not_found")
    return _to_read(p)


@router.post(
    "",
    response_model=PrinterRead,
    status_code=201,
    dependencies=[Depends(require_auth)],
    summary="Register a new printer",
)
async def create_printer(
    payload: PrinterCreate,
    session: Session = Depends(get_session),
    hub: PrinterHub = Depends(get_hub),
) -> PrinterRead:
    p = Printer(
        name=payload.name.strip(),
        provider=payload.provider,
        moonraker_url=(payload.moonraker_url or "").strip().rstrip("/"),
        api_key=payload.api_key or None,
        bambu_host=payload.bambu_host,
        bambu_serial=payload.bambu_serial,
        bambu_access_code=payload.bambu_access_code,
        notes=payload.notes,
        group=payload.group.strip() if payload.group else None,
    )
    _validate_provider_config(p)
    session.add(p)
    session.commit()
    session.refresh(p)
    assert p.id is not None
    await hub.add_printer(p.id)
    return _to_read(p)


@router.patch(
    "/{printer_id}",
    response_model=PrinterRead,
    dependencies=[Depends(require_auth)],
    summary="Update a printer",
)
async def update_printer(
    printer_id: int,
    payload: PrinterUpdate,
    session: Session = Depends(get_session),
    hub: PrinterHub = Depends(get_hub),
) -> PrinterRead:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    if payload.provider is not None:
        p.provider = payload.provider
    if payload.name is not None:
        p.name = payload.name.strip() or p.name
    if payload.moonraker_url is not None:
        p.moonraker_url = payload.moonraker_url.strip().rstrip("/")
    if payload.api_key is not None:
        p.api_key = payload.api_key or None
    if payload.bambu_host is not None:
        p.bambu_host = payload.bambu_host or None
    if payload.bambu_serial is not None:
        p.bambu_serial = payload.bambu_serial or None
    if payload.bambu_access_code is not None:
        p.bambu_access_code = payload.bambu_access_code or None
    if payload.notes is not None:
        p.notes = payload.notes
    if payload.group is not None:
        p.group = payload.group.strip() or None
    _validate_provider_config(p)
    p.updated_at = utcnow()
    session.add(p)
    session.commit()
    session.refresh(p)
    await hub.restart_printer(printer_id)
    return _to_read(p)


@router.delete(
    "/{printer_id}",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Remove a printer",
)
async def delete_printer(
    printer_id: int,
    session: Session = Depends(get_session),
    hub: PrinterHub = Depends(get_hub),
) -> Response:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    p.deleted_at = utcnow()
    session.add(p)
    session.commit()
    await hub.remove_printer(printer_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Send-to-print + control
# ---------------------------------------------------------------------------


@router.post(
    "/{printer_id}/send",
    response_model=PrintJobRead,
    dependencies=[Depends(require_auth)],
    summary="Upload a vault file to the printer (optionally start the print)",
    description=(
        "Streams the chosen vault File to Moonraker's gcode store. The File must "
        "be a .gcode artifact. If start_print is true, Moonraker is asked to start "
        "the print immediately after upload."
    ),
)
async def send_to_printer(
    printer_id: int,
    payload: SendToPrinter,
    session: Session = Depends(get_session),
) -> PrintJobRead:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    provider = get_provider_client(p)
    if not provider.capabilities.can_upload:
        raise HTTPException(
            status_code=409,
            detail="operation_not_supported_for_provider",
        )
    f = get_or_404(session, File, payload.file_id, "file_not_found")
    if f.file_type != FileType.GCODE:
        raise HTTPException(status_code=400, detail="file_not_gcode")
    backend = get_backend()
    blob_exists = await run_in_threadpool(backend.exists, f.path)
    if not blob_exists:
        raise HTTPException(status_code=410, detail="file_blob_missing")
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f"print-{f.id}-",
        suffix=Path(f.original_filename).suffix,
        delete=False,
    )
    temp_target = Path(temp_file.name)
    temp_file.close()
    try:
        local = await run_in_threadpool(backend.download_to_path, f.path, temp_target)
    except Exception:
        temp_target.unlink(missing_ok=True)
        logger.exception("failed to stage print upload file=%s", f.id)
        raise HTTPException(status_code=502, detail="storage_error")

    remote_name = (payload.remote_filename or f.original_filename).strip()
    if not remote_name.lower().endswith((".gcode", ".g", ".gco")):
        remote_name += ".gcode"

    job = PrintJob(
        printer_id=printer_id,
        file_id=f.id,  # type: ignore[arg-type]
        model_id=f.model_id,
        remote_filename=remote_name,
        state=PrintJobState.UPLOADING,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        moonraker_provider = provider
        # Upload is only supported by Moonraker provider at this stage.
        from app.services.printer_provider import MoonrakerProvider

        if not isinstance(moonraker_provider, MoonrakerProvider):
            raise HTTPException(
                status_code=409,
                detail="operation_not_supported_for_provider",
            )
        await moonraker_provider.client.upload_gcode(
            local, remote_name, start_print=payload.start_print
        )
    except ProviderError as exc:
        job.state = PrintJobState.FAILED
        job.error = str(exc)
        job.finished_at = utcnow()
        session.add(job)
        session.commit()
        raise HTTPException(status_code=502, detail=exc.code)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "send_to_printer failed for printer=%s file=%s", printer_id, f.id
        )
        job.state = PrintJobState.FAILED
        job.error = str(exc)
        job.finished_at = utcnow()
        session.add(job)
        session.commit()
        raise HTTPException(status_code=502, detail="provider_error")
    finally:
        try:
            temp_target.unlink(missing_ok=True)
        except OSError:
            logger.warning("failed to remove temp print upload %s", temp_target)

    # Moonraker accepts print=true on upload but returns immediately; the
    # print_stats subscription will move the job into PRINTING. We mark
    # STARTED here optimistically when caller asked to print.
    job.state = PrintJobState.STARTED if payload.start_print else PrintJobState.QUEUED
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    out = PrintJobRead(**job.model_dump())
    upsert_printer_file(
        session,
        printer_id=printer_id,
        file_id=f.id,  # type: ignore[arg-type]
        remote_filename=remote_name,
        size_bytes=f.size_bytes,
        sha256=f.sha256,
        matched_by="upload_history",
    )
    return out


@router.post(
    "/{printer_id}/start",
    response_model=PrintJobRead,
    dependencies=[Depends(require_auth)],
    summary="Start a G-code file already present on the printer",
    description=(
        "Asks the provider to start a remote G-code file already in the printer's "
        "file store. When the remote file is matched to a vault File, the job is "
        "linked to that model; otherwise it is recorded as an external job."
    ),
)
async def start_printer_file(
    printer_id: int,
    payload: StartPrinterFile,
    session: Session = Depends(get_session),
) -> PrintJobRead:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    provider = get_provider_client(p)
    if not provider.capabilities.can_start:
        raise HTTPException(
            status_code=409,
            detail="operation_not_supported_for_provider",
        )

    file_row: File | None = None
    source = "external"
    if payload.file_id is not None:
        candidate = get_or_404(session, File, payload.file_id, "file_not_found")
        if candidate.file_type != FileType.GCODE:
            raise HTTPException(status_code=400, detail="file_not_gcode")
        file_row = candidate
        source = "vault"
    else:
        printer_file = session.exec(
            select(PrinterFile).where(
                PrinterFile.printer_id == printer_id,
                PrinterFile.remote_filename == payload.remote_filename,
                PrinterFile.missing_since.is_(None),  # type: ignore[union-attr]
            )
        ).first()
        if printer_file and printer_file.file_id is not None:
            candidate = session.get(File, printer_file.file_id)
            if candidate and candidate.deleted_at is None:
                file_row = candidate
                source = "vault"

    if file_row is not None:
        file_id = int(file_row.id)
        model_id = file_row.model_id
    else:
        file_id, model_id = _get_sentinel_ids(session)

    job = PrintJob(
        printer_id=printer_id,
        file_id=file_id,
        model_id=model_id,
        remote_filename=payload.remote_filename,
        state=PrintJobState.STARTED,
        source=source,
        started_at=utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        await provider.start(payload.remote_filename)
    except ProviderError as exc:
        job.state = PrintJobState.FAILED
        job.error = exc.detail
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        session.add(job)
        session.commit()
        raise HTTPException(status_code=502, detail=exc.code)
    except Exception:
        logger.exception(
            "printer start failed printer=%s remote=%s",
            printer_id,
            payload.remote_filename,
        )
        job.state = PrintJobState.FAILED
        job.error = "provider_error"
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        session.add(job)
        session.commit()
        raise HTTPException(status_code=502, detail="provider_error")

    return PrintJobRead(**job.model_dump())


async def _printer_control(printer_id: int, session: Session, action: str) -> dict:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    client = get_provider_client(p)
    cap_map = {
        "start": client.capabilities.can_start,
        "pause": client.capabilities.can_pause,
        "resume": client.capabilities.can_resume,
        "cancel": client.capabilities.can_cancel,
    }
    if action in cap_map and not cap_map[action]:
        raise HTTPException(
            status_code=409,
            detail="operation_not_supported_for_provider",
        )
    try:
        await getattr(client, action)()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.code)
    except Exception:
        logger.exception(
            "printer control failed action=%s printer=%s", action, printer_id
        )
        raise HTTPException(status_code=502, detail="provider_error")
    return {"ok": True}


@router.post(
    "/{printer_id}/pause",
    dependencies=[Depends(require_auth)],
    summary="Pause the current print",
)
async def pause_printer(
    printer_id: int, session: Session = Depends(get_session)
) -> dict:
    return await _printer_control(printer_id, session, "pause")


@router.post(
    "/{printer_id}/resume",
    dependencies=[Depends(require_auth)],
    summary="Resume the paused print",
)
async def resume_printer(
    printer_id: int, session: Session = Depends(get_session)
) -> dict:
    return await _printer_control(printer_id, session, "resume")


@router.post(
    "/{printer_id}/cancel",
    dependencies=[Depends(require_auth)],
    summary="Cancel the current print",
)
async def cancel_printer(
    printer_id: int, session: Session = Depends(get_session)
) -> dict:
    return await _printer_control(printer_id, session, "cancel")


# ---------------------------------------------------------------------------
# Live snapshots & print history
# ---------------------------------------------------------------------------


@router.get(
    "/{printer_id}/status",
    summary="One-shot snapshot of cached printer state",
)
def printer_status(
    printer_id: int,
    session: Session = Depends(get_session),
    hub: PrinterHub = Depends(get_hub),
) -> dict:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    return {
        "printer": _to_read(p).model_dump(mode="json"),
        "snapshot": hub.snapshots.get(printer_id, {}),
    }


@router.get(
    "/{printer_id}/files",
    response_model=List[PrinterFileRead],
    summary="List files known to exist on a printer",
)
def list_files_on_printer(
    printer_id: int,
    session: Session = Depends(get_session),
) -> List[PrinterFileRead]:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    out: list[PrinterFileRead] = []
    for row in list_printer_files(session, printer_id=printer_id):
        file_row = session.get(File, row.file_id) if row.file_id else None
        model_row = session.get(Model, file_row.model_id) if file_row else None
        out.append(
            _to_printer_file_read(
                row,
                printer_name=p.name,
                file_row=file_row,
                model_row=model_row,
            )
        )
    return out


@router.post(
    "/{printer_id}/files/sync",
    response_model=List[PrinterFileRead],
    dependencies=[Depends(require_auth)],
    summary="Sync the printer's remote G-code file inventory",
)
async def sync_files_on_printer(
    printer_id: int,
    session: Session = Depends(get_session),
) -> List[PrinterFileRead]:
    p = get_or_404(session, Printer, printer_id, "printer_not_found")
    provider = get_provider_client(p)
    if not provider.capabilities.can_list_files:
        raise HTTPException(
            status_code=409,
            detail="operation_not_supported_for_provider",
        )
    try:
        remote_files = await provider.list_files()
    except ProviderError as exc:
        p.last_error = exc.detail
        p.updated_at = utcnow()
        session.add(p)
        session.commit()
        raise HTTPException(status_code=502, detail=exc.code)

    rows = sync_printer_files(session, printer_id=printer_id, remote_files=remote_files)
    out: list[PrinterFileRead] = []
    for row in rows:
        file_row = session.get(File, row.file_id) if row.file_id else None
        model_row = session.get(Model, file_row.model_id) if file_row else None
        out.append(
            _to_printer_file_read(
                row,
                printer_name=p.name,
                file_row=file_row,
                model_row=model_row,
            )
        )
    return out


@router.get(
    "/{printer_id}/jobs",
    response_model=List[PrintJobRead],
    summary="List recent print jobs for a printer",
)
def list_jobs(
    printer_id: int,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> List[PrintJobRead]:
    rows = session.exec(
        select(PrintJob)
        .where(PrintJob.printer_id == printer_id)
        .order_by(PrintJob.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [PrintJobRead(**j.model_dump()) for j in rows]


@router.websocket("/{printer_id}/ws")
async def printer_ws(
    websocket: WebSocket,
    printer_id: int,
    hub: PrinterHub = Depends(get_hub_from_ws),
) -> None:
    """Live status stream for a single printer.

    Pushes JSON messages of the form::

        {"type": "snapshot", "printer_id": <id>, "data": {...full snapshot...}}
        {"type": "update",   "printer_id": <id>, "data": {...changed objects...}}
    """
    await websocket.accept()
    await hub.attach(printer_id, websocket)
    try:
        while True:
            # Client messages are ignored; we just need to detect disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.detach(printer_id, websocket)
