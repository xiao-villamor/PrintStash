"""Model browse + detail + edit + soft-delete endpoints."""

from __future__ import annotations

from collections import defaultdict
import csv
import io
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
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func
from sqlmodel import Session, delete, select
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.security import require_auth
from app.core.time import utcnow
from app.db.models import (
    Category,
    File,
    FileRevisionStatus,
    FileType,
    FilamentProfile,
    Metadata,
    Model,
    ModelTagLink,
    Printer,
    PrinterFile,
    SENTINEL_MODEL_HASH,
    Tag,
)
from app.db.session import get_session
from app.schemas.models import (
    FileRead,
    FileRevisionUpdate,
    MetadataRead,
    ModelPrinterPresenceRead,
    ModelPrinterFileRead,
    ModelListItem,
    ModelRead,
    ModelUpdate,
    StorageUsageRead,
    VaultStatsRead,
)
from app.services import storage
from app.services.ingestion import add_gcode_revision_to_model
from app.services import taxonomy
from app.services.storage_backend import get_backend

router = APIRouter(prefix="/models", tags=["models"])

_GCODE_SUFFIXES = {".gcode", ".g", ".gco"}
_EXPORT_CSV_FIELDS = [
    "model_id",
    "model_name",
    "model_slug",
    "category",
    "tags",
    "file_id",
    "file_type",
    "version",
    "original_filename",
    "size_bytes",
    "sha256",
    "revision_label",
    "revision_status",
    "revision_notes",
    "is_recommended",
    "uploaded_at",
    "slicer_name",
    "slicer_version",
    "printer_model",
    "nozzle_diameter_mm",
    "layer_height_mm",
    "first_layer_height_mm",
    "infill_percent",
    "wall_loops",
    "top_shell_layers",
    "bottom_shell_layers",
    "support_material",
    "nozzle_temperature_c",
    "bed_temperature_c",
    "estimated_time_s",
    "filament_weight_g",
    "filament_length_mm",
    "filament_cost",
    "material_type",
    "material_brand",
    "bbox_x_mm",
    "bbox_y_mm",
    "bbox_z_mm",
    "volume_mm3",
    "triangle_count",
]


def _tag_names_for(session: Session, model_id: int) -> List[str]:
    stmt = (
        select(Tag.name)
        .join(ModelTagLink, ModelTagLink.tag_id == Tag.id)
        .where(ModelTagLink.model_id == model_id)
        .order_by(Tag.name)
    )
    return list(session.exec(stmt).all())


def _category_name_for(model: Model) -> Optional[str]:
    """Resolve the category name from the FK-joined relationship."""
    return model.category_rel.path if model.category_rel else None


def _thumb_url(model: Model) -> Optional[str]:
    """Stable URL for the model's thumbnail, or None.

    Prefers ``thumbnail_file_id`` (current); falls back to parsing the legacy
    ``thumbnail_path`` for rows written before the file-id column existed.
    """
    if model.thumbnail_file_id:
        return f"/api/v1/files/{model.thumbnail_file_id}/thumbnail"
    if model.thumbnail_path:
        stem = Path(model.thumbnail_path).stem
        if stem.isdigit():
            return f"/api/v1/files/{stem}/thumbnail"
    return None


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


def _printer_presence_for(
    session: Session, model_id: int
) -> list[ModelPrinterPresenceRead]:
    rows = session.exec(
        select(
            Printer.id,
            Printer.name,
            func.count(PrinterFile.id),
        )
        .join(PrinterFile, PrinterFile.printer_id == Printer.id)
        .join(File, File.id == PrinterFile.file_id)
        .where(
            File.model_id == model_id,
            File.file_type == FileType.GCODE,
            File.deleted_at.is_(None),  # type: ignore[union-attr]
            Printer.deleted_at.is_(None),  # type: ignore[union-attr]
            PrinterFile.missing_since.is_(None),  # type: ignore[union-attr]
        )
        .group_by(Printer.id, Printer.name)
        .order_by(Printer.name.asc())  # type: ignore[attr-defined]
    ).all()
    return [
        ModelPrinterPresenceRead(
            printer_id=int(printer_id),
            printer_name=printer_name,
            file_count=int(file_count or 0),
        )
        for printer_id, printer_name, file_count in rows
    ]


def _normalise_profile_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().lower()
    return stripped or None


def _matching_filament_profile(
    session: Session,
    metadata: Metadata,
) -> FilamentProfile | None:
    candidates = [
        _normalise_profile_key(metadata.material_brand),
        _normalise_profile_key(metadata.material_type),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        profile = session.exec(
            select(FilamentProfile).where(func.lower(FilamentProfile.name) == candidate)
        ).first()
        if profile is not None:
            return profile

    material_type = _normalise_profile_key(metadata.material_type)
    material_brand = _normalise_profile_key(metadata.material_brand)
    if material_type is None:
        return None

    stmt = select(FilamentProfile).where(
        func.lower(FilamentProfile.material_type) == material_type
    )
    if material_brand is not None:
        stmt = stmt.where(func.lower(FilamentProfile.material_brand) == material_brand)
    else:
        stmt = stmt.where(FilamentProfile.material_brand.is_(None))
    return session.exec(stmt).first()


def _metadata_read(session: Session, metadata: Metadata) -> MetadataRead:
    data = metadata.model_dump()
    if data.get("filament_cost") is None and metadata.filament_weight_g:
        profile = _matching_filament_profile(session, metadata)
        if profile and profile.cost_per_kg is not None:
            data["filament_cost"] = round(
                metadata.filament_weight_g * profile.cost_per_kg / 1000,
                4,
            )
    return MetadataRead(**data)


@router.get(
    "",
    response_model=List[ModelListItem],
    summary="List models",
    description=(
        "List logical models with optional filtering. Soft-deleted models are excluded. "
        "Filter by category (path prefix match, includes descendants), one or more tag "
        "slugs (AND semantics), and/or a name substring."
    ),
)
def list_models(
    category: Optional[str] = Query(
        None, description="Category path e.g. 'functional/brackets'"
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
    stmt = select(Model).where(Model.deleted_at.is_(None))  # type: ignore[union-attr]

    present_model_ids = (
        select(File.model_id)
        .join(PrinterFile, PrinterFile.file_id == File.id)
        .join(Printer, Printer.id == PrinterFile.printer_id)
        .where(
            File.file_type == FileType.GCODE,
            File.deleted_at.is_(None),  # type: ignore[union-attr]
            Printer.deleted_at.is_(None),  # type: ignore[union-attr]
            PrinterFile.missing_since.is_(None),  # type: ignore[union-attr]
        )
    )

    if category:
        cat_path = category.strip().strip("/").lower()
        # Match the category path or any descendant via FK join on Categories.
        matching_cat_ids = select(Category.id).where(
            (Category.path == cat_path) | (Category.path.startswith(cat_path + "/"))
        )
        stmt = stmt.where(Model.category_id.in_(matching_cat_ids))  # type: ignore[union-attr]

    if q:
        stmt = stmt.where(Model.name.contains(q))  # type: ignore[attr-defined]

    if tag:
        for slug in (t.strip().lower() for t in tag if t.strip()):
            # Each tag adds an EXISTS clause => AND semantics across tags.
            stmt = stmt.where(
                Model.id.in_(  # type: ignore[union-attr]
                    select(ModelTagLink.model_id)
                    .join(Tag, Tag.id == ModelTagLink.tag_id)
                    .where(Tag.slug == slug)
                )
            )

    if printer_id is not None:
        stmt = stmt.where(
            Model.id.in_(present_model_ids.where(PrinterFile.printer_id == printer_id))  # type: ignore[union-attr]
        )
    elif printer_presence == "any":
        stmt = stmt.where(Model.id.in_(present_model_ids))  # type: ignore[union-attr]
    elif printer_presence == "none":
        stmt = stmt.where(Model.id.not_in(present_model_ids))  # type: ignore[attr-defined]

    stmt = stmt.order_by(Model.updated_at.desc()).offset(offset).limit(limit)  # type: ignore[attr-defined]
    rows = session.exec(stmt).all()

    out: List[ModelListItem] = []
    for m in rows:
        file_count = session.exec(
            select(func.count(File.id)).where(File.model_id == m.id)
        ).one()
        out.append(
            ModelListItem(
                id=m.id,  # type: ignore[arg-type]
                name=m.name,
                slug=m.slug,
                category=_category_name_for(m),
                category_id=m.category_id,
                tags=_tag_names_for(session, m.id),  # type: ignore[arg-type]
                thumbnail_url=_thumb_url(m),
                file_count=int(file_count or 0),
                printer_presence=_printer_presence_for(session, m.id),  # type: ignore[arg-type]
                updated_at=m.updated_at,
            )
        )
    return out


def _build_model_read(session: Session, model_id: int) -> ModelRead:
    m = _live_model(session, model_id)

    files_with_meta = session.exec(
        select(File, Metadata)
        .where(File.model_id == model_id)
        .where(File.deleted_at.is_(None))  # type: ignore[union-attr]
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    gcode_revision_numbers: dict[int, int] = {}
    gcode_index = 1
    for f, _md in files_with_meta:
        if f.file_type == FileType.GCODE and f.id is not None:
            gcode_revision_numbers[f.id] = gcode_index
            gcode_index += 1
    file_reads = [
        FileRead(
            id=f.id,  # type: ignore[arg-type]
            model_id=f.model_id,
            original_filename=f.original_filename,
            file_type=f.file_type,
            version=f.version,
            gcode_revision_number=gcode_revision_numbers.get(f.id),
            size_bytes=f.size_bytes,
            sha256=f.sha256,
            revision_label=f.revision_label,
            revision_status=f.revision_status,
            revision_notes=f.revision_notes,
            is_recommended=f.is_recommended,
            uploaded_at=f.uploaded_at,
            metadata=_metadata_read(session, md) if md else None,
        )
        for f, md in files_with_meta
    ]

    return ModelRead(
        id=m.id,  # type: ignore[arg-type]
        name=m.name,
        slug=m.slug,
        hash=m.hash,
        category=_category_name_for(m),
        category_id=m.category_id,
        description=m.description,
        tags=_tag_names_for(session, m.id),  # type: ignore[arg-type]
        thumbnail_url=_thumb_url(m),
        created_at=m.created_at,
        updated_at=m.updated_at,
        files=file_reads,
    )


def _export_models(session: Session) -> dict:
    model_rows = session.exec(
        select(Model)
        .where(Model.deleted_at.is_(None))  # type: ignore[union-attr]
        .where(Model.hash != SENTINEL_MODEL_HASH)
        .order_by(Model.name.asc())  # type: ignore[attr-defined]
    ).all()
    model_ids = [m.id for m in model_rows if m.id is not None]
    if not model_ids:
        models = []
        file_count = 0
    else:
        category_ids = {
            m.category_id for m in model_rows if m.category_id is not None
        }
        categories = {}
        if category_ids:
            categories = {
                c.id: c.path
                for c in session.exec(
                    select(Category).where(Category.id.in_(category_ids))  # type: ignore[union-attr]
                ).all()
                if c.id is not None
            }

        tags_by_model: dict[int, list[str]] = defaultdict(list)
        tag_rows = session.exec(
            select(ModelTagLink.model_id, Tag.name)
            .join(Tag, Tag.id == ModelTagLink.tag_id)
            .where(ModelTagLink.model_id.in_(model_ids))  # type: ignore[union-attr]
            .order_by(ModelTagLink.model_id.asc(), Tag.name.asc())  # type: ignore[attr-defined]
        ).all()
        for model_id, tag_name in tag_rows:
            if model_id is not None:
                tags_by_model[int(model_id)].append(tag_name)

        files_by_model: dict[int, list[dict]] = defaultdict(list)
        gcode_counts: dict[int, int] = defaultdict(int)
        file_rows = session.exec(
            select(File, Metadata)
            .where(File.model_id.in_(model_ids))  # type: ignore[union-attr]
            .where(File.deleted_at.is_(None))  # type: ignore[union-attr]
            .outerjoin(Metadata, Metadata.file_id == File.id)
            .order_by(File.model_id.asc(), File.version.asc())  # type: ignore[attr-defined]
        ).all()
        for file_row, metadata in file_rows:
            gcode_revision_number = None
            if file_row.file_type == FileType.GCODE:
                gcode_counts[file_row.model_id] += 1
                gcode_revision_number = gcode_counts[file_row.model_id]
            files_by_model[file_row.model_id].append(
                FileRead(
                    id=file_row.id,  # type: ignore[arg-type]
                    model_id=file_row.model_id,
                    original_filename=file_row.original_filename,
                    file_type=file_row.file_type,
                    version=file_row.version,
                    gcode_revision_number=gcode_revision_number,
                    size_bytes=file_row.size_bytes,
                    sha256=file_row.sha256,
                    revision_label=file_row.revision_label,
                    revision_status=file_row.revision_status,
                    revision_notes=file_row.revision_notes,
                    is_recommended=file_row.is_recommended,
                    uploaded_at=file_row.uploaded_at,
                    metadata=_metadata_read(session, metadata) if metadata else None,
                ).model_dump(mode="json")
            )

        models = [
            {
                "id": model.id,
                "name": model.name,
                "slug": model.slug,
                "hash": model.hash,
                "category": categories.get(model.category_id),
                "category_id": model.category_id,
                "description": model.description,
                "tags": tags_by_model.get(model.id or 0, []),
                "thumbnail_url": _thumb_url(model),
                "created_at": model.created_at.isoformat(),
                "updated_at": model.updated_at.isoformat(),
                "files": files_by_model.get(model.id or 0, []),
            }
            for model in model_rows
        ]
        file_count = sum(len(model["files"]) for model in models)

    return {
        "export_version": 1,
        "app": {"name": settings.app_name, "version": settings.app_version},
        "generated_at": utcnow().isoformat(),
        "contents": {
            "kind": "metadata_only",
            "includes": [
                "models",
                "categories",
                "tags",
                "stored file metadata",
                "slicer/mesh metadata",
                "G-code revision labels and outcomes",
            ],
            "excludes": ["raw STL/3MF/G-code blobs", "secrets", "printer credentials"],
        },
        "counts": {"models": len(models), "files": file_count},
        "models": models,
    }


def _export_models_csv(payload: dict) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=_EXPORT_CSV_FIELDS)
    writer.writeheader()
    for model in payload["models"]:
        tags = ",".join(model["tags"])
        for file_row in model["files"]:
            metadata = file_row.get("metadata") or {}
            writer.writerow(
                {
                    "model_id": model["id"],
                    "model_name": model["name"],
                    "model_slug": model["slug"],
                    "category": model.get("category") or "",
                    "tags": tags,
                    "file_id": file_row["id"],
                    "file_type": file_row["file_type"],
                    "version": file_row["version"],
                    "original_filename": file_row["original_filename"],
                    "size_bytes": file_row["size_bytes"],
                    "sha256": file_row["sha256"],
                    "revision_label": file_row.get("revision_label") or "",
                    "revision_status": file_row.get("revision_status") or "",
                    "revision_notes": file_row.get("revision_notes") or "",
                    "is_recommended": file_row["is_recommended"],
                    "uploaded_at": file_row["uploaded_at"],
                    "slicer_name": metadata.get("slicer_name") or "",
                    "slicer_version": metadata.get("slicer_version") or "",
                    "printer_model": metadata.get("printer_model") or "",
                    "nozzle_diameter_mm": metadata.get("nozzle_diameter_mm") or "",
                    "layer_height_mm": metadata.get("layer_height_mm") or "",
                    "first_layer_height_mm": metadata.get("first_layer_height_mm") or "",
                    "infill_percent": metadata.get("infill_percent") or "",
                    "wall_loops": metadata.get("wall_loops") or "",
                    "top_shell_layers": metadata.get("top_shell_layers") or "",
                    "bottom_shell_layers": metadata.get("bottom_shell_layers") or "",
                    "support_material": metadata.get("support_material")
                    if metadata.get("support_material") is not None
                    else "",
                    "nozzle_temperature_c": metadata.get("nozzle_temperature_c") or "",
                    "bed_temperature_c": metadata.get("bed_temperature_c") or "",
                    "estimated_time_s": metadata.get("estimated_time_s") or "",
                    "filament_weight_g": metadata.get("filament_weight_g") or "",
                    "filament_length_mm": metadata.get("filament_length_mm") or "",
                    "filament_cost": metadata.get("filament_cost") or "",
                    "material_type": metadata.get("material_type") or "",
                    "material_brand": metadata.get("material_brand") or "",
                    "bbox_x_mm": metadata.get("bbox_x_mm") or "",
                    "bbox_y_mm": metadata.get("bbox_y_mm") or "",
                    "bbox_z_mm": metadata.get("bbox_z_mm") or "",
                    "volume_mm3": metadata.get("volume_mm3") or "",
                    "triangle_count": metadata.get("triangle_count") or "",
                }
            )
    return out.getvalue()


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
    payload = _export_models(session)
    if format == "csv":
        return Response(
            content=_export_models_csv(payload),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="printstash-model-export.csv"'
            },
        )
    return JSONResponse(
        content=jsonable_encoder(payload),
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
    live_model_ids = select(Model.id).where(
        Model.deleted_at.is_(None),  # type: ignore[union-attr]
        Model.hash != SENTINEL_MODEL_HASH,
    )
    live_files = (
        select(File)
        .where(File.deleted_at.is_(None))  # type: ignore[union-attr]
        .where(File.model_id.in_(live_model_ids))  # type: ignore[union-attr]
    )

    model_count = session.exec(select(func.count()).select_from(live_model_ids.subquery())).one()
    file_count = session.exec(select(func.count()).select_from(live_files.subquery())).one()
    indexed_size = session.exec(
        select(func.coalesce(func.sum(File.size_bytes), 0))
        .where(File.deleted_at.is_(None))  # type: ignore[union-attr]
        .where(File.model_id.in_(live_model_ids))  # type: ignore[union-attr]
    ).one()
    source_file_count = session.exec(
        select(func.count(File.id))
        .where(File.deleted_at.is_(None))  # type: ignore[union-attr]
        .where(File.model_id.in_(live_model_ids))  # type: ignore[union-attr]
        .where(File.file_type != FileType.GCODE)
    ).one()
    gcode_file_count = session.exec(
        select(func.count(File.id))
        .where(File.deleted_at.is_(None))  # type: ignore[union-attr]
        .where(File.model_id.in_(live_model_ids))  # type: ignore[union-attr]
        .where(File.file_type == FileType.GCODE)
    ).one()
    category_count = session.exec(
        select(func.count(Category.id)).where(Category.deleted_at.is_(None))  # type: ignore[union-attr]
    ).one()
    tag_count = session.exec(
        select(func.count(Tag.id)).where(Tag.deleted_at.is_(None))  # type: ignore[union-attr]
    ).one()
    printer_count = session.exec(
        select(func.count(Printer.id)).where(Printer.deleted_at.is_(None))  # type: ignore[union-attr]
    ).one()

    try:
        storage_usage = StorageUsageRead(**get_backend().usage())
    except Exception as exc:
        storage_usage = StorageUsageRead(
            backend=settings.storage_backend,
            ok=False,
            error=exc.__class__.__name__,
        )

    return VaultStatsRead(
        model_count=int(model_count or 0),
        file_count=int(file_count or 0),
        source_file_count=int(source_file_count or 0),
        gcode_file_count=int(gcode_file_count or 0),
        category_count=int(category_count or 0),
        tag_count=int(tag_count or 0),
        printer_count=int(printer_count or 0),
        indexed_size_bytes=int(indexed_size or 0),
        storage=storage_usage,
    )


@router.get(
    "/{model_id}",
    response_model=ModelRead,
    summary="Get model detail with files and metadata",
)
def get_model(model_id: int, session: Session = Depends(get_session)) -> ModelRead:
    return _build_model_read(session, model_id)


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
        add_gcode_revision_to_model(
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
    return _build_model_read(session, model_id)


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
            Printer.deleted_at.is_(None),  # type: ignore[union-attr]
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


@router.patch(
    "/{model_id}",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Update a model's name, description, category, or tags",
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

    if payload.category is not None:
        if payload.category.strip() == "":
            m.category_id = None
        else:
            cat = taxonomy.resolve_or_create_category(session, payload.category)
            if cat is not None:
                m.category_id = cat.id

    if payload.tags is not None:
        session.exec(delete(ModelTagLink).where(ModelTagLink.model_id == model_id))  # type: ignore[call-overload]
        if payload.tags:
            new_tags = taxonomy.resolve_or_create_tags(session, payload.tags)
            for t in new_tags:
                session.add(ModelTagLink(model_id=model_id, tag_id=t.id))

    m.updated_at = utcnow()
    session.add(m)
    session.commit()
    return _build_model_read(session, model_id)


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
                    File.deleted_at.is_(None),  # type: ignore[union-attr]
                )
            ).all()
            for other in other_gcode:
                other.is_recommended = False
                session.add(other)

    m.updated_at = utcnow()
    session.add(file_row)
    session.add(m)
    session.commit()
    return _build_model_read(session, model_id)


@router.delete(
    "/{model_id}",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Soft-delete a model",
    description=(
        "Marks the model deleted. Files remain on disk; Stage 4 will introduce "
        "hard delete + GC."
    ),
)
def delete_model(model_id: int, session: Session = Depends(get_session)) -> Response:
    m = _live_model(session, model_id)
    m.deleted_at = utcnow()
    session.add(m)
    session.commit()
    return Response(status_code=204)
