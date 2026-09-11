"""Paged member metadata, scoped before counting or reading Artifact details."""

from collections import defaultdict

from sqlalchemy import case, func, literal
from sqlmodel import Session, col, select

from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelFamilyMember,
    PrintJob,
    User,
)
from app.db.scopes import live
from app.modules.library.families.access import require
from app.schemas.families import (
    FamilyMemberItem,
    FamilyMemberPage,
    FamilyMemberRead,
    FamilyMemberSort,
)
from app.schemas.family_types import VariantRole
from app.schemas.models import ModelFilters

from .family_cursors import context_key, page_rows
from .filters import _filtered_stmt
from .pagination import _job_sort_stats
from .projections import _file_reads_with_revisions, _hydrate_list_rows


def member_page(
    session: Session,
    user: User,
    family_id: int,
    *,
    q: str | None,
    role: VariantRole | None,
    formats: list[FileType],
    known_good: bool | None,
    has_revisions: bool | None,
    source: str | None,
    sort: FamilyMemberSort,
    cursor: str | None,
    limit: int,
) -> FamilyMemberPage:
    require(session, user, family_id)
    filters = ModelFilters(
        family_id=family_id,
        family_role=role,
        q=q,
        file_type=formats,
        storage=[source] if source else [],
    )
    model_ids = _filtered_stmt(session, user, filters).with_only_columns(Model.id)
    stmt = select(ModelFamilyMember).where(
        ModelFamilyMember.family_id == family_id,
        col(ModelFamilyMember.detached_at).is_(None),
        col(ModelFamilyMember.model_id).in_(model_ids),
    )
    revisions = select(File.model_id).where(
        live(File), File.file_type == FileType.GCODE
    )
    if has_revisions is not None:
        matches = col(ModelFamilyMember.model_id).in_(revisions)
        stmt = stmt.where(matches if has_revisions else ~matches)
    if known_good is not None:
        matches = col(ModelFamilyMember.model_id).in_(
            revisions.where(File.revision_status == FileRevisionStatus.KNOWN_GOOD)
        )
        stmt = stmt.where(matches if known_good else ~matches)
    value = ModelFamilyMember.sort_order
    if sort in (FamilyMemberSort.DATE_ASC, FamilyMemberSort.DATE_DESC):
        value = ModelFamilyMember.created_at
    elif sort in (FamilyMemberSort.SCALE_ASC, FamilyMemberSort.SCALE_DESC):
        value = ModelFamilyMember.scale_factor
    elif sort == FamilyMemberSort.SUCCESS_DESC:
        stats = _job_sort_stats()
        stmt = stmt.outerjoin(stats, stats.c.model_id == ModelFamilyMember.model_id)
        value = stats.c.success_rate
    statement = stmt.with_only_columns(
        literal("member").label("kind"),
        ModelFamilyMember.id.label("card_id"),
        value.label("sort_value"),
    )
    page_filters = {
        "family_id": family_id,
        "q": q,
        "role": role,
        "formats": formats,
        "known_good": known_good,
        "has_revisions": has_revisions,
        "source": source,
    }
    page, next_cursor, total = page_rows(
        session,
        statement,
        key=context_key(int(user.id), "family_members", page_filters, sort.value),
        cursor=cursor,
        limit=limit,
        descending=sort
        in (
            FamilyMemberSort.DATE_DESC,
            FamilyMemberSort.SCALE_DESC,
            FamilyMemberSort.SUCCESS_DESC,
        ),
        value_type="date"
        if sort in (FamilyMemberSort.DATE_ASC, FamilyMemberSort.DATE_DESC)
        else "number",
    )
    ids = [row["card_id"] for row in page]
    rows = session.exec(
        select(ModelFamilyMember, Model)
        .join(Model, Model.id == ModelFamilyMember.model_id)
        .where(col(ModelFamilyMember.id).in_(ids))
    ).all()
    by_member = {int(member.id): (member, model) for member, model in rows}
    models = {
        model.id: model
        for model in _hydrate_list_rows(session, user, [model for _, model in rows])
    }
    model_ids_page = list(models)
    aggregates = defaultdict(dict)
    for model_id, file_type, count, good in session.exec(
        select(
            File.model_id,
            File.file_type,
            func.count(),
            func.sum(
                case(
                    (File.revision_status == FileRevisionStatus.KNOWN_GOOD, 1), else_=0
                )
            ),
        )
        .where(col(File.model_id).in_(model_ids_page), live(File))
        .group_by(File.model_id, File.file_type)
    ).all():
        aggregates[model_id][file_type] = (count, good)
    previews = session.exec(
        select(File, Metadata)
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .where(
            col(File.id).in_(
                [model.mesh_file_id for model in models.values() if model.mesh_file_id]
            ),
            live(File),
        )
    ).all()
    files = {
        file.id: file for file in _file_reads_with_revisions(session, list(previews))
    }
    latest_jobs = (
        select(
            PrintJob.model_id,
            PrintJob.state,
            func.row_number()
            .over(
                partition_by=PrintJob.model_id,
                order_by=(col(PrintJob.created_at).desc(), col(PrintJob.id).desc()),
            )
            .label("position"),
        )
        .where(col(PrintJob.model_id).in_(model_ids_page), live(PrintJob))
        .subquery("family_latest_jobs")
    )
    outcomes = dict(
        session.execute(
            select(latest_jobs.c.model_id, latest_jobs.c.state).where(
                latest_jobs.c.position == 1
            )
        ).all()
    )
    items = []
    for row in page:
        member, model_row = by_member[row["card_id"]]
        model = models[int(model_row.id)]
        counts = aggregates[model.id]
        preview = files.get(model.mesh_file_id)
        revisions_count, good_count = counts.get(FileType.GCODE, (0, 0))
        items.append(
            FamilyMemberItem(
                **FamilyMemberRead.model_validate(
                    member, from_attributes=True
                ).model_dump(),
                model=model,
                preview_file=preview,
                formats=sorted(file_type.value for file_type in counts),
                source_file_count=sum(
                    count
                    for file_type, (count, _) in counts.items()
                    if file_type != FileType.GCODE
                ),
                gcode_revision_count=revisions_count,
                known_good_count=good_count,
                latest_print_outcome=outcomes.get(model.id),
                units="mm"
                if preview and preview.file_type == FileType.THREE_MF
                else "unknown",
            )
        )
    return FamilyMemberPage(items=items, total=total, next_cursor=next_cursor)
