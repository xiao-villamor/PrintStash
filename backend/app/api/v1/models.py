"""Model browse + detail + edit + soft-delete endpoints.

Read-model assembly lives in ``services/model_views``; trash lifecycle in
``services/trash``. This router keeps HTTP concerns only.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi import (
    File as UploadFileParam,
)
from fastapi.responses import FileResponse, Response
from sqlmodel import Session, delete, select
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.security import require_auth, require_superuser, require_user
from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionRole,
    FilamentProfile,
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelStar,
    ModelTagLink,
    Printer,
    PrinterFile,
    PrintJob,
    PrintJobState,
    Tag,
    User,
)
from app.db.scopes import live
from app.db.session import get_session
from app.schemas.models import (
    ArtifactOutcomeRead,
    FileRevisionUpdate,
    ImportedPrintJobRead,
    ManualPrintJobCreate,
    ModelBatchDelete,
    ModelBatchFailure,
    ModelBatchMove,
    ModelBatchResult,
    ModelBatchTags,
    ModelFacetsRead,
    ModelFilters,
    ModelListItem,
    ModelPrinterFileRead,
    ModelPrintJobRead,
    ModelRead,
    ModelUpdate,
    PrintStatisticsRead,
    RevisionBatchLabels,
    RevisionBatchResult,
    TrashedModelRead,
    TrashPurgeRead,
    VaultStatsRead,
)
from app.schemas.saved_views import ModelStarRead
from app.services import (
    job_import,
    library_transfer,
    model_views,
    print_results,
    rbac,
    storage,
    taxonomy,
)
from app.services.ingestion import add_gcode_revision_to_model
from app.services.moonraker import MoonrakerError
from app.services.trash import (
    hard_delete_expired_models,
    hard_delete_model,
    soft_delete_model,
    soft_delete_models,
)
from app.services.trash import (
    restore_model as trash_restore_model,
)

router = APIRouter(prefix="/models", tags=["models"])

_GCODE_SUFFIXES = {".gcode", ".g", ".gco", ".bgcode"}


def _stage_gcode_upload(upload: UploadFile, suffix: str) -> Path:
    staged = settings.incoming_dir / f"{uuid.uuid4().hex}{suffix}"
    try:
        storage.stream_to_path(
            upload.file, staged, max_bytes=settings.max_upload_bytes
        )
    except storage.UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="upload_too_large",
        ) from exc
    return staged


def _live_model(session: Session, model_id: int) -> Model:
    """Like ``get_or_404`` but also rejects soft-deleted rows."""
    m = session.exec(select(Model).where(Model.id == model_id, live(Model))).first()
    if m is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    return m


def _require_model_role(
    session: Session,
    user: User,
    model_id: int,
    minimum: CollectionRole,
) -> Model:
    model = _live_model(session, model_id)
    rbac.require_model_collection_role(session, user, model.collection_id, minimum)
    return model


def _detail_or_404(session: Session, model_id: int, user: User) -> ModelRead:
    view = model_views.detail(session, model_id, user)
    if view is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    return view


def _collection_path_for(raw_path: str) -> str:
    segments = [taxonomy.slugify(s.strip()) for s in raw_path.split("/") if s.strip()]
    return "/".join(segments)


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
    direct: bool = Query(
        False,
        description="Only return models directly in the collection (or at root if no collection)",
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
    favorites: bool = Query(False, description="Only models starred by current user"),
    file_type: Optional[List[FileType]] = Query(None),
    material_type: Optional[List[str]] = Query(None),
    slicer_name: Optional[List[str]] = Query(None),
    printer_model: Optional[List[str]] = Query(None),
    revision_status: Optional[List[FileRevisionStatus]] = Query(None),
    printed: Optional[bool] = Query(None),
    print_outcome: Optional[List[PrintJobState]] = Query(None),
    storage_filter: Optional[List[Literal["vault", "external"]]] = Query(
        None, alias="storage"
    ),
    uploaded_after: Optional[datetime] = Query(None),
    uploaded_before: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[ModelListItem]:
    if (
        printer_id is not None or printer_presence is not None
    ) and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="admin_required")
    filters = ModelFilters(
        collection=collection,
        direct=direct,
        tag=tag or [],
        q=q,
        printer_id=printer_id,
        printer_presence=printer_presence,
        favorites=favorites,
        file_type=file_type or [],
        material_type=material_type or [],
        slicer_name=slicer_name or [],
        printer_model=printer_model or [],
        revision_status=revision_status or [],
        printed=printed,
        print_outcome=print_outcome or [],
        storage=storage_filter or [],
        uploaded_after=uploaded_after,
        uploaded_before=uploaded_before,
    )
    return model_views.list_items(
        session,
        current_user,
        filters=filters,
        limit=limit,
        offset=offset,
    )


@router.get("/facets", response_model=ModelFacetsRead)
def model_facets(
    collection: Optional[str] = Query(None),
    direct: bool = Query(False),
    tag: Optional[List[str]] = Query(None),
    q: Optional[str] = Query(None),
    printer_id: Optional[int] = Query(None),
    printer_presence: Optional[Literal["any", "none"]] = Query(None),
    favorites: bool = Query(False),
    file_type: Optional[List[FileType]] = Query(None),
    material_type: Optional[List[str]] = Query(None),
    slicer_name: Optional[List[str]] = Query(None),
    printer_model: Optional[List[str]] = Query(None),
    revision_status: Optional[List[FileRevisionStatus]] = Query(None),
    printed: Optional[bool] = Query(None),
    print_outcome: Optional[List[PrintJobState]] = Query(None),
    storage_filter: Optional[List[Literal["vault", "external"]]] = Query(
        None, alias="storage"
    ),
    uploaded_after: Optional[datetime] = Query(None),
    uploaded_before: Optional[datetime] = Query(None),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelFacetsRead:
    if (
        printer_id is not None or printer_presence is not None
    ) and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="admin_required")
    return model_views.facets(
        session,
        current_user,
        ModelFilters(
            collection=collection,
            direct=direct,
            tag=tag or [],
            q=q,
            printer_id=printer_id,
            printer_presence=printer_presence,
            favorites=favorites,
            file_type=file_type or [],
            material_type=material_type or [],
            slicer_name=slicer_name or [],
            printer_model=printer_model or [],
            revision_status=revision_status or [],
            printed=printed,
            print_outcome=print_outcome or [],
            storage=storage_filter or [],
            uploaded_after=uploaded_after,
            uploaded_before=uploaded_before,
        ),
    )


@router.put(
    "/{model_id}/star",
    response_model=ModelStarRead,
    dependencies=[Depends(require_auth)],
)
def star_model(
    model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelStarRead:
    _require_model_role(session, current_user, model_id, CollectionRole.VIEW)
    existing = session.exec(
        select(ModelStar).where(
            ModelStar.user_id == current_user.id, ModelStar.model_id == model_id
        )
    ).first()
    if existing is None:
        session.add(ModelStar(user_id=current_user.id, model_id=model_id))
        session.commit()
    return ModelStarRead(model_id=model_id, starred=True)


@router.delete(
    "/{model_id}/star",
    response_model=ModelStarRead,
    dependencies=[Depends(require_auth)],
)
def unstar_model(
    model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelStarRead:
    _require_model_role(session, current_user, model_id, CollectionRole.VIEW)
    session.exec(
        delete(ModelStar).where(
            ModelStar.user_id == current_user.id, ModelStar.model_id == model_id
        )
    )
    session.commit()
    return ModelStarRead(model_id=model_id, starred=False)


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
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    payload = model_views.export_payload(session, current_user)
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
    "/library-archive",
    dependencies=[Depends(require_auth)],
    summary="Export a portable full-library archive",
)
def export_library_archive(
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> FileResponse:
    path = library_transfer.create_archive(session, current_user)
    return FileResponse(
        path,
        media_type="application/zip",
        filename="printstash-library-v1.zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post(
    "/library-import",
    dependencies=[Depends(require_superuser)],
    summary="Import a portable full-library archive",
)
async def import_library_archive(
    file: UploadFile = UploadFileParam(...),
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".zip":
        raise HTTPException(status_code=400, detail="archive_zip_required")
    fd, name = tempfile.mkstemp(suffix=".zip")
    try:
        with open(fd, "wb", closefd=True) as target:
            shutil.copyfileobj(file.file, target)
        return library_transfer.import_archive(session, Path(name), current_user)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        Path(name).unlink(missing_ok=True)


@router.get(
    "/stats",
    response_model=VaultStatsRead,
    summary="Vault library and storage summary",
    description=(
        "Returns live library counts plus real storage usage from the configured "
        "local or S3-compatible backend."
    ),
)
def vault_stats(
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> VaultStatsRead:
    return model_views.vault_stats(session, current_user)


@router.get(
    "/stats/prints",
    response_model=PrintStatisticsRead,
    dependencies=[Depends(require_superuser)],
    summary="Print analytics over a time window",
    description=(
        "Aggregates completed print jobs over a preset window (7d/30d/90d/1y/all): "
        "total cost and filament, average filament per print, total print time, "
        "and the collections and filaments with the most prints."
    ),
)
def print_stats(
    period: str = Query("30d", description="Preset window: 7d, 30d, 90d, 1y, or all"),
    session: Session = Depends(get_session),
) -> PrintStatisticsRead:
    return model_views.print_statistics(session, period)


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
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[TrashedModelRead]:
    return model_views.list_trashed(
        session,
        current_user,
        limit=limit,
        offset=offset,
        retention_days=int(settings.trash_retention_days),
    )


@router.delete(
    "/trash/expired",
    response_model=TrashPurgeRead,
    dependencies=[Depends(require_superuser)],
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
def get_model(
    model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelRead:
    return _detail_or_404(session, model_id, current_user)


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
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelRead:
    model = _require_model_role(session, current_user, model_id, CollectionRole.EDIT)
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
    return _detail_or_404(session, model_id, current_user)


@router.get(
    "/{model_id}/printer-files",
    response_model=List[ModelPrinterFileRead],
    summary="List printers where this model's G-code files are present",
)
def get_model_printer_files(
    model_id: int,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> List[ModelPrinterFileRead]:
    _require_model_role(session, current_user, model_id, CollectionRole.VIEW)
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
    model_id: int,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> List[ModelPrintJobRead]:
    _require_model_role(session, current_user, model_id, CollectionRole.VIEW)
    revision_numbers = _gcode_revision_numbers(session, model_id)
    profiles = list(session.exec(select(FilamentProfile)).all())

    rows = session.exec(
        select(PrintJob, Printer, File, Metadata)
        .outerjoin(Printer, Printer.id == PrintJob.printer_id)
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
            printer_name=(
                printer.name
                if printer is not None
                else (job.printer_name or "Unknown printer")
            ),
            file_id=job.file_id,
            gcode_revision_number=revision_numbers.get(job.file_id),
            revision_label=file.revision_label,
            state=job.state,
            material_type=md.material_type if md else None,
            error=job.error,
            filament_used_g=job.filament_used_g,
            actual_duration_s=job.actual_duration_s,
            filament_cost=model_views.filament_cost_for_job(
                profiles, md, job.filament_used_g, job.spool_filament_id
            ),
            spool_id=job.spool_id,
            spool_name=job.spool_name,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
        )
        for job, printer, file, md in rows
    ]


@router.get(
    "/{model_id}/artifact-outcomes",
    response_model=List[ArtifactOutcomeRead],
    summary="Compare actual print outcomes for Model Artifacts",
)
def get_artifact_outcomes(
    model_id: int,
    file_id: List[int] = Query(..., min_length=1, max_length=2),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[ArtifactOutcomeRead]:
    _require_model_role(session, current_user, model_id, CollectionRole.VIEW)
    rows = model_views.artifact_outcomes(session, model_id, file_id)
    if len(rows) != len(set(file_id)):
        raise HTTPException(status_code=404, detail="file_not_found")
    return [ArtifactOutcomeRead.model_validate(row) for row in rows]


@router.post(
    "/{model_id}/print-jobs",
    response_model=ModelPrintJobRead,
    dependencies=[Depends(require_superuser)],
    summary="Manually log a print job for this model",
)
def create_manual_print_job(
    model_id: int,
    payload: ManualPrintJobCreate,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> ModelPrintJobRead:
    _require_model_role(session, current_user, model_id, CollectionRole.EDIT)

    file_row = session.get(File, payload.file_id)
    if file_row is None or file_row.model_id != model_id:
        raise HTTPException(status_code=404, detail={"code": "file_not_found"})

    # Either a registered printer (by id) or an ad-hoc free-text printer name.
    printer: Optional[Printer] = None
    printer_name = payload.printer_name
    if payload.printer_id is not None:
        printer = session.get(Printer, payload.printer_id)
        if printer is None or printer.deleted_at is not None:
            raise HTTPException(status_code=404, detail={"code": "printer_not_found"})
        printer_name = None  # name is derived from the registered printer
    elif not printer_name:
        raise HTTPException(status_code=422, detail={"code": "printer_required"})

    try:
        state = PrintJobState(payload.state)
    except ValueError:
        state = PrintJobState.COMPLETED

    job = PrintJob(
        printer_id=payload.printer_id,
        printer_name=printer_name,
        file_id=payload.file_id,
        model_id=model_id,
        remote_filename=file_row.original_filename,
        state=state,
        source="manual",
        spool_id=payload.spool_id,
        spool_name=payload.spool_name,
        spool_filament_id=payload.spool_filament_id,
        started_at=payload.started_at,
        finished_at=payload.finished_at,
        error=payload.error,
    )
    if state == PrintJobState.COMPLETED:
        job.filament_g_effective, job.cost = print_results.resolve_completion_cost(
            session, job
        )
    session.add(job)
    session.commit()
    session.refresh(job)

    revision_numbers = _gcode_revision_numbers(session, model_id)

    return ModelPrintJobRead(
        id=job.id,
        printer_id=job.printer_id,
        printer_name=(
            printer.name if printer is not None else (printer_name or "Unknown printer")
        ),
        file_id=job.file_id,
        gcode_revision_number=revision_numbers.get(job.file_id),
        revision_label=file_row.revision_label,
        state=job.state,
        material_type=None,
        error=job.error,
        spool_id=job.spool_id,
        spool_name=job.spool_name,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
    )


@router.post(
    "/{model_id}/print-jobs/import-printer/{printer_id}",
    response_model=List[ImportedPrintJobRead],
    dependencies=[Depends(require_superuser)],
    summary="Fetch and import matching print history from a Moonraker printer",
)
async def import_print_jobs_from_printer(
    model_id: int,
    printer_id: int,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> List[ImportedPrintJobRead]:
    _require_model_role(session, current_user, model_id, CollectionRole.EDIT)

    printer = session.get(Printer, printer_id)
    if printer is None or printer.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "printer_not_found"})
    if not printer.moonraker_url:
        raise HTTPException(status_code=400, detail={"code": "printer_no_url"})

    try:
        return await job_import.import_print_jobs_from_printer(
            session,
            model_id=model_id,
            printer_id=printer_id,
            moonraker_url=printer.moonraker_url,
            moonraker_api_key=printer.api_key,
        )
    except MoonrakerError as exc:
        raise HTTPException(
            status_code=502, detail={"code": "printer_unreachable", "detail": str(exc)}
        ) from exc


def _dedupe_ids(ids: List[int]) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _partition_editable_models(
    session: Session, user: User, ids: List[int]
) -> tuple[List[Model], List[ModelBatchFailure]]:
    """Single-pass RBAC for batch ops: split ``ids`` into editable models and
    per-id failures, preserving input order.

    Replaces a per-model ``_require_model_role`` loop, which re-ran the same
    "all of this user's grants" query once per model (an N+1 that scaled with
    the selection). Here the models are fetched in one query and the editable
    collection set is computed once. Failure reasons match the single-model
    endpoint so the client sees the same ``reason`` strings.
    """
    rows = session.exec(
        select(Model).where(Model.id.in_(ids), live(Model))  # type: ignore[union-attr]
    ).all()
    by_id = {m.id: m for m in rows}
    # Superuser short-circuits inside accessible_collection_ids (returns every
    # collection), so a single call covers both roles.
    editable_ids = rbac.accessible_collection_ids(session, user, CollectionRole.EDIT)
    editable: List[Model] = []
    failed: List[ModelBatchFailure] = []
    for mid in ids:
        m = by_id.get(mid)
        if m is None:
            failed.append(ModelBatchFailure(model_id=mid, reason="model_not_found"))
        elif m.collection_id is None:
            if user.is_superuser:
                editable.append(m)
            else:
                failed.append(
                    ModelBatchFailure(
                        model_id=mid, reason="root_collection_admin_required"
                    )
                )
        elif m.collection_id in editable_ids:
            editable.append(m)
        else:
            failed.append(
                ModelBatchFailure(model_id=mid, reason="collection_permission_denied")
            )
    return editable, failed


def _require_all_editable_models(
    session: Session, user: User, ids: List[int]
) -> List[Model]:
    editable, failed = _partition_editable_models(session, user, _dedupe_ids(ids))
    if failed:
        status_code = 404 if failed[0].reason == "model_not_found" else 403
        raise HTTPException(status_code=status_code, detail=failed[0].reason)
    return editable


@router.post(
    "/batch/move",
    response_model=ModelBatchResult,
    dependencies=[Depends(require_auth)],
    summary="Move several models to a collection",
    description=(
        "Moves the given models into one destination collection. The destination "
        "is resolved once: an empty path means root (superuser only) and a missing "
        "path is created (superuser only). Every model is preflighted for existence "
        "and edit access; any failure rejects the whole request without writes."
    ),
)
def batch_move_models(
    payload: ModelBatchMove,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelBatchResult:
    # Validate the shared destination up front — these are whole-request errors,
    # not per-item, because the destination is the same for everyone. Creating a
    # *missing* collection is deferred until we know at least one model will move
    # (below), so a fully-failed batch never leaves an orphan empty collection.
    dest_is_root = payload.collection.strip() == ""
    if dest_is_root and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="root_collection_admin_required")

    existing_dest: Optional[Collection] = None
    if not dest_is_root:
        collection_path = _collection_path_for(payload.collection)
        existing_dest = session.exec(
            select(Collection).where(
                Collection.path == collection_path, live(Collection)
            )
        ).first()
        if existing_dest is None and not current_user.is_superuser:
            raise HTTPException(status_code=403, detail="collection_permission_denied")
        if existing_dest is not None:
            rbac.require_collection_role(
                session, current_user, existing_dest.id, CollectionRole.EDIT
            )

    editable = _require_all_editable_models(session, current_user, payload.model_ids)

    if dest_is_root:
        dest_id: Optional[int] = None
    elif existing_dest is not None:
        dest_id = existing_dest.id
    else:
        cat = taxonomy.resolve_or_create_collection_in_transaction(
            session, payload.collection
        )
        rbac.require_collection_role(session, current_user, cat.id, CollectionRole.EDIT)
        dest_id = cat.id

    succeeded: List[int] = []
    for m in editable:
        m.collection_id = dest_id
        m.updated_at = utcnow()
        session.add(m)
        succeeded.append(m.id)  # type: ignore[arg-type]

    session.commit()
    return ModelBatchResult(
        succeeded_ids=succeeded,
        failed=[],
        succeeded_count=len(succeeded),
        failed_count=0,
    )


@router.post(
    "/batch/tags",
    response_model=ModelBatchResult,
    dependencies=[Depends(require_auth)],
    summary="Add and/or remove tags on several models",
    description=(
        "Additive tag editing across a selection: tags in `add` are created if "
        "missing and appended (idempotent); tags in `remove` are detached if "
        "present. Each model keeps its other tags. Every model is preflighted; any "
        "missing or non-editable model rejects the whole request without writes."
    ),
)
def batch_tag_models(
    payload: ModelBatchTags,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelBatchResult:
    editable = _require_all_editable_models(session, current_user, payload.model_ids)

    add_tags = (
        taxonomy.resolve_or_create_tags_in_transaction(session, payload.add)
        if payload.add
        else []
    )
    # Removal only targets tags that already exist; never create on remove.
    remove_tag_ids: List[int] = []
    for raw in payload.remove:
        slug = taxonomy.slugify(raw.strip())
        if not slug:
            continue
        tag = session.exec(select(Tag).where(Tag.slug == slug, live(Tag))).first()
        if tag is not None and tag.id is not None:
            remove_tag_ids.append(tag.id)

    succeeded: List[int] = []
    for m in editable:
        model_id = m.id
        if add_tags:
            existing = set(
                session.exec(
                    select(ModelTagLink.tag_id).where(ModelTagLink.model_id == model_id)
                ).all()
            )
            for t in add_tags:
                if t.id not in existing:
                    session.add(ModelTagLink(model_id=model_id, tag_id=t.id))
        if remove_tag_ids:
            session.exec(
                delete(ModelTagLink).where(  # type: ignore[call-overload]
                    ModelTagLink.model_id == model_id,
                    ModelTagLink.tag_id.in_(remove_tag_ids),  # type: ignore[attr-defined]
                )
            )
        m.updated_at = utcnow()
        session.add(m)
        succeeded.append(model_id)

    session.commit()
    return ModelBatchResult(
        succeeded_ids=succeeded,
        failed=[],
        succeeded_count=len(succeeded),
        failed_count=0,
    )


@router.patch(
    "/batch/revision-labels",
    response_model=RevisionBatchResult,
    dependencies=[Depends(require_auth)],
    summary="Set the label on several G-code revisions",
)
def batch_set_revision_labels(
    payload: RevisionBatchLabels,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> RevisionBatchResult:
    file_ids = _dedupe_ids(payload.file_ids)
    rows = session.exec(
        select(File).where(File.id.in_(file_ids), live(File))  # type: ignore[union-attr]
    ).all()
    by_id = {row.id: row for row in rows}
    ordered: List[File] = []
    for file_id in file_ids:
        row = by_id.get(file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="file_not_found")
        if row.file_type != FileType.GCODE:
            raise HTTPException(status_code=400, detail="revision_not_supported")
        ordered.append(row)

    models_by_id = {
        model.id: model
        for model in _require_all_editable_models(
            session, current_user, [row.model_id for row in ordered]
        )
    }
    if any(row.model_id not in models_by_id for row in ordered):
        raise HTTPException(status_code=404, detail="model_not_found")

    try:
        model_views.set_revision_labels(session, ordered, payload.revision_label)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return RevisionBatchResult(succeeded_ids=file_ids, succeeded_count=len(file_ids))


@router.post(
    "/batch/delete",
    response_model=ModelBatchResult,
    dependencies=[Depends(require_auth)],
    summary="Soft-delete several models",
    description=(
        "Moves the given models to the trash. Every model is preflighted; any missing "
        "or non-editable model rejects the whole request without writes."
    ),
)
def batch_delete_models(
    payload: ModelBatchDelete,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelBatchResult:
    editable = _require_all_editable_models(session, current_user, payload.model_ids)
    soft_delete_models(session, editable)
    session.commit()
    return ModelBatchResult(
        succeeded_ids=[m.id for m in editable],  # type: ignore[misc]
        failed=[],
        succeeded_count=len(editable),
        failed_count=0,
    )


@router.patch(
    "/{model_id}",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Update a model's name, description, source URL, collection, or tags",
)
def update_model(
    model_id: int,
    payload: ModelUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelRead:
    m = _require_model_role(session, current_user, model_id, CollectionRole.EDIT)

    if payload.name is not None:
        m.name = payload.name.strip() or m.name
    if payload.description is not None:
        m.description = payload.description
    if "source_url" in payload.model_fields_set:
        m.source_url = payload.source_url

    if payload.collection is not None:
        if payload.collection.strip() == "":
            if not current_user.is_superuser:
                raise HTTPException(
                    status_code=403, detail="root_collection_admin_required"
                )
            m.collection_id = None
        else:
            collection_path = _collection_path_for(payload.collection)
            cat = session.exec(
                select(Collection).where(
                    Collection.path == collection_path, live(Collection)
                )
            ).first()
            if cat is None:
                if not current_user.is_superuser:
                    raise HTTPException(
                        status_code=403, detail="collection_permission_denied"
                    )
                cat = taxonomy.resolve_or_create_collection(session, payload.collection)
            if cat is not None:
                rbac.require_collection_role(
                    session,
                    current_user,
                    cat.id,
                    CollectionRole.EDIT,
                )
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
    return _detail_or_404(session, model_id, current_user)


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
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelRead:
    m = _require_model_role(session, current_user, model_id, CollectionRole.EDIT)
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
    return _detail_or_404(session, model_id, current_user)


@router.delete(
    "/{model_id}/files/{file_id}/revision",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Delete a G-code revision",
    description=(
        "Soft-deletes a G-code revision file. The blob is reclaimed by the "
        "trash GC. Only G-code files are supported."
    ),
)
def delete_file_revision(
    model_id: int,
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelRead:
    m = _require_model_role(session, current_user, model_id, CollectionRole.EDIT)
    file_row = session.get(File, file_id)
    if (
        file_row is None
        or file_row.model_id != model_id
        or file_row.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="file_not_found")
    if file_row.file_type != FileType.GCODE:
        raise HTTPException(status_code=400, detail="revision_not_supported")

    was_recommended = file_row.is_recommended
    file_row.deleted_at = utcnow()
    file_row.deleted_by = current_user.id
    file_row.is_recommended = False

    # Invariant: a model with G-code always keeps exactly one recommended
    # revision (CONTEXT.md). If we just removed the recommended one, promote the
    # newest remaining live G-code revision so the model never ends up holding
    # G-code with nothing recommended.
    if was_recommended:
        replacement = session.exec(
            select(File)
            .where(
                File.model_id == model_id,
                File.id != file_id,
                File.file_type == FileType.GCODE,
                live(File),
            )
            .order_by(File.version.desc())  # type: ignore[attr-defined]
        ).first()
        if replacement is not None:
            replacement.is_recommended = True
            session.add(replacement)

    # Drop a stale thumbnail pointer if it referenced this revision.
    if m.thumbnail_file_id == file_id:
        m.thumbnail_file_id = None
        m.thumbnail_path = None

    m.updated_at = utcnow()
    session.add(file_row)
    session.add(m)
    session.commit()
    return _detail_or_404(session, model_id, current_user)


@router.post(
    "/{model_id}/restore",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Restore a model from the trash",
)
def restore_model(
    model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ModelRead:
    m = session.get(Model, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    rbac.require_model_collection_role(
        session,
        current_user,
        m.collection_id,
        CollectionRole.EDIT,
    )
    trash_restore_model(session, m)
    return _detail_or_404(session, model_id, current_user)


@router.delete(
    "/{model_id}/purge",
    response_model=TrashPurgeRead,
    dependencies=[Depends(require_auth)],
    summary="Permanently delete a model from the trash",
)
def purge_model(
    model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> TrashPurgeRead:
    m = session.get(Model, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    if m.deleted_at is None:
        raise HTTPException(status_code=400, detail="model_not_in_trash")
    rbac.require_model_collection_role(
        session,
        current_user,
        m.collection_id,
        CollectionRole.EDIT,
    )
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
def delete_model(
    model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    m = _require_model_role(session, current_user, model_id, CollectionRole.EDIT)
    soft_delete_model(session, m)
    return Response(status_code=204)
