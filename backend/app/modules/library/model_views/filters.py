"""SQL predicates for structured library filters."""

from __future__ import annotations

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.content_search import ranked_model_matches
from app.db.models import (
    SENTINEL_MODEL_HASH,
    Collection,
    File,
    FileType,
    Metadata,
    Model,
    ModelFamilyMember,
    ModelStar,
    Printer,
    PrinterFile,
    PrintJob,
    User,
)
from app.db.scopes import live
from app.modules.library import library_search
from app.schemas.models import (
    ModelFilters,
)

from .access import _apply_model_access

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
            artifact_ids = artifact_ids.where(
                File.uploaded_at >= filters.uploaded_after
            )
        if filters.uploaded_before:
            artifact_ids = artifact_ids.where(
                File.uploaded_at <= filters.uploaded_before
            )
        if filters.material_type:
            artifact_ids = artifact_ids.where(
                func.lower(Metadata.material_type).in_(
                    value.lower() for value in filters.material_type
                )
            )
        if filters.slicer_name:
            artifact_ids = artifact_ids.where(
                func.lower(Metadata.slicer_name).in_(
                    value.lower() for value in filters.slicer_name
                )
            )
        if filters.printer_model:
            artifact_ids = artifact_ids.where(
                func.lower(Metadata.printer_model).in_(
                    value.lower() for value in filters.printer_model
                )
            )
        stmt = stmt.where(Model.id.in_(artifact_ids))  # type: ignore[union-attr]

    matching_jobs = (
        select(PrintJob.id)
        .where(PrintJob.model_id == Model.id, *print_job_predicates(filters))
        .exists()
    )
    has_history = bool(filters.print_outcome) or any(
        value is not None
        for value in (
            filters.printed_after,
            filters.printed_before,
            filters.print_duration_min_s,
            filters.print_duration_max_s,
        )
    )
    if filters.printed is False:
        # NOT EXISTS also handles captured jobs that have no attached Model.
        any_job = (
            select(PrintJob.id)
            .where(PrintJob.model_id == Model.id, live(PrintJob))
            .exists()
        )
        stmt = stmt.where(~any_job)
    if filters.printed is True or has_history:
        stmt = stmt.where(matching_jobs)

    return stmt


def print_job_predicates(filters: ModelFilters):
    """Date, outcome and real duration describe the same live PrintJob."""
    predicates = [live(PrintJob)]
    if filters.print_outcome:
        predicates.append(PrintJob.state.in_(filters.print_outcome))
    if filters.printed_after is not None:
        predicates.append(PrintJob.finished_at >= filters.printed_after)
    if filters.printed_before is not None:
        predicates.append(PrintJob.finished_at < filters.printed_before)
    if filters.print_duration_min_s is not None:
        predicates.append(PrintJob.actual_duration_s >= filters.print_duration_min_s)
    if filters.print_duration_max_s is not None:
        predicates.append(PrintJob.actual_duration_s < filters.print_duration_max_s)
    return predicates


def filtered_with_rank(session: Session, user: User, filters: ModelFilters):
    stmt = select(Model).where(live(Model), Model.hash != SENTINEL_MODEL_HASH)
    stmt = _apply_model_access(stmt, session, user)
    if (
        filters.family_id is not None
        or filters.family_role is not None
        or filters.in_family is not None
    ):
        from .extensions import membership_rows

        memberships = membership_rows(session, user)
        if filters.in_family is not None:
            any_membership = Model.id.in_(
                memberships.with_only_columns(ModelFamilyMember.model_id)
            )
            stmt = stmt.where(any_membership if filters.in_family else ~any_membership)
        if filters.family_id is not None:
            memberships = memberships.where(
                ModelFamilyMember.family_id == filters.family_id
            )
        if filters.family_role is not None:
            memberships = memberships.where(
                ModelFamilyMember.role == filters.family_role
            )
        if filters.family_id is not None or filters.family_role is not None:
            stmt = stmt.where(
                Model.id.in_(memberships.with_only_columns(ModelFamilyMember.model_id))
            )
    if filters.has_similar_candidates is not None:
        from .extensions import has_open_candidates

        predicate = has_open_candidates(session, user)
        stmt = stmt.where(predicate if filters.has_similar_candidates else ~predicate)
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
    matches = (
        ranked_model_matches(
            session, filters.q, stmt.with_only_columns(Model.id).correlate(None)
        )
        if filters.q and filters.q.strip()
        else None
    )
    stmt = library_search.apply_library_search(
        stmt,
        query=filters.q,
        tag_slugs=filters.tag,
        matches=matches,
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
            Model.id.in_(
                present_model_ids.where(PrinterFile.printer_id == filters.printer_id)
            )  # type: ignore[union-attr]
        )
    elif filters.printer_presence == "any":
        stmt = stmt.where(Model.id.in_(present_model_ids))  # type: ignore[union-attr]
    elif filters.printer_presence == "none":
        stmt = stmt.where(Model.id.not_in(present_model_ids))  # type: ignore[attr-defined]
    rank = (
        select(matches.c.score)
        .where(matches.c.model_id == Model.id)
        .correlate(Model)
        .scalar_subquery()
        if matches is not None
        else None
    )
    return stmt, rank


def _filtered_stmt(session: Session, user: User, filters: ModelFilters):
    return filtered_with_rank(session, user, filters)[0]
