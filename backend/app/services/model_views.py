"""Read-model assembly for the Model library.

The single owner of every Model → response-schema composition: browse list,
detail, metadata export, trash listing, and vault stats. Routers keep HTTP
concerns (auth, status codes, content negotiation) and delegate here.

All list-shaped reads batch their per-model lookups (one grouped/IN query
per facet) — N+1 regressions are bugs in this module, testable without HTTP.
"""

from __future__ import annotations

from collections import defaultdict
import csv
import io
from pathlib import Path
from typing import List, Literal, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    Collection,
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
from app.db.scopes import live, trashed
from app.schemas.models import (
    FileRead,
    MetadataRead,
    ModelListItem,
    ModelPrinterPresenceRead,
    ModelRead,
    PrintSummaryRead,
    StorageUsageRead,
    TrashedModelRead,
    VaultStatsRead,
)
from app.services.storage_backend import get_backend
from app.services.trash import trash_expires_at

_EXPORT_CSV_FIELDS = [
    "model_id",
    "model_name",
    "model_slug",
    "collection",
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


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


def tag_names_for(session: Session, model_id: int) -> List[str]:
    stmt = (
        select(Tag.name)
        .join(ModelTagLink, ModelTagLink.tag_id == Tag.id)
        .where(ModelTagLink.model_id == model_id)
        .order_by(Tag.name)
    )
    return list(session.exec(stmt).all())


def collection_name_for(model: Model) -> Optional[str]:
    """Resolve the collection name from the FK-joined relationship."""
    return model.collection_rel.path if model.collection_rel else None


def thumb_url(model: Model) -> Optional[str]:
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


def _normalise_profile_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().lower()
    return stripped or None


def _load_filament_profiles(session: Session) -> list[FilamentProfile]:
    """All filament profiles, loaded once per read-model composition.

    The table holds a handful of presets; matching in memory avoids the
    per-Metadata lookup queries that turned detail/export into N+1.
    """
    return list(session.exec(select(FilamentProfile)).all())


def _matching_filament_profile(
    profiles: list[FilamentProfile],
    metadata: Metadata,
) -> FilamentProfile | None:
    candidates = [
        _normalise_profile_key(metadata.material_brand),
        _normalise_profile_key(metadata.material_type),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        for profile in profiles:
            if _normalise_profile_key(profile.name) == candidate:
                return profile

    material_type = _normalise_profile_key(metadata.material_type)
    material_brand = _normalise_profile_key(metadata.material_brand)
    if material_type is None:
        return None

    for profile in profiles:
        if _normalise_profile_key(profile.material_type) != material_type:
            continue
        if material_brand is not None:
            if _normalise_profile_key(profile.material_brand) == material_brand:
                return profile
        elif profile.material_brand is None:
            return profile
    return None


def _live_file_metadata(session: Session) -> list[Metadata]:
    """Metadata rows attached to live (non-trashed) files."""
    return list(
        session.exec(
            select(Metadata).join(File, File.id == Metadata.file_id).where(live(File))
        ).all()
    )


def filament_profile_usage(session: Session) -> dict[int, int]:
    """Live-file count per filament profile id, using the same brand/type
    matching that drives cost estimates."""
    profiles = _load_filament_profiles(session)
    counts: dict[int, int] = defaultdict(int)
    for metadata in _live_file_metadata(session):
        profile = _matching_filament_profile(profiles, metadata)
        if profile is not None and profile.id is not None:
            counts[profile.id] += 1
    return dict(counts)


def printer_profile_usage(session: Session) -> dict[int, int]:
    """Live-file count per printer profile id, matched on preset name or
    bare printer model."""
    from app.db.models import PrinterProfile

    profiles = list(session.exec(select(PrinterProfile)).all())
    counts: dict[int, int] = defaultdict(int)
    for metadata in _live_file_metadata(session):
        key = _normalise_profile_key(metadata.printer_model)
        if key is None:
            continue
        for profile in profiles:
            if key in (
                _normalise_profile_key(profile.name),
                _normalise_profile_key(profile.printer_model),
            ):
                if profile.id is not None:
                    counts[profile.id] += 1
                break
    return dict(counts)


def metadata_read(
    session: Session,
    metadata: Metadata,
    profiles: list[FilamentProfile] | None = None,
) -> MetadataRead:
    data = metadata.model_dump()
    if profiles is None:
        profiles = _load_filament_profiles(session)
    profile = _matching_filament_profile(profiles, metadata)
    if profile and profile.cost_per_kg is not None and metadata.filament_weight_g:
        data["filament_cost"] = round(
            metadata.filament_weight_g * profile.cost_per_kg / 1000,
            4,
        )
    return MetadataRead(**data)


def _file_reads_with_revisions(
    session: Session, files_with_meta: list
) -> list[FileRead]:
    """Build FileReads with derived G-code revision numbers (1-based, by version)."""
    gcode_revision_numbers: dict[int, int] = {}
    gcode_index = 1
    for f, _md in files_with_meta:
        if f.file_type == FileType.GCODE and f.id is not None:
            gcode_revision_numbers[f.id] = gcode_index
            gcode_index += 1
    profiles = _load_filament_profiles(session)
    return [
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
            metadata=metadata_read(session, md, profiles) if md else None,
        )
        for f, md in files_with_meta
    ]


# ---------------------------------------------------------------------------
# Browse list
# ---------------------------------------------------------------------------


def list_items(
    session: Session,
    *,
    collection: Optional[str] = None,
    direct: bool = False,
    tags: Optional[List[str]] = None,
    q: Optional[str] = None,
    printer_id: Optional[int] = None,
    printer_presence: Optional[Literal["any", "none"]] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[ModelListItem]:
    """Filtered, paginated library browse with batched per-model facets."""
    stmt = select(Model).where(live(Model))

    present_model_ids = (
        select(File.model_id)
        .join(PrinterFile, PrinterFile.file_id == File.id)
        .join(Printer, Printer.id == PrinterFile.printer_id)
        .where(
            File.file_type == FileType.GCODE,
            live(File),
            live(Printer),
            PrinterFile.missing_since.is_(None),  # type: ignore[union-attr]
        )
    )

    if direct:
        # Show only direct children: root→NULL, collection→exact path match.
        if collection:
            cat_path = collection.strip().strip("/").lower()
            matching_cat_ids = select(Collection.id).where(Collection.path == cat_path)
            stmt = stmt.where(Model.collection_id.in_(matching_cat_ids))  # type: ignore[union-attr]
        else:
            stmt = stmt.where(Model.collection_id.is_(None))  # type: ignore[union-attr]
    elif collection:
        cat_path = collection.strip().strip("/").lower()
        # Match the collection path or any descendant via FK join on Collections.
        matching_cat_ids = select(Collection.id).where(
            (Collection.path == cat_path) | (Collection.path.startswith(cat_path + "/"))
        )
        stmt = stmt.where(Model.collection_id.in_(matching_cat_ids))  # type: ignore[union-attr]

    if q:
        stmt = stmt.where(Model.name.contains(q))  # type: ignore[attr-defined]

    if tags:
        for slug in (t.strip().lower() for t in tags if t.strip()):
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
    model_ids = [m.id for m in rows if m.id is not None]
    if not model_ids:
        return []

    # Batch the per-model lookups: one grouped/IN query per facet instead of
    # five queries per row (tags + collection already arrive via selectin).
    file_counts = dict(
        session.exec(
            select(File.model_id, func.count(File.id))
            .where(File.model_id.in_(model_ids))  # type: ignore[union-attr]
            .group_by(File.model_id)
        ).all()
    )

    # Newest mesh file per model, for client-side 3D preview preloading.
    mesh_file_ids: dict[int, int] = {}
    for model_id, file_id in session.exec(
        select(File.model_id, File.id)
        .where(
            File.model_id.in_(model_ids),  # type: ignore[union-attr]
            File.file_type.in_([FileType.STL, FileType.THREE_MF, FileType.OBJ]),  # type: ignore[attr-defined]
            live(File),
        )
        .order_by(File.model_id.asc(), File.version.desc())  # type: ignore[attr-defined]
    ).all():
        mesh_file_ids.setdefault(int(model_id), int(file_id))

    recommended: dict[int, tuple[FileRevisionStatus | None, str | None]] = {}
    for model_id, rev_status, rev_label in session.exec(
        select(File.model_id, File.revision_status, File.revision_label).where(
            File.model_id.in_(model_ids),  # type: ignore[union-attr]
            live(File),
            File.is_recommended == True,  # noqa: E712
        )
    ).all():
        recommended.setdefault(int(model_id), (rev_status, rev_label))

    presence_by_model: dict[int, list[ModelPrinterPresenceRead]] = defaultdict(list)
    for model_id, p_id, printer_name, file_count in session.exec(
        select(
            File.model_id,
            Printer.id,
            Printer.name,
            func.count(PrinterFile.id),
        )
        .join(PrinterFile, PrinterFile.file_id == File.id)
        .join(Printer, Printer.id == PrinterFile.printer_id)
        .where(
            File.model_id.in_(model_ids),  # type: ignore[union-attr]
            File.file_type == FileType.GCODE,
            live(File),
            live(Printer),
            PrinterFile.missing_since.is_(None),  # type: ignore[union-attr]
        )
        .group_by(File.model_id, Printer.id, Printer.name)
        .order_by(Printer.name.asc())  # type: ignore[attr-defined]
    ).all():
        presence_by_model[int(model_id)].append(
            ModelPrinterPresenceRead(
                printer_id=int(p_id),
                printer_name=printer_name,
                file_count=int(file_count or 0),
            )
        )

    summaries: dict[int, PrintSummaryRead] = {}
    for model_id, md in session.exec(
        select(File.model_id, Metadata)
        .join(File, File.id == Metadata.file_id)
        .where(
            File.model_id.in_(model_ids),  # type: ignore[union-attr]
            File.file_type == FileType.GCODE,
            live(File),
        )
        .order_by(File.model_id.asc(), File.uploaded_at.desc())  # type: ignore[attr-defined]
    ).all():
        # First row per model is the newest G-code file.
        if int(model_id) not in summaries:
            summaries[int(model_id)] = PrintSummaryRead(
                layer_height_mm=md.layer_height_mm,
                estimated_time_s=md.estimated_time_s,
                filament_weight_g=md.filament_weight_g,
                material_type=md.material_type,
                slicer_name=md.slicer_name,
            )

    out: List[ModelListItem] = []
    for m in rows:
        assert m.id is not None
        rec_status, rec_label = recommended.get(m.id, (None, None))
        out.append(
            ModelListItem(
                id=m.id,
                name=m.name,
                slug=m.slug,
                collection=collection_name_for(m),
                collection_id=m.collection_id,
                tags=sorted(t.name for t in m.tags),
                thumbnail_url=thumb_url(m),
                file_count=int(file_counts.get(m.id, 0)),
                mesh_file_id=mesh_file_ids.get(m.id),
                printer_presence=presence_by_model.get(m.id, []),
                updated_at=m.updated_at,
                print_summary=summaries.get(m.id),
                recommended_revision_status=rec_status,
                recommended_revision_label=rec_label,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def detail(session: Session, model_id: int) -> ModelRead | None:
    """Full model detail with files + metadata. None when missing or trashed."""
    m = session.get(Model, model_id)
    if m is None or m.deleted_at is not None:
        return None

    files_with_meta = session.exec(
        select(File, Metadata)
        .where(File.model_id == model_id)
        .where(live(File))
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()

    return ModelRead(
        id=m.id,  # type: ignore[arg-type]
        name=m.name,
        slug=m.slug,
        hash=m.hash,
        collection=collection_name_for(m),
        collection_id=m.collection_id,
        description=m.description,
        tags=tag_names_for(session, m.id),  # type: ignore[arg-type]
        thumbnail_url=thumb_url(m),
        created_at=m.created_at,
        updated_at=m.updated_at,
        files=_file_reads_with_revisions(session, files_with_meta),
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_payload(session: Session) -> dict:
    model_rows = session.exec(
        select(Model)
        .where(live(Model))
        .where(Model.hash != SENTINEL_MODEL_HASH)
        .order_by(Model.name.asc())  # type: ignore[attr-defined]
    ).all()
    model_ids = [m.id for m in model_rows if m.id is not None]
    if not model_ids:
        models = []
        file_count = 0
    else:
        collection_ids = {
            m.collection_id for m in model_rows if m.collection_id is not None
        }
        collections = {}
        if collection_ids:
            collections = {
                c.id: c.path
                for c in session.exec(
                    select(Collection).where(Collection.id.in_(collection_ids))  # type: ignore[union-attr]
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
        profiles = _load_filament_profiles(session)
        file_rows = session.exec(
            select(File, Metadata)
            .where(File.model_id.in_(model_ids))  # type: ignore[union-attr]
            .where(live(File))
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
                    metadata=metadata_read(session, metadata, profiles)
                    if metadata
                    else None,
                ).model_dump(mode="json")
            )

        models = [
            {
                "id": model.id,
                "name": model.name,
                "slug": model.slug,
                "hash": model.hash,
                "collection": collections.get(model.collection_id),
                "collection_id": model.collection_id,
                "description": model.description,
                "tags": tags_by_model.get(model.id or 0, []),
                "thumbnail_url": thumb_url(model),
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
                "collections",
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


def export_csv(payload: dict) -> str:
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
                    "collection": model.get("collection") or "",
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
                    "first_layer_height_mm": metadata.get("first_layer_height_mm")
                    or "",
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


# ---------------------------------------------------------------------------
# Trash listing
# ---------------------------------------------------------------------------


def list_trashed(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    retention_days: int,
) -> List[TrashedModelRead]:
    rows = session.exec(
        select(Model)
        .where(trashed(Model))
        .order_by(Model.deleted_at.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    ).all()
    model_ids = [m.id for m in rows if m.id is not None]
    file_stats: dict[int, tuple[int, int]] = {}
    if model_ids:
        for model_id, count, size in session.exec(
            select(
                File.model_id,
                func.count(File.id),
                func.coalesce(func.sum(File.size_bytes), 0),
            )
            .where(File.model_id.in_(model_ids))  # type: ignore[union-attr]
            .group_by(File.model_id)
        ).all():
            file_stats[int(model_id)] = (int(count or 0), int(size or 0))
    out: List[TrashedModelRead] = []
    for model in rows:
        file_count, size_bytes = file_stats.get(model.id or 0, (0, 0))
        assert model.deleted_at is not None
        out.append(
            TrashedModelRead(
                id=model.id,  # type: ignore[arg-type]
                name=model.name,
                slug=model.slug,
                collection=collection_name_for(model),
                tags=sorted(t.name for t in model.tags),
                thumbnail_url=thumb_url(model),
                file_count=file_count,
                size_bytes=size_bytes,
                deleted_at=model.deleted_at,
                expires_at=trash_expires_at(model.deleted_at, retention_days),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Vault stats
# ---------------------------------------------------------------------------


def vault_stats(session: Session) -> VaultStatsRead:
    live_model_ids = select(Model.id).where(
        live(Model),
        Model.hash != SENTINEL_MODEL_HASH,
    )
    live_files = (
        select(File).where(live(File)).where(File.model_id.in_(live_model_ids))  # type: ignore[union-attr]
    )

    model_count = session.exec(
        select(func.count()).select_from(live_model_ids.subquery())
    ).one()
    file_count = session.exec(
        select(func.count()).select_from(live_files.subquery())
    ).one()
    indexed_size = session.exec(
        select(func.coalesce(func.sum(File.size_bytes), 0))
        .where(live(File))
        .where(File.model_id.in_(live_model_ids))  # type: ignore[union-attr]
    ).one()
    source_file_count = session.exec(
        select(func.count(File.id))
        .where(live(File))
        .where(File.model_id.in_(live_model_ids))  # type: ignore[union-attr]
        .where(File.file_type != FileType.GCODE)
    ).one()
    gcode_file_count = session.exec(
        select(func.count(File.id))
        .where(live(File))
        .where(File.model_id.in_(live_model_ids))  # type: ignore[union-attr]
        .where(File.file_type == FileType.GCODE)
    ).one()
    collection_count = session.exec(
        select(func.count(Collection.id)).where(live(Collection))
    ).one()
    tag_count = session.exec(select(func.count(Tag.id)).where(live(Tag))).one()
    printer_count = session.exec(
        select(func.count(Printer.id)).where(live(Printer))
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
        collection_count=int(collection_count or 0),
        tag_count=int(tag_count or 0),
        printer_count=int(printer_count or 0),
        indexed_size_bytes=int(indexed_size or 0),
        storage=storage_usage,
    )
