"""Collapse authorized Family cards in SQL before sorting or limiting a page."""

from sqlalchemy import func, literal, or_, union_all
from sqlmodel import Session, col, select

from app.db.models import (
    Collection,
    Model,
    ModelFamily,
    ModelFamilyMember,
    ModelFamilyStar,
    ModelFamilyTagLink,
    Tag,
    User,
)
from app.db.scopes import live, trashed
from app.modules.library.families.access import editable_clause, visible_clause
from app.modules.library.library_search import _escaped_like
from app.schemas.families import (
    FamilyBrowseCard,
    FamilyBrowsePage,
    FamilyPageRead,
    ModelBrowseCard,
)
from app.schemas.models import ModelFilters, ModelSort

from .access import accessible_live_model_ids_stmt
from .families import family_reads, membership_rows
from .family_cursors import context_key, page_rows
from .filters import _filtered_stmt
from .pagination import _sort_value_and_statement
from .projections import _hydrate_list_rows


def _family_sort(statement, sort: ModelSort, session: Session, user: User):
    if sort in (ModelSort.DATE_ASC, ModelSort.DATE_DESC):
        return statement, ModelFamily.updated_at
    if sort in (ModelSort.NAME_ASC, ModelSort.NAME_DESC):
        return statement, func.lower(ModelFamily.name)
    measured, expression = _sort_value_and_statement(select(Model), sort)
    measured = (
        measured.with_only_columns(
            Model.id.label("model_id"), expression.label("value")
        )
        .where(col(Model.id).in_(accessible_live_model_ids_stmt(session, user)))
        .subquery("visible_canonical_metrics")
    )
    return (
        statement.outerjoin(
            ModelFamilyMember, ModelFamilyMember.id == ModelFamily.canonical_member_id
        ).outerjoin(measured, measured.c.model_id == ModelFamilyMember.model_id),
        measured.c.value,
    )


def _page(
    session: Session,
    statement,
    user: User,
    mode: str,
    filters: dict,
    sort: ModelSort,
    cursor: str | None,
    limit: int,
):
    return page_rows(
        session,
        statement,
        key=context_key(int(user.id), mode, filters, sort.value),
        cursor=cursor,
        limit=limit,
        descending=sort
        in (
            ModelSort.DATE_DESC,
            ModelSort.NAME_DESC,
            ModelSort.SUCCESS_DESC,
            ModelSort.PRINTED_DESC,
        ),
        value_type="date"
        if sort in (ModelSort.DATE_ASC, ModelSort.DATE_DESC, ModelSort.PRINTED_DESC)
        else "text"
        if sort in (ModelSort.NAME_ASC, ModelSort.NAME_DESC)
        else "number",
    )


def collapsed_page(
    session: Session,
    user: User,
    *,
    filters: ModelFilters,
    sort: ModelSort,
    cursor: str | None,
    limit: int,
) -> FamilyBrowsePage:
    filtered = _filtered_stmt(session, user, filters)
    memberships = membership_rows(session, user).cte("visible_memberships")
    ungrouped = filtered.where(col(Model.id).not_in(select(memberships.c.model_id)))
    ungrouped, model_sort = _sort_value_and_statement(ungrouped, sort)
    model_cards = ungrouped.with_only_columns(
        literal("model").label("kind"),
        Model.id.label("card_id"),
        model_sort.label("sort_value"),
        literal(0).label("matching_count"),
    )

    # Model filters retain their normal semantics. Family stars are personal to
    # the grouping; Family text can match independently of its visible siblings.
    member_filters = filters.model_copy(update={"q": None, "favorites": False})
    member_ids = _filtered_stmt(session, user, member_filters).with_only_columns(
        Model.id
    )
    text_ids = _filtered_stmt(
        session, user, filters.model_copy(update={"favorites": False})
    ).with_only_columns(Model.id)
    matching = (
        select(
            memberships.c.family_id,
            func.count().label("count"),
        )
        .where(memberships.c.model_id.in_(text_ids))
        .group_by(memberships.c.family_id)
        .subquery("matching_family_members")
    )
    families = select(ModelFamily).where(
        live(ModelFamily), visible_clause(session, user)
    )
    if filters.in_family is False:
        families = families.where(literal(False))
    if filters.family_id is not None:
        families = families.where(ModelFamily.id == filters.family_id)
    if filters.favorites:
        families = families.where(
            col(ModelFamily.id).in_(
                select(ModelFamilyStar.family_id).where(
                    ModelFamilyStar.user_id == user.id
                )
            )
        )
    remaining = member_filters.model_dump(
        exclude_defaults=True, exclude={"browse", "family_id", "in_family"}
    )
    if remaining:
        families = families.where(
            col(ModelFamily.id).in_(
                select(memberships.c.family_id).where(
                    memberships.c.model_id.in_(member_ids)
                )
            )
        )
    families = families.outerjoin(matching, matching.c.family_id == ModelFamily.id)
    if filters.q and filters.q.strip():
        pattern = _escaped_like(filters.q.strip())
        families = families.where(
            or_(
                col(ModelFamily.name).ilike(pattern, escape="\\"),
                col(ModelFamily.description).ilike(pattern, escape="\\"),
                matching.c.count > 0,
            )
        )
    families, family_sort = _family_sort(families, sort, session, user)
    family_cards = families.with_only_columns(
        literal("family").label("kind"),
        ModelFamily.id.label("card_id"),
        family_sort.label("sort_value"),
        func.coalesce(matching.c.count, 0).label("matching_count"),
    )
    page, next_cursor, total = _page(
        session,
        union_all(model_cards, family_cards),
        user,
        "families_collapsed",
        filters.model_dump(mode="json"),
        sort,
        cursor,
        limit,
    )
    family_ids = [row["card_id"] for row in page if row["kind"] == "family"]
    model_ids = [row["card_id"] for row in page if row["kind"] == "model"]
    cards = family_reads(
        session,
        user,
        list(
            session.exec(
                select(ModelFamily).where(col(ModelFamily.id).in_(family_ids))
            ).all()
        ),
    )
    models = {
        model.id: model
        for model in _hydrate_list_rows(
            session,
            user,
            list(session.exec(select(Model).where(col(Model.id).in_(model_ids))).all()),
        )
    }
    items: list[FamilyBrowseCard | ModelBrowseCard] = []
    for row in page:
        if row["kind"] == "family":
            card = cards[row["card_id"]]
            card.matching_visible_members = row["matching_count"]
            items.append(FamilyBrowseCard(family=card))
        else:
            items.append(ModelBrowseCard(model=models[row["card_id"]]))
    return FamilyBrowsePage(items=items, total=total, next_cursor=next_cursor)


def family_page(
    session: Session,
    user: User,
    *,
    q: str | None,
    collection_id: int | None,
    favorites: bool,
    tags: list[str],
    include_trashed: bool,
    sort: ModelSort,
    cursor: str | None,
    limit: int,
) -> FamilyPageRead:
    stmt = select(ModelFamily).where(
        trashed(ModelFamily) if include_trashed else live(ModelFamily),
        visible_clause(session, user),
    )
    if include_trashed:
        stmt = stmt.where(editable_clause(session, user))
    if q and q.strip():
        pattern = _escaped_like(q.strip())
        stmt = stmt.where(
            or_(
                col(ModelFamily.name).ilike(pattern, escape="\\"),
                col(ModelFamily.description).ilike(pattern, escape="\\"),
            )
        )
    if collection_id is not None:
        stmt = stmt.where(
            ModelFamily.collection_id == collection_id,
            col(ModelFamily.collection_id).in_(
                select(Collection.id).where(live(Collection))
            ),
        )
    if favorites:
        stmt = stmt.where(
            col(ModelFamily.id).in_(
                select(ModelFamilyStar.family_id).where(
                    ModelFamilyStar.user_id == user.id
                )
            )
        )
    for tag in set(tags):
        stmt = stmt.where(
            col(ModelFamily.id).in_(
                select(ModelFamilyTagLink.family_id)
                .join(Tag, Tag.id == ModelFamilyTagLink.tag_id)
                .where(Tag.slug == tag, live(Tag))
            )
        )
    stmt, value = _family_sort(stmt, sort, session, user)
    stmt = stmt.with_only_columns(
        literal("family").label("kind"),
        ModelFamily.id.label("card_id"),
        value.label("sort_value"),
    )
    filters = {
        "q": q,
        "collection_id": collection_id,
        "favorites": favorites,
        "tags": tags,
        "trashed": include_trashed,
    }
    page, next_cursor, total = _page(
        session, stmt, user, "families", filters, sort, cursor, limit
    )
    cards = family_reads(
        session,
        user,
        list(
            session.exec(
                select(ModelFamily).where(
                    col(ModelFamily.id).in_([row["card_id"] for row in page])
                )
            ).all()
        ),
    )
    return FamilyPageRead(
        items=[cards[row["card_id"]] for row in page],
        total=total,
        next_cursor=next_cursor,
    )
