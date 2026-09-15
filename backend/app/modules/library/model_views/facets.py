"""Filter facet queries over the authorized library."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import String, case, cast, func, literal, union_all
from sqlmodel import Session, select

from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    PrintJob,
    PrintJobState,
    User,
)
from app.db.scopes import live
from app.schemas.models import (
    FacetValueRead,
    ModelFacetsRead,
    ModelFilters,
)

from .filters import _filtered_stmt, print_job_predicates


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
        .where(*print_job_predicates(filters))
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
        grouped_branch("revision_status", files.c.revision_status, files.c.model_id),
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
        "revision_status": {member.name: member.value for member in FileRevisionStatus},
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
