"""Page the sidebar's collections and lightweight leaves without library-wide caps."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Select, case, func, literal, or_, select, tuple_, union_all
from sqlalchemy.orm import aliased
from sqlmodel import Session, col

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    Collection,
    Model,
    MultipartModel,
    MultipartModelChoice,
    MultipartModelStar,
    MultipartModelTagLink,
    Tag,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac
from app.modules.library import collection_tree
from app.modules.library.model_views.filters import _filtered_stmt
from app.modules.library.model_views.outliner import entry_response
from app.schemas.models import ModelFilters
from app.schemas.outliner import (
    OutlinerCollection,
    OutlinerCollectionLevel,
    OutlinerCollectionPage,
    OutlinerEntryLevel,
    OutlinerEntryPage,
    OutlinerKind,
    OutlinerQuery,
    OutlinerRestoreQuery,
    OutlinerRestoreRead,
    OutlinerSearchPage,
    OutlinerView,
)


class Scope(str, Enum):
    COLLECTIONS = "collections"
    ENTRIES = "entries"
    SEARCH = "search"


class Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    key: str
    name: str
    kind: OutlinerKind
    id: int = Field(gt=0, strict=True)


def _key(user: User, query: OutlinerQuery, scope: Scope) -> str:
    data = query.model_dump(mode="json", exclude={"cursor", "limit", "reveal_id"})
    for key, value in data.items():
        if isinstance(value, list):
            data[key] = sorted(set(value))
    raw = json.dumps([user.id, scope.value, data], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def _page(session: Session, statement: Select, source, query: OutlinerQuery, key: str):
    if query.cursor is not None:
        try:
            cursor = Cursor.model_validate_json(
                base64.b64decode(query.cursor, altchars=b"-_", validate=True)
            )
        except (ValueError, binascii.Error, ValidationError) as exc:
            raise OperationError("outliner_cursor_invalid") from exc
        if cursor.key != key:
            raise OperationError("outliner_cursor_invalid")
        statement = statement.where(
            tuple_(source.c.sort_name, source.c.kind, source.c.id)
            > tuple_(
                literal(cursor.name), literal(cursor.kind.value), literal(cursor.id)
            )
        )
    rows = list(
        session.execute(
            statement.order_by(source.c.sort_name, source.c.kind, source.c.id).limit(
                query.limit + 1
            )
        ).all()
    )
    return _page_result(rows, query.limit, key)


def _page_result(rows, limit: int, key: str):
    """Both ordinary reads and restoration issue interchangeable cursors."""
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        token = Cursor(
            key=key, name=last.sort_name, kind=OutlinerKind(last.kind), id=last.id
        )
        next_cursor = base64.urlsafe_b64encode(
            token.model_dump_json().encode()
        ).decode()
    return rows, next_cursor


def _visible(session: Session, user: User):
    return rbac.accessible_collection_ids_stmt(session, user)


def _require_parent(session: Session, user: User, parent: int | None) -> None:
    if (
        parent is not None
        and session.execute(
            select(col(Collection.id)).where(
                col(Collection.id) == parent,
                col(Collection.id).in_(_visible(session, user)),
            )
        ).first()
        is None
    ):
        raise OperationError("collection_not_found", kind=ErrorKind.NOT_FOUND)


def _columns(entity, kind: OutlinerKind, *, counts_only: bool):
    if counts_only:
        return (entity.collection_id.label("collection_id"),)
    return (
        entity.id.label("id"),
        entity.name.label("name"),
        entity.collection_id.label("collection_id"),
        literal(kind.value).label("kind"),
        func.lower(entity.name).label("sort_name"),
        entity.edit_version.label("edit_version"),
        entity.edit_epoch.label("edit_epoch"),
    )


def _entries(
    session: Session,
    user: User,
    query: OutlinerQuery,
    *,
    searching: bool = False,
    counts_only: bool = False,
    direct: bool = False,
):
    visible = _visible(session, user)
    allowed_multipart = select(col(MultipartModel.id)).where(
        or_(
            col(MultipartModel.collection_id).is_(None)
            if user.is_superuser
            else literal(False),
            col(MultipartModel.collection_id).in_(visible),
        )
    )
    membership = (
        select(col(MultipartModelChoice.id))
        .where(
            col(MultipartModelChoice.model_id) == col(Model.id),
            col(MultipartModelChoice.multipart_model_id).in_(allowed_multipart),
        )
        .exists()
    )
    models = _filtered_stmt(session, user, query.filters()).with_only_columns(
        *_columns(Model, OutlinerKind.MODEL, counts_only=counts_only)
    )
    # The canonical model predicate already scopes non-admins to live folders.
    # Administrators also need to exclude entries inside a trashed folder.
    if user.is_superuser:
        models = models.where(
            or_(
                col(Model.collection_id).is_(None),
                col(Model.collection_id).in_(visible),
            )
        )
    if query.view == OutlinerView.MULTIPART:
        models = models.where(literal(False))
    elif query.view == OutlinerView.COMPONENTS:
        models = models.where(membership)
    elif query.view == OutlinerView.ORGANIZED and not searching:
        models = models.where(~membership)
    multipart = select(
        *_columns(MultipartModel, OutlinerKind.MULTIPART, counts_only=counts_only)
    ).where(col(MultipartModel.id).in_(allowed_multipart))
    if query.view == OutlinerView.COMPONENTS:
        multipart = multipart.where(literal(False))
    for tag in query.tag:
        multipart = multipart.where(
            col(MultipartModel.id).in_(
                select(col(MultipartModelTagLink.multipart_model_id))
                .join(Tag, col(Tag.id) == col(MultipartModelTagLink.tag_id))
                .where(col(Tag.slug) == tag, live(Tag))
            )
        )
    if query.favorites:
        multipart = multipart.where(
            col(MultipartModel.id).in_(
                select(col(MultipartModelStar.multipart_model_id)).where(
                    col(MultipartModelStar.user_id) == user.id
                )
            )
        )
    if direct:
        models = models.where(col(Model.collection_id) == query.collection_id)
        multipart = multipart.where(
            col(MultipartModel.collection_id) == query.collection_id
        )
    return union_all(models, multipart).cte("outliner_entries")


def _counts(entries):
    direct = (
        select(entries.c.collection_id.label("id"), func.count().label("count"))
        .group_by(entries.c.collection_id)
        .cte("outliner_direct")
    )
    ancestry = (
        select(direct.c.id, direct.c.count)
        .where(direct.c.id.is_not(None))
        .cte("outliner_ancestry", recursive=True)
    )
    parent = aliased(Collection)
    ancestry = ancestry.union_all(
        select(col(parent.parent_id), ancestry.c.count)
        .join(parent, col(parent.id) == ancestry.c.id)
        .where(col(parent.parent_id).is_not(None), live(parent))
    )
    subtree = (
        select(ancestry.c.id, func.sum(ancestry.c.count).label("count"))
        .group_by(ancestry.c.id)
        .cte("outliner_subtree")
    )
    return direct, subtree


def _filtered(query: OutlinerQuery) -> bool:
    return query.filters() != ModelFilters()


def _folders(session: Session, user: User, query: OutlinerQuery, subtree):
    folders = select(col(Collection.id)).where(
        col(Collection.id).in_(_visible(session, user))
    )
    if _filtered(query):
        folders = folders.where(
            col(Collection.id).in_(select(subtree.c.id).where(subtree.c.count > 0))
        )
    return folders


def _page_counts(
    session: Session, entries, eligible, visible, ids: list[int], parent_id: int | None
):
    """Walk only the returned branches; never aggregate every ancestor first."""
    if not ids:
        return {}
    descendants = (
        select(col(Collection.id).label("root"), col(Collection.id).label("id"))
        .where(col(Collection.id).in_(ids))
        .cte("page_descendants", recursive=True)
    )
    child = aliased(Collection)
    descendants = descendants.union_all(
        select(descendants.c.root, col(child.id)).join(
            child, col(child.parent_id) == descendants.c.id
        )
    )
    direct = (
        select(entries.c.collection_id.label("id"), func.count().label("count"))
        .group_by(entries.c.collection_id)
        .cte("page_direct")
    )
    raw = (
        select(col(Model.collection_id).label("id"), func.count().label("count"))
        .where(live(Model))
        .group_by(col(Model.collection_id))
        .subquery()
    )
    totals = (
        select(
            descendants.c.root,
            func.sum(direct.c.count).label("count"),
            func.coalesce(func.sum(raw.c.count), 0).label("models"),
            (func.count(col(Collection.id)) - 1).label("folders"),
        )
        .select_from(descendants)
        .join(Collection, col(Collection.id) == descendants.c.id)
        .outerjoin(direct, direct.c.id == col(Collection.id))
        .outerjoin(raw, raw.c.id == col(Collection.id))
        .where(live(Collection), col(Collection.id).in_(visible))
        .group_by(descendants.c.root)
        .subquery()
    )
    children = (
        select(col(Collection.parent_id).label("id"), func.count().label("count"))
        .where(col(Collection.parent_id).in_(ids), col(Collection.id).in_(eligible))
        .group_by(col(Collection.parent_id))
        .subquery()
    )
    return {
        row.id: row
        for row in session.execute(
            select(
                col(Collection.id),
                func.coalesce(direct.c.count, 0).label("direct"),
                func.coalesce(totals.c.count, 0).label("total"),
                func.coalesce(children.c.count, 0).label("children"),
                totals.c.models,
                totals.c.folders,
                func.coalesce(
                    select(direct.c.count)
                    .where(direct.c.id == parent_id)
                    .scalar_subquery(),
                    0,
                ).label("parent_direct"),
            )
            .outerjoin(direct, direct.c.id == col(Collection.id))
            .outerjoin(totals, totals.c.root == col(Collection.id))
            .outerjoin(children, children.c.id == col(Collection.id))
            .where(col(Collection.id).in_(ids))
        )
    }


def collections(
    session: Session, user: User, query: OutlinerQuery
) -> OutlinerCollectionPage:
    _require_parent(session, user, query.parent_id)
    entries = _entries(session, user, query, counts_only=True)
    _, subtree = _counts(entries)
    eligible = _folders(session, user, query, subtree)
    source = select(
        col(Collection.id),
        col(Collection.name),
        func.lower(col(Collection.name)).label("sort_name"),
        literal(OutlinerKind.COLLECTION.value).label("kind"),
    ).where(col(Collection.id).in_(eligible))
    if query.parent_id is None:
        source = source.where(
            or_(
                col(Collection.parent_id).is_(None),
                col(Collection.parent_id).not_in(_visible(session, user)),
            )
        )
    else:
        source = source.where(col(Collection.parent_id) == query.parent_id)
    source = source.subquery()
    rows, cursor = _page(
        session, select(source), source, query, _key(user, query, Scope.COLLECTIONS)
    )
    revealed = None
    if query.reveal_id is not None:
        revealed = session.execute(
            select(source).where(source.c.id == query.reveal_id)
        ).first()
    all_rows = rows + (
        [revealed]
        if revealed is not None and revealed.id not in {row.id for row in rows}
        else []
    )
    ids = [row.id for row in all_rows]
    counts = _page_counts(
        session, entries, eligible, _visible(session, user), ids, query.parent_id
    )
    nodes = collection_tree.nodes_for_ids(
        session,
        user,
        ids,
        {
            id: collection_tree.SubtreeCounts(
                models=int(row.models), collections=int(row.folders)
            )
            for id, row in counts.items()
        },
    )
    by_id = {node.id: node for node in nodes}
    projected = {
        row.id: OutlinerCollection(
            **by_id[row.id].model_dump(),
            direct_entry_count=counts[row.id].direct,
            subtree_entry_count=counts[row.id].total,
            visible_child_count=counts[row.id].children,
        )
        for row in all_rows
    }
    if ids:
        parent_count = counts[ids[0]].parent_direct
    else:
        parent_entries = _entries(
            session,
            user,
            query.model_copy(update={"collection_id": query.parent_id}),
            counts_only=True,
            direct=True,
        )
        parent_count = session.execute(
            select(func.count()).select_from(parent_entries)
        ).scalar_one()
    return OutlinerCollectionPage(
        items=[projected[row.id] for row in rows],
        next_cursor=cursor,
        parent_direct_entry_count=parent_count,
        revealed=projected[revealed.id] if revealed is not None else None,
    )


def _entry_rows(session: Session, user: User, query: OutlinerQuery, *, searching: bool):
    entries = _entries(session, user, query, searching=searching)
    source = select(entries)
    if searching:
        assert query.q is not None
        needle = query.q.strip()
        source = source.where(entries.c.name.icontains(needle, autoescape=True))
        _, subtree = _counts(entries)
        folder_ids = _folders(session, user, query, subtree)
        folders = select(
            col(Collection.id),
            col(Collection.name),
            col(Collection.id).label("collection_id"),
            literal(OutlinerKind.COLLECTION.value).label("kind"),
            func.lower(col(Collection.name)).label("sort_name"),
            literal(None).label("edit_version"),
            literal(None).label("edit_epoch"),
        ).where(
            col(Collection.id).in_(folder_ids),
            col(Collection.name).icontains(needle, autoescape=True),
        )
        source = union_all(source, folders)
    else:
        _require_parent(session, user, query.collection_id)
        source = source.where(entries.c.collection_id == query.collection_id)
    source = source.subquery()
    statement = select(source, col(Collection.path).label("collection")).outerjoin(
        Collection, col(Collection.id) == source.c.collection_id
    )
    scope = Scope.SEARCH if searching else Scope.ENTRIES
    rows, cursor = _page(session, statement, source, query, _key(user, query, scope))
    labels = collection_tree.collection_labels(
        session, user, (row.collection for row in rows if row.collection is not None)
    )
    return [entry_response(row, labels) for row in rows], cursor


def entries(session: Session, user: User, query: OutlinerQuery) -> OutlinerEntryPage:
    rows, cursor = _entry_rows(session, user, query, searching=False)
    return OutlinerEntryPage.model_validate({"items": rows, "next_cursor": cursor})


def search(session: Session, user: User, query: OutlinerQuery) -> OutlinerSearchPage:
    rows, cursor = _entry_rows(session, user, query, searching=True)
    return OutlinerSearchPage(items=rows, next_cursor=cursor)


def restore(
    session: Session, user: User, request: OutlinerRestoreQuery
) -> OutlinerRestoreRead:
    """First pages for known open branches, with shared predicates and projection.

    Window limits apply per parent before any nodes are projected. Missing or
    newly inaccessible paths from session storage are ignored, never revealed.
    No cache of user-dependent server data survives the request.
    """
    query = OutlinerQuery.model_validate(
        request.model_dump(exclude={"expanded_paths", "selected_path"})
    )
    visible = _visible(session, user)
    wanted = set(request.expanded_paths)
    lineage = (
        set(ancestor for ancestor in collection_tree._prefixes(request.selected_path))
        if request.selected_path
        else set()
    )
    paths = wanted | lineage
    known = list(
        session.execute(
            select(col(Collection.id), col(Collection.path), col(Collection.parent_id))
            .where(col(Collection.path).in_(paths), col(Collection.id).in_(visible))
            .order_by(col(Collection.path))
        ).all()
    )
    parents: list[int | None] = [None, *(row.id for row in known if row.path in wanted)]
    parent_ids = [id for id in parents if id is not None]
    reveals = {
        row.parent_id if row.parent_id in {node.id for node in known} else None: row.id
        for row in known
        if row.path in lineage
    }
    entries = _entries(session, user, query)
    _, subtree = _counts(entries)
    eligible = _folders(session, user, query, subtree)
    parent_scope = case(
        (col(Collection.parent_id).in_(visible), col(Collection.parent_id)), else_=None
    )
    source = (
        select(
            col(Collection.id),
            col(Collection.name),
            func.lower(col(Collection.name)).label("sort_name"),
            literal(OutlinerKind.COLLECTION.value).label("kind"),
            parent_scope.label("scope"),
        )
        .where(
            col(Collection.id).in_(eligible),
            or_(parent_scope.is_(None), parent_scope.in_(parent_ids)),
        )
        .subquery()
    )
    ranked = select(
        source,
        func.row_number()
        .over(
            partition_by=source.c.scope,
            order_by=(source.c.sort_name, source.c.kind, source.c.id),
        )
        .label("position"),
    ).subquery()
    rows = list(
        session.execute(
            select(ranked)
            .where(
                or_(
                    ranked.c.position <= query.limit + 1,
                    ranked.c.id.in_(reveals.values()),
                )
            )
            .order_by(ranked.c.scope, ranked.c.position)
        ).all()
    )
    siblings = {parent: [] for parent in parents}
    revealed = {}
    for row in rows:
        if row.id == reveals.get(row.scope):
            revealed[row.scope] = row
        if row.position <= query.limit + 1:
            siblings[row.scope].append(row)
    pages = {
        parent: _page_result(
            siblings[parent],
            query.limit,
            _key(
                user, query.model_copy(update={"parent_id": parent}), Scope.COLLECTIONS
            ),
        )
        for parent in parents
    }
    node_ids = sorted(
        {row.id for rows, _ in pages.values() for row in rows}
        | {row.id for row in revealed.values()}
        | set(parent_ids)
    )
    counts = _page_counts(session, entries, eligible, visible, node_ids, None)
    nodes = collection_tree.nodes_for_ids(
        session,
        user,
        node_ids,
        {
            id: collection_tree.SubtreeCounts(
                models=int(row.models), collections=int(row.folders)
            )
            for id, row in counts.items()
        },
    )
    projected = {
        node.id: OutlinerCollection(
            **node.model_dump(),
            direct_entry_count=counts[node.id].direct,
            subtree_entry_count=counts[node.id].total,
            visible_child_count=counts[node.id].children,
        )
        for node in nodes
    }
    # The root count must also be available when the library has no folders.
    root_count = session.execute(
        select(func.count())
        .select_from(entries)
        .where(entries.c.collection_id.is_(None))
    ).scalar_one()
    collection_levels = [
        OutlinerCollectionLevel(
            parent_id=parent,
            page=OutlinerCollectionPage(
                items=[projected[row.id] for row in pages[parent][0]],
                next_cursor=pages[parent][1],
                parent_direct_entry_count=root_count
                if parent is None
                else counts[parent].direct,
                revealed=projected[revealed[parent].id] if parent in revealed else None,
            ),
        )
        for parent in parents
    ]
    entry_source = (
        select(
            entries,
            func.row_number()
            .over(
                partition_by=entries.c.collection_id,
                order_by=(entries.c.sort_name, entries.c.kind, entries.c.id),
            )
            .label("position"),
        )
        .where(
            or_(
                entries.c.collection_id.is_(None),
                entries.c.collection_id.in_(parent_ids),
            )
        )
        .subquery()
    )
    entry_rows = list(
        session.execute(
            select(entry_source, col(Collection.path).label("collection"))
            .outerjoin(Collection, col(Collection.id) == entry_source.c.collection_id)
            .where(entry_source.c.position <= query.limit + 1)
            .order_by(entry_source.c.collection_id, entry_source.c.position)
        ).all()
    )
    # Every entry belongs to one of the authorized, already projected parents.
    # Reuse that request-local ancestry instead of traversing the tree again.
    labels = {node.path: node.display_path for node in nodes}
    leaves = {parent: [] for parent in parents}
    for row in entry_rows:
        leaves[row.collection_id].append(row)
    entry_levels = []
    for parent in parents:
        items, cursor = _page_result(
            leaves[parent],
            query.limit,
            _key(
                user, query.model_copy(update={"collection_id": parent}), Scope.ENTRIES
            ),
        )
        entry_levels.append(
            OutlinerEntryLevel(
                collection_id=parent,
                page=OutlinerEntryPage(
                    items=[entry_response(row, labels) for row in items],
                    next_cursor=cursor,
                ),
            )
        )
    return OutlinerRestoreRead(collections=collection_levels, entries=entry_levels)
