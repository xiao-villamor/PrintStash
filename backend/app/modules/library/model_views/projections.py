"""Batched Model response composition and related-row loading."""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from sqlalchemy import case, func
from sqlmodel import Session, select

from app.core.time import ensure_utc
from app.db.models import (
    FilamentProfile,
    File,
    FileRevisionStatus,
    FileTagLink,
    FileType,
    Metadata,
    Model,
    ModelStar,
    Printer,
    PrinterFile,
    PrintJob,
    PrintJobState,
    Tag,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac
from app.modules.printing.costing import (
    cost_profiles,
    match_cost_profile,
)
from app.schemas.models import (
    FileRead,
    MetadataRead,
    ModelListItem,
    ModelPrinterPresenceRead,
    PrintSummaryRead,
)

from .extensions import similarity_summaries
from .families import family_summaries
from .thumbnails import thumb_url

# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


def collection_name_for(model: Model) -> Optional[str]:
    """Resolve the collection name from the FK-joined relationship."""
    return model.collection_rel.path if model.collection_rel else None


def metadata_read(
    session: Session,
    metadata: Metadata,
    profiles: list[FilamentProfile] | None = None,
) -> MetadataRead:
    data = metadata.model_dump()
    if profiles is None:
        profiles = cost_profiles(session)
    profile = match_cost_profile(profiles, metadata)
    if profile and profile.cost_per_kg is not None and metadata.filament_weight_g:
        data["filament_cost"] = round(
            metadata.filament_weight_g * profile.cost_per_kg / 1000,
            4,
        )
    return MetadataRead(**data)


def _file_tag_names(session: Session, file_ids: list[int]) -> dict[int, list[str]]:
    if not file_ids:
        return {}
    result: dict[int, list[str]] = defaultdict(list)
    rows = session.exec(
        select(FileTagLink.file_id, Tag.name)
        .join(Tag, Tag.id == FileTagLink.tag_id)
        .where(FileTagLink.file_id.in_(file_ids), live(Tag))  # type: ignore[union-attr]
        .order_by(FileTagLink.file_id.asc(), Tag.name.asc())  # type: ignore[attr-defined]
    ).all()
    for file_id, name in rows:
        if file_id is not None:
            result[file_id].append(name)
    return dict(result)


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
    tags_by_file = _file_tag_names(
        session,
        [f.id for f, _md in files_with_meta if f.id is not None],
    )
    profiles = cost_profiles(session)
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
            tags=tags_by_file.get(f.id, []),
            metadata=metadata_read(session, md, profiles) if md else None,
        )
        for f, md in files_with_meta
    ]


def _hydrate_list_rows(
    session: Session, user: User, rows: list[Model]
) -> list[ModelListItem]:
    """Compose one already-ordered Model page with bounded batch queries."""
    model_ids = [m.id for m in rows if m.id is not None]
    if not model_ids:
        return []
    similarity = similarity_summaries(session, user, model_ids)
    families = family_summaries(session, user, model_ids)
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
            File.model_id.asc(),
            File.uploaded_at.desc(),
            File.id.desc(),  # type: ignore[attr-defined]
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

    for (
        model_id,
        completed,
        decided,
        last_printed,
        average_duration,
        total_cost,
    ) in session.exec(
        select(
            PrintJob.model_id,
            func.sum(case((PrintJob.state == PrintJobState.COMPLETED, 1), else_=0)),
            func.sum(
                case(
                    (
                        PrintJob.state.in_(
                            [PrintJobState.COMPLETED, PrintJobState.FAILED]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
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
        summary.average_duration_s = (
            float(average_duration) if average_duration is not None else None
        )
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
                family=families.get(model.id),
                similarity=similarity.get(model.id, {}),
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
