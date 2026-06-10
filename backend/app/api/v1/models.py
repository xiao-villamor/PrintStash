"""Model browse + detail + edit + soft-delete endpoints.

Read-model assembly lives in ``services/model_views``; trash lifecycle in
``services/trash``. This router keeps HTTP concerns only.
"""

from __future__ import annotations

from pathlib import Path
import uuid
from typing import Literal, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File as UploadFileParam,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlmodel import Session, delete, select
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.security import require_auth
from app.core.time import utcnow
from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelTagLink,
    Printer,
    PrinterFile,
    PrintJob,
    PrintJobState,
)
from app.db.scopes import live
from app.db.session import get_session
from app.schemas.models import (
    FileRevisionUpdate,
    ImportedPrintJobRead,
    ManualPrintJobCreate,
    ModelPrinterFileRead,
    ModelPrintJobRead,
    ModelListItem,
    ModelRead,
    ModelUpdate,
    TrashPurgeRead,
    TrashedModelRead,
    VaultStatsRead,
)
from app.services import model_views, storage, taxonomy
from app.services.ingestion import add_gcode_revision_to_model
from app.services.moonraker import MoonrakerClient, MoonrakerError
from app.services.trash import (
    hard_delete_expired_models,
    hard_delete_model,
    restore_model as trash_restore_model,
    soft_delete_model,
)

router = APIRouter(prefix="/models", tags=["models"])

_GCODE_SUFFIXES = {".gcode", ".g", ".gco"}


def _stage_gcode_upload(upload: UploadFile, suffix: str) -> Path:
    staged = settings.incoming_dir / f"{uuid.uuid4().hex}{suffix}"
    written = storage.stream_to_path(upload.file, staged)
    if written > settings.max_upload_bytes:
        staged.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="upload_too_large",
        )
    return staged


def _live_model(session: Session, model_id: int) -> Model:
    """Like ``get_or_404`` but also rejects soft-deleted rows."""
    m = session.get(Model, model_id)
    if m is None or m.deleted_at is not None:
        raise HTTPException(status_code=404, detail="model_not_found")
    return m


def _detail_or_404(session: Session, model_id: int) -> ModelRead:
    view = model_views.detail(session, model_id)
    if view is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    return view


@router.get(
    "",
    response_model=List[ModelListItem],
    summary="List models",
    description=(
        "List logical models with optional filtering. Soft-deleted models are excluded. "
        "Filter by collection (path prefix match, includes descendants), one or more tag "
        "slugs (AND semantics), and/or a name substring."
    ),
)
def list_models(
    collection: Optional[str] = Query(
        None, description="Collection path e.g. 'functional/brackets'"
    ),
    tag: Optional[List[str]] = Query(
        None, description="Tag slug; repeat for AND-filter"
    ),
    q: Optional[str] = Query(None, description="Substring match on name"),
    printer_id: Optional[int] = Query(
        None, description="Only models with a live G-code match on this printer"
    ),
    printer_presence: Optional[Literal["any", "none"]] = Query(
        None, description="Filter models by whether they exist on any printer"
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> List[ModelListItem]:
    return model_views.list_items(
        session,
        collection=collection,
        tags=tag,
        q=q,
        printer_id=printer_id,
        printer_presence=printer_presence,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/export",
    dependencies=[Depends(require_auth)],
    summary="Export library metadata",
    description=(
        "Exports the searchable PrintStash library metadata without raw model "
        "or G-code file blobs. Use JSON for portability/AI context and CSV for "
        "spreadsheet analysis."
    ),
)
def export_models(
    format: Literal["json", "csv"] = Query("json", description="Export format"),
    session: Session = Depends(get_session),
) -> Response:
    payload = model_views.export_payload(session)
    if format == "csv":
        return Response(
            content=model_views.export_csv(payload),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="printstash-model-export.csv"'
            },
        )
    # orjson handles datetimes natively and serialises this potentially large
    # payload several times faster than jsonable_encoder + stdlib json.
    import orjson

    return Response(
        content=orjson.dumps(payload),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="printstash-model-export.json"'
        },
    )


@router.get(
    "/stats",
    response_model=VaultStatsRead,
    summary="Vault library and storage summary",
    description=(
        "Returns live library counts plus real storage usage from the configured "
        "local or S3-compatible backend."
    ),
)
def vault_stats(session: Session = Depends(get_session)) -> VaultStatsRead:
    return model_views.vault_stats(session)


@router.get(
    "/trash",
    response_model=List[TrashedModelRead],
    dependencies=[Depends(require_auth)],
    summary="List models in the trash",
    description=(
        "Returns soft-deleted models. They are hidden from normal library browse "
        "but can be restored until they are permanently purged."
    ),
)
def list_trash(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> List[TrashedModelRead]:
    return model_views.list_trashed(
        session,
        limit=limit,
        offset=offset,
        retention_days=int(settings.trash_retention_days),
    )


@router.delete(
    "/trash/expired",
    response_model=TrashPurgeRead,
    dependencies=[Depends(require_auth)],
    summary="Permanently delete expired trash items",
)
def purge_expired_trash(session: Session = Depends(get_session)) -> TrashPurgeRead:
    purged_model_ids = hard_delete_expired_models(
        session,
        retention_days=int(settings.trash_retention_days),
    )
    session.commit()
    return TrashPurgeRead(
        purged_model_ids=purged_model_ids,
        purged_count=len(purged_model_ids),
    )


@router.get(
    "/{model_id}",
    response_model=ModelRead,
    summary="Get model detail with files and metadata",
)
def get_model(model_id: int, session: Session = Depends(get_session)) -> ModelRead:
    return _detail_or_404(session, model_id)


@router.post(
    "/{model_id}/gcode-revisions",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Add a G-code revision to an existing model",
    description=(
        "Uploads a new sliced G-code artifact directly onto the target model. "
        "Manual revisions default to needs_test unless another status is provided."
    ),
)
async def add_gcode_revision(
    model_id: int,
    file: UploadFile = UploadFileParam(..., description="The .gcode revision file"),
    revision_label: Optional[str] = Form(None, max_length=128),
    revision_status: Optional[FileRevisionStatus] = Form(FileRevisionStatus.NEEDS_TEST),
    revision_notes: Optional[str] = Form(None, max_length=4096),
    is_recommended: bool = Form(False),
    session: Session = Depends(get_session),
) -> ModelRead:
    model = _live_model(session, model_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")

    original_filename = Path(file.filename).name
    suffix = Path(original_filename).suffix.lower() or ".gcode"
    if suffix not in _GCODE_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_file_type")

    staged = await run_in_threadpool(_stage_gcode_upload, file, suffix)
    try:
        # Hashing + header parsing + thumbnail extraction are blocking file
        # I/O — keep them off the event loop.
        await run_in_threadpool(
            add_gcode_revision_to_model,
            session=session,
            model=model,
            staged_path=staged,
            original_filename=original_filename,
            revision_label=revision_label,
            revision_status=revision_status,
            revision_notes=revision_notes,
            is_recommended=is_recommended,
        )
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return _detail_or_404(session, model_id)


@router.get(
    "/{model_id}/printer-files",
    response_model=List[ModelPrinterFileRead],
    summary="List printers where this model's G-code files are present",
)
def get_model_printer_files(
    model_id: int, session: Session = Depends(get_session)
) -> List[ModelPrinterFileRead]:
    _live_model(session, model_id)
    rows = session.exec(
        select(PrinterFile, Printer)
        .join(File, File.id == PrinterFile.file_id)
        .join(Printer, Printer.id == PrinterFile.printer_id)
        .where(
            File.model_id == model_id,
            File.file_type == FileType.GCODE,
            live(Printer),
        )
        .order_by(Printer.name.asc(), PrinterFile.remote_filename.asc())  # type: ignore[attr-defined]
    ).all()
    return [
        ModelPrinterFileRead(
            file_id=row.file_id,  # type: ignore[arg-type]
            printer_id=printer.id,  # type: ignore[arg-type]
            printer_name=printer.name,
            remote_filename=row.remote_filename,
            matched_by=row.matched_by,
            last_seen_at=row.last_seen_at,
            missing_since=row.missing_since,
        )
        for row, printer in rows
    ]


def _gcode_revision_numbers(session: Session, model_id: int) -> dict[int, int]:
    """Derived 1-based revision numbers for live G-code files, by version."""
    gcode_files = session.exec(
        select(File)
        .where(File.model_id == model_id)
        .where(live(File))
        .where(File.file_type == FileType.GCODE)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    return {f.id: i for i, f in enumerate(gcode_files, start=1)}


@router.get(
    "/{model_id}/print-jobs",
    response_model=List[ModelPrintJobRead],
    summary="List recent print jobs for this model (print history)",
)
def get_model_print_jobs(
    model_id: int, session: Session = Depends(get_session)
) -> List[ModelPrintJobRead]:
    _live_model(session, model_id)
    revision_numbers = _gcode_revision_numbers(session, model_id)

    rows = session.exec(
        select(PrintJob, Printer, File, Metadata)
        .join(Printer, Printer.id == PrintJob.printer_id)
        .join(File, File.id == PrintJob.file_id)
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .where(
            PrintJob.model_id == model_id,
            live(PrintJob),
        )
        .order_by(PrintJob.created_at.desc())  # type: ignore[attr-defined]
        .limit(50)
    ).all()
    return [
        ModelPrintJobRead(
            id=job.id,  # type: ignore[arg-type]
            printer_id=job.printer_id,
            printer_name=printer.name,
            file_id=job.file_id,
            gcode_revision_number=revision_numbers.get(job.file_id),
            revision_label=file.revision_label,
            state=job.state,
            material_type=md.material_type if md else None,
            error=job.error,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
        )
        for job, printer, file, md in rows
    ]


@router.post(
    "/{model_id}/print-jobs",
    response_model=ModelPrintJobRead,
    dependencies=[Depends(require_auth)],
    summary="Manually log a print job for this model",
)
def create_manual_print_job(
    model_id: int,
    payload: ManualPrintJobCreate,
    session: Session = Depends(get_session),
) -> ModelPrintJobRead:
    _live_model(session, model_id)

    file_row = session.get(File, payload.file_id)
    if file_row is None or file_row.model_id != model_id:
        raise HTTPException(status_code=404, detail={"code": "file_not_found"})

    printer = session.get(Printer, payload.printer_id)
    if printer is None or printer.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "printer_not_found"})

    try:
        state = PrintJobState(payload.state)
    except ValueError:
        state = PrintJobState.COMPLETED

    job = PrintJob(
        printer_id=payload.printer_id,
        file_id=payload.file_id,
        model_id=model_id,
        remote_filename=file_row.original_filename,
        state=state,
        source="manual",
        started_at=payload.started_at,
        finished_at=payload.finished_at,
        error=payload.error,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    revision_numbers = _gcode_revision_numbers(session, model_id)

    return ModelPrintJobRead(
        id=job.id,
        printer_id=job.printer_id,
        printer_name=printer.name,
        file_id=job.file_id,
        gcode_revision_number=revision_numbers.get(job.file_id),
        revision_label=file_row.revision_label,
        state=job.state,
        material_type=None,
        error=job.error,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
    )


@router.post(
    "/{model_id}/print-jobs/import-printer/{printer_id}",
    response_model=List[ImportedPrintJobRead],
    dependencies=[Depends(require_auth)],
    summary="Fetch and import matching print history from a Moonraker printer",
)
async def import_print_jobs_from_printer(
    model_id: int,
    printer_id: int,
    session: Session = Depends(get_session),
) -> List[ImportedPrintJobRead]:
    _live_model(session, model_id)

    printer = session.get(Printer, printer_id)
    if printer is None or printer.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "printer_not_found"})
    if not printer.moonraker_url:
        raise HTTPException(status_code=400, detail={"code": "printer_no_url"})

    gcode_files = session.exec(
        select(File)
        .where(File.model_id == model_id)
        .where(live(File))
        .where(File.file_type == FileType.GCODE)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    filenames_to_file = {f.original_filename.lower(): f for f in gcode_files}

    client = MoonrakerClient(printer.moonraker_url, printer.api_key)
    try:
        history = await client.get_print_history(limit=100)
    except MoonrakerError as exc:
        raise HTTPException(
            status_code=502, detail={"code": "printer_unreachable", "detail": str(exc)}
        )

    existing_remote = {
        row[0]
        for row in session.exec(
            select(PrintJob.remote_filename)
            .where(PrintJob.printer_id == printer_id)
            .where(PrintJob.model_id == model_id)
            .where(live(PrintJob))
        ).all()
    }

    results: List[ImportedPrintJobRead] = []
    for entry in history:
        fname = entry.get("filename", "")
        matched = filenames_to_file.get(fname.lower())
        already_imported = fname in existing_remote

        if matched and not already_imported:
            raw_status = entry.get("status", "completed")
            state_map = {
                "completed": PrintJobState.COMPLETED,
                "cancelled": PrintJobState.CANCELLED,
                "error": PrintJobState.FAILED,
            }
            state = state_map.get(raw_status, PrintJobState.COMPLETED)
            start_ts = entry.get("start_time")
            end_ts = entry.get("end_time")

            from datetime import timezone
            from datetime import datetime as _dt

            def _ts(t: float | None):
                return _dt.fromtimestamp(t, tz=timezone.utc) if t else None

            job = PrintJob(
                printer_id=printer_id,
                file_id=matched.id,
                model_id=model_id,
                remote_filename=fname,
                state=state,
                source="printer_history",
                started_at=_ts(start_ts),
                finished_at=_ts(end_ts),
            )
            session.add(job)
            session.commit()

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

    return results


@router.patch(
    "/{model_id}",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Update a model's name, description, collection, or tags",
)
def update_model(
    model_id: int,
    payload: ModelUpdate,
    session: Session = Depends(get_session),
) -> ModelRead:
    m = _live_model(session, model_id)

    if payload.name is not None:
        m.name = payload.name.strip() or m.name
    if payload.description is not None:
        m.description = payload.description

    if payload.collection is not None:
        if payload.collection.strip() == "":
            m.collection_id = None
        else:
            cat = taxonomy.resolve_or_create_collection(session, payload.collection)
            if cat is not None:
                m.collection_id = cat.id

    if payload.tags is not None:
        session.exec(delete(ModelTagLink).where(ModelTagLink.model_id == model_id))  # type: ignore[call-overload]
        if payload.tags:
            new_tags = taxonomy.resolve_or_create_tags(session, payload.tags)
            for t in new_tags:
                session.add(ModelTagLink(model_id=model_id, tag_id=t.id))

    m.updated_at = utcnow()
    session.add(m)
    session.commit()
    return _detail_or_404(session, model_id)


@router.patch(
    "/{model_id}/files/{file_id}/revision",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Update G-code revision status, notes, or recommended marker",
    description=(
        "Updates G-code revision fields for a file under a model. Only G-code "
        "files are supported. Marking a file recommended clears the marker from "
        "other G-code files on the same model."
    ),
)
def update_file_revision(
    model_id: int,
    file_id: int,
    payload: FileRevisionUpdate,
    session: Session = Depends(get_session),
) -> ModelRead:
    m = _live_model(session, model_id)
    file_row = session.get(File, file_id)
    if (
        file_row is None
        or file_row.model_id != model_id
        or file_row.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="file_not_found")
    if file_row.file_type != FileType.GCODE:
        raise HTTPException(status_code=400, detail="revision_not_supported")

    fields = payload.model_fields_set
    if "revision_label" in fields:
        label = payload.revision_label
        file_row.revision_label = label.strip() if label and label.strip() else None
    if "revision_status" in fields:
        file_row.revision_status = payload.revision_status
    if "revision_notes" in fields:
        notes = payload.revision_notes
        file_row.revision_notes = notes.strip() if notes and notes.strip() else None
    if "is_recommended" in fields:
        file_row.is_recommended = bool(payload.is_recommended)
        if file_row.is_recommended:
            other_gcode = session.exec(
                select(File).where(
                    File.model_id == model_id,
                    File.id != file_id,
                    File.file_type == FileType.GCODE,
                    live(File),
                )
            ).all()
            for other in other_gcode:
                other.is_recommended = False
                session.add(other)

    m.updated_at = utcnow()
    session.add(file_row)
    session.add(m)
    session.commit()
    return _detail_or_404(session, model_id)


@router.post(
    "/{model_id}/restore",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Restore a model from the trash",
)
def restore_model(model_id: int, session: Session = Depends(get_session)) -> ModelRead:
    m = session.get(Model, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    trash_restore_model(session, m)
    return _detail_or_404(session, model_id)


@router.delete(
    "/{model_id}/purge",
    response_model=TrashPurgeRead,
    dependencies=[Depends(require_auth)],
    summary="Permanently delete a model from the trash",
)
def purge_model(
    model_id: int, session: Session = Depends(get_session)
) -> TrashPurgeRead:
    m = session.get(Model, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    if m.deleted_at is None:
        raise HTTPException(status_code=400, detail="model_not_in_trash")
    hard_delete_model(session, m)
    session.commit()
    return TrashPurgeRead(purged_model_ids=[model_id], purged_count=1)


@router.delete(
    "/{model_id}",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Soft-delete a model",
    description="Marks the model deleted; it moves to the trash until purged.",
)
def delete_model(model_id: int, session: Session = Depends(get_session)) -> Response:
    m = _live_model(session, model_id)
    soft_delete_model(session, m)
    return Response(status_code=204)
