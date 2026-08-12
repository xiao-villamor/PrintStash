"""Read-model assembly for the Model library.

The single owner of every Model → response-schema composition: browse list,
detail, metadata export, trash listing, and vault stats. Routers keep HTTP
concerns (auth, status codes, content negotiation) and delegate here.

All list-shaped reads batch their per-model lookups (one grouped/IN query
per facet) — N+1 regressions are bugs in this module, testable without HTTP.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, List, Literal, Optional

from sqlalchemy import Float, String, case, cast, func, literal, union_all
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    SENTINEL_MODEL_HASH,
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
from app.db.scopes import live, trashed
from app.schemas.models import (
    CollectionStatRead,
    FacetValueRead,
    FilamentStatRead,
    FileRead,
    MetadataRead,
    ModelFacetsRead,
    ModelFilters,
    ModelListItem,
    ModelPageRead,
    ModelPrinterPresenceRead,
    ModelRead,
    ModelSort,
    ModelStatRead,
    OutlinerModelRead,
    PrinterStatRead,
    PrintStatisticsRead,
    PrintSummaryRead,
    StorageUsageRead,
    TimeBucketRead,
    TrashedModelRead,
    VaultStatsRead,
)
from app.services import rbac
from app.services.storage_backend import get_backend
from app.services.trash import trash_expires_at


def set_revision_labels(
    session: Session, files: list[File], revision_label: str | None
) -> None:
    """Set one label across prevalidated live G-code revisions, without commit."""
    label = revision_label.strip() if revision_label and revision_label.strip() else None
    touched_models: set[int] = set()
    for file_row in files:
        file_row.revision_label = label
        session.add(file_row)
        touched_models.add(file_row.model_id)
    for model in session.exec(
        select(Model).where(Model.id.in_(touched_models), live(Model))  # type: ignore[union-attr]
    ).all():
        model.updated_at = utcnow()
        session.add(model)

_EXPORT_CSV_FIELDS = [
    "model_id",
    "model_name",
    "model_slug",
    "model_source_url",
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


def _apply_model_access(stmt, session: Session, user: User):
    if user.is_superuser:
        return stmt
    collection_ids = rbac.accessible_collection_ids(session, user, CollectionRole.VIEW)
    if not collection_ids:
        return stmt.where(Model.id == -1)
    return stmt.where(Model.collection_id.in_(collection_ids))  # type: ignore[union-attr]


def accessible_live_model_ids_stmt(session: Session, user: User):
    """Reusable SQL scope for accessible live library Models.

    Large exports must keep this as a subquery instead of first materializing
    every Model response (or a Python ID list). That keeps RBAC identical to the
    normal read models without paying for their Artifact/Metadata composition.
    """
    stmt = select(Model.id).where(
        live(Model),
        Model.hash != SENTINEL_MODEL_HASH,
    )
    return _apply_model_access(stmt, session, user)


def _effective_model_role(
    session: Session,
    user: User,
    model: Model,
) -> CollectionRole | None:
    return rbac.effective_collection_role(session, user, model.collection_id)


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


def filament_cost_for_grams(
    profiles: list[FilamentProfile],
    metadata: Metadata | None,
    grams: float | None,
) -> float | None:
    """Cost of *grams* of filament using the profile matching *metadata*.

    Used for measured per-print cost (PrintJob.filament_used_g). Returns None
    when grams, a matching profile, or its cost_per_kg is missing.
    """
    if grams is None or metadata is None:
        return None
    profile = _matching_filament_profile(profiles, metadata)
    if profile is None or profile.cost_per_kg is None:
        return None
    return round(grams * profile.cost_per_kg / 1000, 4)


def filament_cost_for_job(
    profiles: list[FilamentProfile],
    metadata: Metadata | None,
    grams: float | None,
    spool_filament_id: int | None,
) -> float | None:
    """Per-print cost, preferring the exact synced spool over metadata matching.

    When a Spoolman spool was selected, its synced FilamentProfile gives the
    exact cost; otherwise fall back to the fuzzy metadata match.
    """
    if grams is not None and spool_filament_id is not None:
        for profile in profiles:
            if (
                profile.spoolman_filament_id == spool_filament_id
                and profile.cost_per_kg is not None
            ):
                return round(grams * profile.cost_per_kg / 1000, 4)
    return filament_cost_for_grams(profiles, metadata, grams)


def _matching_filament_profile_by_fields(
    profiles: list[FilamentProfile],
    material_brand: str | None,
    material_type: str | None,
) -> FilamentProfile | None:
    candidates = [
        _normalise_profile_key(material_brand),
        _normalise_profile_key(material_type),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        for profile in profiles:
            if _normalise_profile_key(profile.name) == candidate:
                return profile

    norm_type = _normalise_profile_key(material_type)
    norm_brand = _normalise_profile_key(material_brand)
    if norm_type is None:
        return None

    for profile in profiles:
        if _normalise_profile_key(profile.material_type) != norm_type:
            continue
        if norm_brand is not None:
            if _normalise_profile_key(profile.material_brand) == norm_brand:
                return profile
        elif profile.material_brand is None:
            return profile
    return None


def _matching_filament_profile(
    profiles: list[FilamentProfile],
    metadata: Metadata,
) -> FilamentProfile | None:
    return _matching_filament_profile_by_fields(
        profiles, metadata.material_brand, metadata.material_type
    )


def filament_profile_usage(session: Session) -> dict[int, int]:
    """Live-file count per filament profile id, using the same brand/type
    matching that drives cost estimates.

    Selects only the two matched columns instead of hydrating full Metadata
    rows — this scans every live file's metadata, so the row stays as thin as
    the matching logic allows.
    """
    profiles = _load_filament_profiles(session)
    counts: dict[int, int] = defaultdict(int)
    rows = session.exec(
        select(Metadata.material_brand, Metadata.material_type)
        .join(File, File.id == Metadata.file_id)
        .where(live(File))
    ).all()
    for material_brand, material_type in rows:
        profile = _matching_filament_profile_by_fields(
            profiles, material_brand, material_type
        )
        if profile is not None and profile.id is not None:
            counts[profile.id] += 1
    return dict(counts)


def printer_profile_usage(session: Session) -> dict[int, int]:
    """Live-file count per printer profile id, matched on preset name or
    bare printer model. Selects only the matched column (see
    ``filament_profile_usage``)."""
    from app.db.models import PrinterProfile

    profiles = list(session.exec(select(PrinterProfile)).all())
    counts: dict[int, int] = defaultdict(int)
    rows = session.exec(
        select(Metadata.printer_model)
        .join(File, File.id == Metadata.file_id)
        .where(live(File))
    ).all()
    for printer_model in rows:
        key = _normalise_profile_key(printer_model)
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
            is_external=f.is_external,
            uploaded_at=f.uploaded_at,
            metadata=metadata_read(session, md, profiles) if md else None,
        )
        for f, md in files_with_meta
    ]


# ---------------------------------------------------------------------------
# Browse list
# ---------------------------------------------------------------------------


def _apply_structured_filters(stmt, filters: ModelFilters):
    artifact_filters = any(
        (
            filters.file_type,
            filters.material_type,
            filters.slicer_name,
            filters.printer_model,
            filters.revision_status,
            filters.storage,
            filters.uploaded_after,
            filters.uploaded_before,
        )
    )
    if artifact_filters:
        artifact_ids = select(File.model_id).where(live(File))
        metadata_filters = any(
            (filters.material_type, filters.slicer_name, filters.printer_model)
        )
        if metadata_filters:
            artifact_ids = artifact_ids.join(Metadata, Metadata.file_id == File.id)
        if filters.file_type:
            artifact_ids = artifact_ids.where(File.file_type.in_(filters.file_type))  # type: ignore[union-attr]
        if filters.revision_status:
            artifact_ids = artifact_ids.where(
                File.revision_status.in_(filters.revision_status)  # type: ignore[union-attr]
            )
        if len(set(filters.storage)) == 1:
            artifact_ids = artifact_ids.where(
                File.is_external == (filters.storage[0] == "external")
            )
        if filters.uploaded_after:
            artifact_ids = artifact_ids.where(File.uploaded_at >= filters.uploaded_after)
        if filters.uploaded_before:
            artifact_ids = artifact_ids.where(File.uploaded_at <= filters.uploaded_before)
        if filters.material_type:
            artifact_ids = artifact_ids.where(
                func.lower(Metadata.material_type).in_(value.lower() for value in filters.material_type)
            )
        if filters.slicer_name:
            artifact_ids = artifact_ids.where(
                func.lower(Metadata.slicer_name).in_(value.lower() for value in filters.slicer_name)
            )
        if filters.printer_model:
            artifact_ids = artifact_ids.where(
                func.lower(Metadata.printer_model).in_(value.lower() for value in filters.printer_model)
            )
        stmt = stmt.where(Model.id.in_(artifact_ids))  # type: ignore[union-attr]

    live_jobs = select(PrintJob.model_id).where(live(PrintJob))
    if filters.printed is True:
        stmt = stmt.where(Model.id.in_(live_jobs))  # type: ignore[union-attr]
    elif filters.printed is False:
        stmt = stmt.where(Model.id.not_in(live_jobs))  # type: ignore[attr-defined]
    if filters.print_outcome:
        matching_jobs = select(PrintJob.model_id).where(
            live(PrintJob), PrintJob.state.in_(filters.print_outcome)  # type: ignore[union-attr]
        )
        stmt = stmt.where(Model.id.in_(matching_jobs))  # type: ignore[union-attr]
    return stmt


def _filtered_stmt(session: Session, user: User, filters: ModelFilters):
    stmt = select(Model).where(live(Model), Model.hash != SENTINEL_MODEL_HASH)
    stmt = _apply_model_access(stmt, session, user)
    if filters.favorites:
        stmt = stmt.where(
            Model.id.in_(  # type: ignore[union-attr]
                select(ModelStar.model_id).where(ModelStar.user_id == user.id)
            )
        )
    if filters.direct:
        if filters.collection:
            cat_path = filters.collection.strip().strip("/").lower()
            stmt = stmt.where(
                Model.collection_id.in_(  # type: ignore[union-attr]
                    select(Collection.id).where(Collection.path == cat_path)
                )
            )
        else:
            stmt = stmt.where(Model.collection_id.is_(None))  # type: ignore[union-attr]
    elif filters.collection:
        cat_path = filters.collection.strip().strip("/").lower()
        matching = select(Collection.id).where(
            (Collection.path == cat_path) | (Collection.path.startswith(cat_path + "/"))
        )
        stmt = stmt.where(Model.collection_id.in_(matching))  # type: ignore[union-attr]
    if filters.q:
        stmt = stmt.where(Model.name.ilike(f"%{filters.q}%"))  # type: ignore[attr-defined]
    for slug in (tag.strip().lower() for tag in filters.tag if tag.strip()):
        stmt = stmt.where(
            Model.id.in_(  # type: ignore[union-attr]
                select(ModelTagLink.model_id)
                .join(Tag, Tag.id == ModelTagLink.tag_id)
                .where(Tag.slug == slug)
            )
        )
    stmt = _apply_structured_filters(stmt, filters)
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
    if filters.printer_id is not None:
        stmt = stmt.where(
            Model.id.in_(present_model_ids.where(PrinterFile.printer_id == filters.printer_id))  # type: ignore[union-attr]
        )
    elif filters.printer_presence == "any":
        stmt = stmt.where(Model.id.in_(present_model_ids))  # type: ignore[union-attr]
    elif filters.printer_presence == "none":
        stmt = stmt.where(Model.id.not_in(present_model_ids))  # type: ignore[attr-defined]
    return stmt


def facets(session: Session, user: User, filters: ModelFilters) -> ModelFacetsRead:
    """Accessible live facet values in one database round-trip.

    The union keeps each dimension independently grouped while avoiding the
    row multiplication that a single wide File/Metadata/PrintJob join would
    introduce. This matters on large libraries and preserves distinct-Model
    counts for every value.
    """
    filtered = (
        _filtered_stmt(session, user, filters)
        .with_only_columns(Model.id)
        .cte("facet_models")
    )
    files = (
        select(
            File.id.label("file_id"),
            File.model_id,
            File.file_type,
            File.revision_status,
            File.is_external,
        )
        .join(filtered, filtered.c.id == File.model_id)
        .where(live(File))
        .cte("facet_files")
    )
    metadata = (
        select(
            files.c.model_id,
            Metadata.material_type,
            Metadata.slicer_name,
            Metadata.printer_model,
        )
        .join(Metadata, Metadata.file_id == files.c.file_id)
        .cte("facet_metadata")
    )
    jobs = (
        select(PrintJob.model_id, PrintJob.state)
        .join(filtered, filtered.c.id == PrintJob.model_id)
        .where(live(PrintJob))
        .cte("facet_jobs")
    )

    def grouped_branch(facet: str, value, model_id, *, where=()):
        return (
            select(
                literal(facet).label("facet"),
                cast(value, String).label("value"),
                func.count(func.distinct(model_id)).label("count"),
            )
            .where(value.is_not(None), cast(value, String) != "", *where)
            .group_by(value)
        )

    branches = [
        grouped_branch("file_type", files.c.file_type, files.c.model_id),
        grouped_branch("material_type", metadata.c.material_type, metadata.c.model_id),
        grouped_branch("slicer_name", metadata.c.slicer_name, metadata.c.model_id),
        grouped_branch("printer_model", metadata.c.printer_model, metadata.c.model_id),
        grouped_branch(
            "revision_status", files.c.revision_status, files.c.model_id
        ),
        grouped_branch(
            "print_outcome",
            jobs.c.state,
            jobs.c.model_id,
            where=(
                jobs.c.state.in_(
                    [
                        PrintJobState.COMPLETED,
                        PrintJobState.FAILED,
                        PrintJobState.CANCELLED,
                    ]
                ),
            ),
        ),
        grouped_branch(
            "storage",
            case((files.c.is_external.is_(True), "external"), else_="vault"),
            files.c.model_id,
        ),
        select(
            literal("printed").label("facet"),
            literal("yes").label("value"),
            func.count(func.distinct(jobs.c.model_id)).label("count"),
        ),
        select(
            literal("printed_total").label("facet"),
            literal("total").label("value"),
            func.count(filtered.c.id).label("count"),
        ),
    ]
    combined = union_all(*branches).subquery("facet_values")
    rows = session.execute(
        select(combined.c.facet, combined.c.value, combined.c.count).order_by(
            combined.c.facet.asc(), combined.c.value.asc()
        )
    ).all()

    enum_values = {
        "file_type": {member.name: member.value for member in FileType},
        "revision_status": {
            member.name: member.value for member in FileRevisionStatus
        },
        "print_outcome": {member.name: member.value for member in PrintJobState},
    }
    values: dict[str, list[FacetValueRead]] = defaultdict(list)
    printed_count = 0
    total = 0
    for facet, raw_value, count in rows:
        if facet == "printed_total":
            total = int(count or 0)
            continue
        value = str(raw_value)
        value = enum_values.get(str(facet), {}).get(value, value)
        item = FacetValueRead(value=value, count=int(count or 0))
        values[str(facet)].append(item)
        if facet == "printed" and value == "yes":
            printed_count = item.count
    values["printed"].append(
        FacetValueRead(value="no", count=max(0, total - printed_count))
    )

    return ModelFacetsRead(
        file_type=values["file_type"],
        material_type=values["material_type"],
        slicer_name=values["slicer_name"],
        printer_model=values["printer_model"],
        revision_status=values["revision_status"],
        print_outcome=values["print_outcome"],
        storage=values["storage"],
        printed=values["printed"],
    )


def _hydrate_list_rows(
    session: Session, user: User, rows: list[Model]
) -> list[ModelListItem]:
    """Compose one already-ordered Model page with bounded batch queries."""
    model_ids = [m.id for m in rows if m.id is not None]
    if not model_ids:
        return []
    starred_ids = set(
        session.exec(
            select(ModelStar.model_id).where(
                ModelStar.user_id == user.id,
                ModelStar.model_id.in_(model_ids),  # type: ignore[union-attr]
            )
        ).all()
    )

    file_counts = dict(
        session.exec(
            select(File.model_id, func.count(File.id))
            .where(File.model_id.in_(model_ids), live(File))  # type: ignore[union-attr]
            .group_by(File.model_id)
        ).all()
    )

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
    if user.is_superuser:
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
        .order_by(
            File.model_id.asc(), File.uploaded_at.desc(), File.id.desc()  # type: ignore[attr-defined]
        )
    ).all():
        if int(model_id) not in summaries:
            summaries[int(model_id)] = PrintSummaryRead(
                layer_height_mm=md.layer_height_mm,
                estimated_time_s=md.estimated_time_s,
                filament_weight_g=md.filament_weight_g,
                material_type=md.material_type,
                slicer_name=md.slicer_name,
            )

    for model_id, completed, decided, last_printed, average_duration, total_cost in session.exec(
        select(
            PrintJob.model_id,
            func.sum(case((PrintJob.state == PrintJobState.COMPLETED, 1), else_=0)),
            func.sum(case((PrintJob.state.in_([PrintJobState.COMPLETED, PrintJobState.FAILED]), 1), else_=0)),
            func.max(PrintJob.finished_at),
            func.avg(PrintJob.actual_duration_s),
            func.sum(PrintJob.cost),
        )
        .where(PrintJob.model_id.in_(model_ids), live(PrintJob))  # type: ignore[union-attr]
        .group_by(PrintJob.model_id)
    ).all():
        summary = summaries.setdefault(int(model_id), PrintSummaryRead())
        summary.success_rate = float(completed or 0) / int(decided) if decided else None
        summary.last_printed_at = ensure_utc(last_printed) if last_printed else None
        summary.average_duration_s = float(average_duration) if average_duration is not None else None
        summary.total_cost = float(total_cost) if total_cost is not None else None

    roles = rbac.effective_roles_for_collections(
        session, user, (m.collection_id for m in rows)
    )
    out: list[ModelListItem] = []
    for model in rows:
        assert model.id is not None
        rec_status, rec_label = recommended.get(model.id, (None, None))
        out.append(
            ModelListItem(
                id=model.id,
                name=model.name,
                slug=model.slug,
                collection=collection_name_for(model),
                collection_id=model.collection_id,
                source_url=model.source_url,
                effective_role=roles.get(model.collection_id),
                tags=sorted(tag.name for tag in model.tags),
                thumbnail_url=thumb_url(model),
                file_count=int(file_counts.get(model.id, 0)),
                mesh_file_id=mesh_file_ids.get(model.id),
                printer_presence=presence_by_model.get(model.id, []),
                updated_at=model.updated_at,
                print_summary=summaries.get(model.id),
                recommended_revision_status=rec_status,
                recommended_revision_label=rec_label,
                starred=model.id in starred_ids,
            )
        )
    return out


def list_items(
    session: Session,
    user: User,
    *,
    collection: Optional[str] = None,
    direct: bool = False,
    tags: Optional[List[str]] = None,
    q: Optional[str] = None,
    printer_id: Optional[int] = None,
    printer_presence: Optional[Literal["any", "none"]] = None,
    favorites: bool = False,
    filters: ModelFilters | None = None,
    limit: int = 50,
    offset: int = 0,
) -> List[ModelListItem]:
    """Filtered, paginated library browse with batched per-model facets."""
    # Exclude the external-job sentinel model — it's internal bookkeeping for
    # print jobs that don't map to a real vault model and must never surface in
    # the library grid (vault_stats/export already exclude it, which is why the
    # header count and the grid would otherwise disagree).
    filters = filters or ModelFilters(
        collection=collection,
        direct=direct,
        tag=tags or [],
        q=q,
        printer_id=printer_id,
        printer_presence=printer_presence,
        favorites=favorites,
    )
    stmt = _filtered_stmt(session, user, filters)

    # Model.id is the stable tiebreaker: without it, models sharing an
    # updated_at (e.g. a batch ZIP import) sort non-deterministically, so
    # pagination can repeat or skip rows across page boundaries.
    stmt = (
        stmt.order_by(Model.updated_at.desc(), Model.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    rows = session.exec(stmt).all()
    return _hydrate_list_rows(session, user, list(rows))


def _job_sort_stats():
    completed = func.sum(
        case((PrintJob.state == PrintJobState.COMPLETED, 1), else_=0)
    )
    decided = func.sum(
        case(
            (
                PrintJob.state.in_([PrintJobState.COMPLETED, PrintJobState.FAILED]),
                1,
            ),
            else_=0,
        )
    )
    return (
        select(
            PrintJob.model_id.label("model_id"),
            (cast(completed, Float) / func.nullif(decided, 0)).label("success_rate"),
            func.max(PrintJob.finished_at).label("last_printed_at"),
            func.avg(PrintJob.actual_duration_s).label("average_duration_s"),
            func.sum(PrintJob.cost).label("total_cost"),
        )
        .where(live(PrintJob))
        .group_by(PrintJob.model_id)
        .subquery("browse_job_stats")
    )


def _latest_gcode_metadata():
    ranked = (
        select(
            File.model_id.label("model_id"),
            Metadata.estimated_time_s.label("estimated_time_s"),
            Metadata.filament_weight_g.label("filament_weight_g"),
            func.row_number()
            .over(
                partition_by=File.model_id,
                order_by=(File.uploaded_at.desc(), File.id.desc()),  # type: ignore[attr-defined]
            )
            .label("row_number"),
        )
        .join(Metadata, Metadata.file_id == File.id)
        .where(File.file_type == FileType.GCODE, live(File))
        .subquery("ranked_gcode_metadata")
    )
    return (
        select(
            ranked.c.model_id,
            ranked.c.estimated_time_s,
            ranked.c.filament_weight_g,
        )
        .where(ranked.c.row_number == 1)
        .subquery("latest_gcode_metadata")
    )


def _sort_value_and_statement(stmt, sort: ModelSort):
    if sort in (ModelSort.DATE_DESC, ModelSort.DATE_ASC):
        return stmt, Model.updated_at
    if sort in (ModelSort.NAME_ASC, ModelSort.NAME_DESC):
        return stmt, func.lower(Model.name)
    if sort == ModelSort.FILAMENT_ASC:
        metadata = _latest_gcode_metadata()
        return (
            stmt.outerjoin(metadata, metadata.c.model_id == Model.id),
            metadata.c.filament_weight_g,
        )

    jobs = _job_sort_stats()
    stmt = stmt.outerjoin(jobs, jobs.c.model_id == Model.id)
    if sort == ModelSort.SUCCESS_DESC:
        return stmt, jobs.c.success_rate
    if sort == ModelSort.PRINTED_DESC:
        return stmt, jobs.c.last_printed_at
    if sort == ModelSort.COST_ASC:
        return stmt, jobs.c.total_cost

    metadata = _latest_gcode_metadata()
    stmt = stmt.outerjoin(metadata, metadata.c.model_id == Model.id)
    if sort == ModelSort.DURATION_ASC:
        return stmt, func.coalesce(
            jobs.c.average_duration_s, metadata.c.estimated_time_s
        )
    raise ValueError(f"unsupported_model_sort:{sort}")


def _encode_model_cursor(
    sort: ModelSort,
    value: object,
    model_id: int,
    total: int,
    filter_key: str,
) -> str:
    encoded_value = value
    if isinstance(value, datetime):
        encoded_value = ensure_utc(value).isoformat()
    payload = json.dumps(
        {
            "sort": sort.value,
            "value": encoded_value,
            "id": model_id,
            "total": total,
            "filters": filter_key,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_model_cursor(
    cursor: str, sort: ModelSort, filter_key: str
) -> tuple[object, int, int]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if payload.get("sort") != sort.value or payload.get("filters") != filter_key:
            raise ValueError
        value = payload["value"]
        model_id = int(payload["id"])
        total = int(payload["total"])
        if model_id <= 0 or total < 0:
            raise ValueError
        if value is not None and sort in (
            ModelSort.DATE_DESC,
            ModelSort.DATE_ASC,
            ModelSort.PRINTED_DESC,
        ):
            value = datetime.fromisoformat(str(value))
        elif value is not None and sort not in (
            ModelSort.NAME_ASC,
            ModelSort.NAME_DESC,
        ):
            value = float(value)
        return value, model_id, total
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_model_cursor") from exc


def _model_filter_key(filters: ModelFilters, user: User) -> str:
    payload = json.dumps(
        {
            "filters": filters.model_dump(mode="json"),
            "user_id": user.id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _apply_model_cursor(
    stmt,
    expression,
    sort: ModelSort,
    cursor: tuple[object, int, int] | None,
):
    descending = sort in (
        ModelSort.DATE_DESC,
        ModelSort.NAME_DESC,
        ModelSort.SUCCESS_DESC,
        ModelSort.PRINTED_DESC,
    )
    if cursor:
        value, model_id, _total = cursor
        id_after = Model.id < model_id if descending else Model.id > model_id
        if value is None:
            stmt = stmt.where(expression.is_(None), id_after)
        else:
            value_after = expression < value if descending else expression > value
            stmt = stmt.where(
                value_after
                | ((expression == value) & id_after)
                | expression.is_(None)
            )
    null_rank = case((expression.is_(None), 1), else_=0)
    order_value = expression.desc() if descending else expression.asc()
    order_id = Model.id.desc() if descending else Model.id.asc()  # type: ignore[attr-defined]
    return stmt.order_by(null_rank.asc(), order_value, order_id)


def page_items(
    session: Session,
    user: User,
    *,
    filters: ModelFilters,
    sort: ModelSort = ModelSort.DATE_DESC,
    cursor: str | None = None,
    limit: int = 60,
) -> ModelPageRead:
    """Globally sorted keyset page; the browser never drains the full library."""
    filtered = _filtered_stmt(session, user, filters)
    filter_key = _model_filter_key(filters, user)
    decoded_cursor = (
        _decode_model_cursor(cursor, sort, filter_key) if cursor else None
    )
    if decoded_cursor is None:
        total = int(
            session.exec(
                select(func.count()).select_from(
                    filtered.with_only_columns(Model.id).subquery()
                )
            ).one()
        )
    else:
        total = decoded_cursor[2]
    stmt, sort_value = _sort_value_and_statement(filtered, sort)
    stmt = _apply_model_cursor(stmt, sort_value, sort, decoded_cursor)
    raw_rows = session.execute(stmt.add_columns(sort_value).limit(limit + 1)).all()
    has_more = len(raw_rows) > limit
    page_rows = raw_rows[:limit]
    models = [row[0] for row in page_rows]
    next_cursor = None
    if has_more and page_rows:
        last_model, last_value = page_rows[-1]
        next_cursor = _encode_model_cursor(
            sort, last_value, last_model.id, total, filter_key
        )
    return ModelPageRead(
        items=_hydrate_list_rows(session, user, models),
        next_cursor=next_cursor,
        total=total,
    )


def outliner_items(
    session: Session,
    user: User,
    *,
    filters: ModelFilters,
    limit: int = 500,
) -> list[OutlinerModelRead]:
    """Minimal desktop outliner rows; no Artifact/Metadata/print hydration."""
    rows = session.exec(
        _filtered_stmt(session, user, filters)
        .order_by(func.lower(Model.name).asc(), Model.id.asc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [
        OutlinerModelRead(
            id=model.id,
            name=model.name,
            collection=collection_name_for(model),
            collection_id=model.collection_id,
        )
        for model in rows
    ]


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def detail(session: Session, model_id: int, user: User) -> ModelRead | None:
    """Full model detail with files + metadata. None when missing or trashed."""
    m = session.get(Model, model_id)
    if m is None or m.deleted_at is not None:
        return None
    role = _effective_model_role(session, user, m)
    if not rbac.role_allows(role, CollectionRole.VIEW):
        return None

    files_with_meta = session.exec(
        select(File, Metadata)
        .where(File.model_id == model_id)
        .where(live(File))
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    starred = session.exec(
        select(ModelStar.id).where(
            ModelStar.user_id == user.id, ModelStar.model_id == model_id
        )
    ).first() is not None

    return ModelRead(
        id=m.id,  # type: ignore[arg-type]
        name=m.name,
        slug=m.slug,
        hash=m.hash,
        collection=collection_name_for(m),
        collection_id=m.collection_id,
        description=m.description,
        source_url=m.source_url,
        effective_role=role,
        # m.tags loads via selectin (see the Model relationship) — no extra query.
        tags=sorted(t.name for t in m.tags),
        thumbnail_url=thumb_url(m),
        created_at=m.created_at,
        updated_at=m.updated_at,
        files=_file_reads_with_revisions(session, files_with_meta),
        starred=starred,
    )


def artifact_outcomes(
    session: Session, model_id: int, file_ids: list[int]
) -> list[dict[str, object]]:
    """Aggregate measured print outcomes for selected live Artifacts."""
    ids = list(dict.fromkeys(file_ids))
    files = session.exec(
        select(File.id).where(File.model_id == model_id, File.id.in_(ids), live(File))
    ).all()
    if len(files) != len(ids):
        return []
    jobs = session.exec(
        select(PrintJob).where(
            PrintJob.model_id == model_id,
            PrintJob.file_id.in_(ids),
            live(PrintJob),
        )
    ).all()
    result: list[dict[str, object]] = []
    for file_id in ids:
        rows = [job for job in jobs if job.file_id == file_id]
        completed = [job for job in rows if job.state == PrintJobState.COMPLETED]
        failed = [job for job in rows if job.state == PrintJobState.FAILED]
        cancelled = [job for job in rows if job.state == PrintJobState.CANCELLED]
        decided = len(completed) + len(failed)
        durations = [job.actual_duration_s for job in rows if job.actual_duration_s is not None]
        filament = [job.filament_g_effective for job in rows if job.filament_g_effective is not None]
        costs = [job.cost for job in rows if job.cost is not None]
        result.append({
            "file_id": file_id,
            "print_count": len(rows),
            "completed_count": len(completed),
            "failed_count": len(failed),
            "cancelled_count": len(cancelled),
            "success_rate": len(completed) / decided if decided else None,
            "average_duration_s": sum(durations) / len(durations) if durations else None,
            "total_filament_g": sum(filament) if filament else None,
            "total_cost": sum(costs) if costs else None,
        })
    return result


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_payload(session: Session, user: User) -> dict:
    stmt = (
        select(Model)
        .where(live(Model))
        .where(Model.hash != SENTINEL_MODEL_HASH)
        .order_by(Model.name.asc())  # type: ignore[attr-defined]
    )
    stmt = _apply_model_access(stmt, session, user)
    model_rows = session.exec(stmt).all()
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
                    is_external=file_row.is_external,
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
                "source_url": model.source_url,
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


def _csv_cell(value: Any) -> Any:
    """Render a CSV cell, preserving a legitimate ``0`` / ``False``.

    A plain ``value or ""`` collapses real zeros — 0 % infill (vase mode), a
    0 °C unheated bed, 0 top-shell layers (open-top print) — into blanks. Only
    ``None`` (genuinely absent) should become an empty cell.
    """
    return "" if value is None else value


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
                    "model_source_url": model.get("source_url") or "",
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
                    "slicer_name": _csv_cell(metadata.get("slicer_name")),
                    "slicer_version": _csv_cell(metadata.get("slicer_version")),
                    "printer_model": _csv_cell(metadata.get("printer_model")),
                    "nozzle_diameter_mm": _csv_cell(metadata.get("nozzle_diameter_mm")),
                    "layer_height_mm": _csv_cell(metadata.get("layer_height_mm")),
                    "first_layer_height_mm": _csv_cell(
                        metadata.get("first_layer_height_mm")
                    ),
                    "infill_percent": _csv_cell(metadata.get("infill_percent")),
                    "wall_loops": _csv_cell(metadata.get("wall_loops")),
                    "top_shell_layers": _csv_cell(metadata.get("top_shell_layers")),
                    "bottom_shell_layers": _csv_cell(
                        metadata.get("bottom_shell_layers")
                    ),
                    "support_material": _csv_cell(metadata.get("support_material")),
                    "nozzle_temperature_c": _csv_cell(
                        metadata.get("nozzle_temperature_c")
                    ),
                    "bed_temperature_c": _csv_cell(metadata.get("bed_temperature_c")),
                    "estimated_time_s": _csv_cell(metadata.get("estimated_time_s")),
                    "filament_weight_g": _csv_cell(metadata.get("filament_weight_g")),
                    "filament_length_mm": _csv_cell(metadata.get("filament_length_mm")),
                    "filament_cost": _csv_cell(metadata.get("filament_cost")),
                    "material_type": _csv_cell(metadata.get("material_type")),
                    "material_brand": _csv_cell(metadata.get("material_brand")),
                    "bbox_x_mm": _csv_cell(metadata.get("bbox_x_mm")),
                    "bbox_y_mm": _csv_cell(metadata.get("bbox_y_mm")),
                    "bbox_z_mm": _csv_cell(metadata.get("bbox_z_mm")),
                    "volume_mm3": _csv_cell(metadata.get("volume_mm3")),
                    "triangle_count": _csv_cell(metadata.get("triangle_count")),
                }
            )
    return out.getvalue()


# ---------------------------------------------------------------------------
# Trash listing
# ---------------------------------------------------------------------------


def list_trashed(
    session: Session,
    user: User,
    *,
    limit: int = 50,
    offset: int = 0,
    retention_days: int,
) -> List[TrashedModelRead]:
    stmt = (
        select(Model)
        .where(trashed(Model))
        # Stable tiebreaker on id: a bulk trash gives many rows the same
        # deleted_at, which would otherwise paginate non-deterministically.
        .order_by(Model.deleted_at.desc(), Model.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    stmt = _apply_model_access(stmt, session, user)
    rows = session.exec(stmt).all()
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


# ``backend.usage()`` walks the whole storage tree (or lists the bucket). The
# dashboard calls it on every load, where a slightly stale total is fine.
_USAGE_TTL_S = 60.0
_usage_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _cached_storage_usage() -> dict:
    """Storage usage, recomputed at most once per minute per configured backend.

    Keyed on the effective backend + data dir so a runtime reconfiguration (and
    each test's tmp_path) gets its own entry. Failures are not cached: a
    transient S3 error should not pin an error state for a minute.
    """
    key = (str(settings.storage_backend), str(settings.data_dir))
    now = monotonic()
    hit = _usage_cache.get(key)
    if hit is not None and now - hit[0] < _USAGE_TTL_S:
        return hit[1]
    usage = get_backend().usage()
    _usage_cache[key] = (now, usage)
    return usage


def vault_stats(session: Session, user: User) -> VaultStatsRead:
    live_model_ids = select(Model.id).where(
        live(Model), Model.hash != SENTINEL_MODEL_HASH
    )
    live_model_ids = _apply_model_access(live_model_ids, session, user).cte(
        "vault_models"
    )
    file_stats = (
        select(
            func.count(File.id).label("file_count"),
            func.coalesce(func.sum(File.size_bytes), 0).label("indexed_size"),
            func.coalesce(
                func.sum(case((File.file_type != FileType.GCODE, 1), else_=0)), 0
            ).label("source_file_count"),
            func.coalesce(
                func.sum(case((File.file_type == FileType.GCODE, 1), else_=0)), 0
            ).label("gcode_file_count"),
        )
        .join(live_model_ids, live_model_ids.c.id == File.model_id)
        .where(live(File))
        .cte("vault_file_stats")
    )
    counts = session.execute(
        select(
            select(func.count()).select_from(live_model_ids).scalar_subquery(),
            file_stats.c.file_count,
            file_stats.c.indexed_size,
            file_stats.c.source_file_count,
            file_stats.c.gcode_file_count,
            select(func.count(Collection.id))
            .where(live(Collection))
            .scalar_subquery(),
            select(func.count(Tag.id)).where(live(Tag)).scalar_subquery(),
            select(func.count(Printer.id)).where(live(Printer)).scalar_subquery(),
        ).select_from(file_stats)
    ).one()
    (
        model_count,
        file_count,
        indexed_size,
        source_file_count,
        gcode_file_count,
        collection_count,
        tag_count,
        printer_count,
    ) = counts

    try:
        storage_usage = StorageUsageRead(**_cached_storage_usage())
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


# Supported preset windows → lookback in days; None means "all time".
_STATS_PERIODS: dict[str, Optional[int]] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "1y": 365,
    "all": None,
}


def print_statistics(session: Session, period: str) -> PrintStatisticsRead:
    """Aggregate *completed* print jobs over a preset time window.

    Reads the cost and effective filament grams that were resolved and
    frozen once, at completion time (see
    ``print_results.resolve_completion_cost``), rather than re-hydrating
    every job row and re-matching a filament profile on every dashboard
    load. A profile's price edited after a print completed does not change
    that print's historical cost.
    """
    if period not in _STATS_PERIODS:
        period = "30d"
    lookback_days = _STATS_PERIODS[period]

    end_at = utcnow()
    start_at: Optional[datetime] = (
        end_at - timedelta(days=lookback_days) if lookback_days is not None else None
    )
    # Manually-logged jobs may not set finished_at; fall back to created_at so
    # they still land in the right window.
    anchor = func.coalesce(PrintJob.finished_at, PrintJob.created_at)

    query = (
        select(
            PrintJob.cost,
            PrintJob.filament_g_effective,
            PrintJob.actual_duration_s,
            PrintJob.finished_at,
            PrintJob.created_at,
            Model.collection_id,
            Model.id,
            Model.name,
            Collection.name,
            Collection.path,
            Printer.id,
            Printer.name,
            Metadata.material_type,
            Metadata.material_brand,
            Metadata.estimated_time_s,
        )
        .join(File, File.id == PrintJob.file_id)
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .join(Model, Model.id == PrintJob.model_id)
        .outerjoin(Collection, Collection.id == Model.collection_id)
        .outerjoin(Printer, Printer.id == PrintJob.printer_id)
        .where(
            live(PrintJob),
            PrintJob.state == PrintJobState.COMPLETED,
        )
    )
    if start_at is not None:
        query = query.where(anchor >= start_at)  # type: ignore[operator]

    rows = session.exec(query).all()

    total_cost = 0.0
    has_cost = False
    total_filament_g = 0.0
    has_filament = False
    total_duration_s = 0
    grams_samples = 0
    grams_sum = 0.0

    bucket_monthly = lookback_days is None or lookback_days > 90

    collection_acc: dict[Optional[int], dict] = {}
    filament_acc: dict[tuple, dict] = {}
    model_acc: dict[int, dict] = {}
    printer_acc: dict[Optional[int], dict] = {}
    time_acc: dict[str, dict] = {}

    for (
        cost,
        grams,
        duration,
        finished_at,
        created_at,
        cid,
        model_id,
        model_name,
        cname,
        cpath,
        printer_id,
        printer_name,
        mtype,
        mbrand,
        estimated_time_s,
    ) in rows:
        if duration is None:
            duration = estimated_time_s
        if cost is not None:
            total_cost += cost
            has_cost = True
        if grams is not None:
            total_filament_g += grams
            has_filament = True
            grams_sum += grams
            grams_samples += 1
        if duration is not None:
            total_duration_s += duration

        # Top collections (Uncategorized bucket when the model has no collection).
        c = collection_acc.setdefault(
            cid,
            {
                "name": cname if cid is not None else "Uncategorized",
                "path": cpath if cid is not None else None,
                "print_count": 0,
                "total_cost": 0.0,
                "has_cost": False,
            },
        )
        c["print_count"] += 1
        if cost is not None:
            c["total_cost"] += cost
            c["has_cost"] = True

        model_stat = model_acc.setdefault(
            model_id,
            {"name": model_name, "print_count": 0, "total_g": 0.0, "has_g": False},
        )
        model_stat["print_count"] += 1
        if grams is not None:
            model_stat["total_g"] += grams
            model_stat["has_g"] = True

        printer_stat = printer_acc.setdefault(
            printer_id,
            {
                "name": printer_name or "Unassigned / manual",
                "print_count": 0,
                "print_time_s": 0,
            },
        )
        printer_stat["print_count"] += 1
        if duration is not None:
            printer_stat["print_time_s"] += duration

        # Top filaments grouped by (material_type, material_brand).
        f = filament_acc.setdefault(
            (mtype, mbrand),
            {
                "material_type": mtype,
                "material_brand": mbrand,
                "print_count": 0,
                "total_g": 0.0,
                "has_g": False,
                "total_cost": 0.0,
                "has_cost": False,
            },
        )
        f["print_count"] += 1
        if grams is not None:
            f["total_g"] += grams
            f["has_g"] = True
        if cost is not None:
            f["total_cost"] += cost
            f["has_cost"] = True

        # Cost-over-time buckets keyed by day (≤90d) or month (longer/all).
        when = ensure_utc(finished_at or created_at)
        key = when.strftime("%Y-%m") if bucket_monthly else when.strftime("%Y-%m-%d")
        b = time_acc.setdefault(
            key,
            {
                "cost": 0.0,
                "has_cost": False,
                "filament_g": 0.0,
                "has_g": False,
                "print_count": 0,
            },
        )
        b["print_count"] += 1
        if cost is not None:
            b["cost"] += cost
            b["has_cost"] = True
        if grams is not None:
            b["filament_g"] += grams
            b["has_g"] = True

    top_collections = [
        CollectionStatRead(
            collection_id=cid,
            name=v["name"],
            path=v["path"],
            print_count=v["print_count"],
            total_cost=round(v["total_cost"], 4) if v["has_cost"] else None,
        )
        for cid, v in sorted(
            collection_acc.items(), key=lambda kv: kv[1]["print_count"], reverse=True
        )
    ][:10]

    top_filaments = [
        FilamentStatRead(
            material_type=v["material_type"],
            material_brand=v["material_brand"],
            print_count=v["print_count"],
            total_g=round(v["total_g"], 2) if v["has_g"] else None,
            total_cost=round(v["total_cost"], 4) if v["has_cost"] else None,
        )
        for v in sorted(
            filament_acc.values(), key=lambda x: x["print_count"], reverse=True
        )
    ][:10]

    top_models = [
        ModelStatRead(
            model_id=model_id,
            name=v["name"],
            print_count=v["print_count"],
            total_g=round(v["total_g"], 2) if v["has_g"] else None,
        )
        for model_id, v in sorted(
            model_acc.items(), key=lambda kv: kv[1]["print_count"], reverse=True
        )
    ][:10]

    top_printers = [
        PrinterStatRead(
            printer_id=printer_id,
            name=v["name"],
            print_count=v["print_count"],
            print_time_s=v["print_time_s"],
        )
        for printer_id, v in sorted(
            printer_acc.items(), key=lambda kv: kv[1]["print_time_s"], reverse=True
        )
    ][:10]

    cost_over_time = [
        TimeBucketRead(
            bucket=key,
            cost=round(v["cost"], 4) if v["has_cost"] else None,
            filament_g=round(v["filament_g"], 2) if v["has_g"] else None,
            print_count=v["print_count"],
        )
        for key, v in sorted(time_acc.items())
    ]

    return PrintStatisticsRead(
        period=period,
        start_at=start_at,
        end_at=end_at,
        total_prints=len(rows),
        total_cost=round(total_cost, 4) if has_cost else None,
        total_filament_g=round(total_filament_g, 2) if has_filament else None,
        avg_filament_g=(round(grams_sum / grams_samples, 2) if grams_samples else None),
        total_print_time_s=total_duration_s,
        top_collections=top_collections,
        top_filaments=top_filaments,
        top_models=top_models,
        top_printers=top_printers,
        cost_over_time=cost_over_time,
    )
