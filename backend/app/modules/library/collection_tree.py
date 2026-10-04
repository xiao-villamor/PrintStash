"""The collection tree served a level, a lookup or a search page at a time.

Returning the whole tree on every page load is what made a 9,000-collection
library take a minute to open (#295). These reads return one page, and the
work behind a page is bounded by the page: a fixed number of statements, none
binding a parameter per collection in the library. Subtree totals still cover
the whole subtree, counted in one grouped query per page.

Visibility is the RBAC scope as a subquery. A grant reaches every descendant,
so a collection the caller can see has a subtree the caller can see; the
"roots" of a non-administrator's tree are the visible collections whose parent
they cannot see.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, func, or_
from sqlalchemy import select as sa_select
from sqlalchemy.orm import aliased
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    Collection,
    CollectionRole,
    CollectionTagLink,
    Model,
    Tag,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac
from app.schemas.models import (
    CollectionLookupRead,
    CollectionNodeRead,
    CollectionPage,
)

CHILDREN_LIMIT_MAX = 500
SEARCH_LIMIT_MAX = 50


@dataclass(frozen=True)
class _Row:
    id: int
    name: str
    slug: str
    path: str
    parent_id: int | None
    has_readme: bool
    sort_name: str


def _columns() -> tuple[Any, ...]:
    return (
        Collection.id,
        Collection.name,
        Collection.slug,
        Collection.path,
        Collection.parent_id,
        # Only whether a readme exists; the text can be large.
        func.coalesce(func.length(Collection.readme), 0) > 0,
        func.lower(Collection.name),
    )


def _encode_cursor(row: _Row) -> str:
    raw = json.dumps([row.sort_name, row.id]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _after_cursor(cursor: str | None):
    """The keyset predicate for rows after *cursor*, or ``None`` for page one."""
    if cursor is None:
        return None
    try:
        sort_name, row_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (binascii.Error, ValueError, TypeError) as exc:
        raise OperationError("collection_cursor_invalid") from exc
    if not isinstance(sort_name, str) or not isinstance(row_id, int):
        raise OperationError("collection_cursor_invalid")
    key = func.lower(Collection.name)
    return or_(key > sort_name, and_(key == sort_name, Collection.id > row_id))


def _page(
    session: Session, stmt, cursor: str | None, limit: int
) -> tuple[list[_Row], str | None]:
    after = _after_cursor(cursor)
    if after is not None:
        stmt = stmt.where(after)
    rows = [
        _Row(*row)
        for row in session.execute(
            stmt.order_by(func.lower(Collection.name), Collection.id).limit(limit + 1)
        ).all()
    ]
    if len(rows) > limit:
        return rows[:limit], _encode_cursor(rows[limit - 1])
    return rows, None


def _prefixes(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: index + 1]) for index in range(len(parts))]


@dataclass(frozen=True)
class _Subtree:
    models: int
    collections: int


def _subtree_counts(
    session: Session, rows: Sequence[_Row], visible: SelectOfScalar[int]
) -> dict[str, _Subtree]:
    """Live Models and collections below each row, keyed by path, counted in SQL.

    Walk indexed parent ids from the page rows, then group below each root.
    The recursive query returns one count row per page item without loading
    the descendants into Python or comparing every collection path to a page
    path. A deleted or inaccessible descendant contributes to neither count.
    """
    if not rows:
        return {}
    descendants = (
        sa_select(
            Collection.id.label("root_id"),
            Collection.id.label("below_id"),
        )
        .where(Collection.id.in_([row.id for row in rows]))  # type: ignore[union-attr]
        .cte("descendants", recursive=True)
    )
    child = aliased(Collection)
    descendants = descendants.union_all(
        sa_select(descendants.c.root_id, child.id).join(
            child, child.parent_id == descendants.c.below_id
        )
    )
    page = aliased(Collection)
    below = aliased(Collection)
    # Aggregate Models once per collection before joining the descendants, so
    # the recursive result has one row per collection rather than per Model.
    direct = (
        sa_select(Model.collection_id, func.count(Model.id).label("models"))
        .where(live(Model))
        .group_by(Model.collection_id)
        .subquery()
    )
    counts = {
        path: _Subtree(models=int(models), collections=int(collections))
        for path, models, collections in session.execute(
            sa_select(
                page.path,
                func.coalesce(func.sum(direct.c.models), 0),
                # The row itself is in its own subtree; the rest are below it.
                func.count(below.id) - 1,
            )
            .select_from(descendants)
            .join(page, page.id == descendants.c.root_id)
            .join(below, below.id == descendants.c.below_id)
            .outerjoin(direct, direct.c.collection_id == below.id)
            .where(
                live(below),
                below.id.in_(visible),
            )
            .group_by(page.path)
        ).all()
    }
    return {row.path: counts[row.path] for row in rows}


def _child_counts(session: Session, ids: list[int]) -> dict[int, int]:
    return dict(
        session.execute(
            sa_select(Collection.parent_id, func.count(Collection.id))
            .where(live(Collection), Collection.parent_id.in_(ids))  # type: ignore[union-attr]
            .group_by(Collection.parent_id)
        ).all()
    )


def collection_tags(
    session: Session, ids: list[int] | SelectOfScalar[int]
) -> dict[int, list[str]]:
    """Live tag names per collection, sorted; a collection without tags is absent."""
    result: dict[int, list[str]] = {}
    rows = session.exec(
        select(CollectionTagLink.collection_id, Tag.name)
        .join(Tag, Tag.id == CollectionTagLink.tag_id)
        .where(
            CollectionTagLink.collection_id.in_(ids),  # type: ignore[union-attr]
            live(Tag),
        )
        .order_by(CollectionTagLink.collection_id.asc(), Tag.name.asc())  # type: ignore[attr-defined]
    ).all()
    for collection_id, tag_name in rows:
        if collection_id is not None:
            result.setdefault(collection_id, []).append(tag_name)
    return result


def _labels(
    session: Session, paths: Iterable[str], visible: SelectOfScalar[int]
) -> dict[str, str]:
    """Each path's visible ancestry as names, root first: ``Parts/Brackets``.

    Follow indexed parent ids from each requested path. Ancestors above what
    the caller can see are left out, as the tree leaves them out. The query
    binds only the requested paths, however deep the tree is.
    """
    wanted = sorted(set(paths))
    if not wanted:
        return {}
    lineage = (
        sa_select(
            Collection.path.label("target_path"),
            Collection.id.label("ancestor_id"),
            Collection.parent_id.label("parent_id"),
        )
        .where(Collection.path.in_(wanted))  # type: ignore[union-attr]
        .cte("lineage", recursive=True)
    )
    parent = aliased(Collection)
    lineage = lineage.union_all(
        sa_select(lineage.c.target_path, parent.id, parent.parent_id).join(
            parent, parent.id == lineage.c.parent_id
        )
    )
    ancestor = aliased(Collection)
    chains: dict[str, list[tuple[str, str]]] = {}
    for path, ancestor_path, name in session.execute(
        sa_select(lineage.c.target_path, ancestor.path, ancestor.name)
        .select_from(lineage)
        .join(ancestor, ancestor.id == lineage.c.ancestor_id)
        .where(
            live(ancestor),
            ancestor.id.in_(visible),
        )
    ).all():
        chains.setdefault(path, []).append((ancestor_path, name))
    return {
        path: "/".join(
            name for _, name in sorted(chains.get(path, []), key=lambda a: len(a[0]))
        )
        for path in wanted
    }


def collection_labels(
    session: Session, user: User, paths: Iterable[str | None]
) -> dict[str, str]:
    """Labels for the collections a page of items sits in, keyed by path.

    Lets a card name its folder (``Parts/Brackets``) without the client holding
    the whole tree. Items outside any collection have no label.
    """
    return _labels(
        session,
        (path for path in paths if path is not None),
        rbac.accessible_collection_ids_stmt(session, user),
    )


def _nodes(
    session: Session,
    user: User,
    rows: Sequence[_Row],
    visible: SelectOfScalar[int],
) -> list[CollectionNodeRead]:
    ids = [row.id for row in rows]
    subtrees = _subtree_counts(session, rows, visible)
    children = _child_counts(session, ids) if ids else {}
    roles = rbac.effective_roles_for_paths(
        session, user, ((row.id, row.path) for row in rows)
    )
    tags = collection_tags(session, ids) if ids else {}
    display = _labels(session, (row.path for row in rows), visible)
    return [
        CollectionNodeRead(
            id=row.id,
            name=row.name,
            slug=row.slug,
            path=row.path,
            parent_id=row.parent_id,
            model_count=subtrees[row.path].models,
            effective_role=roles[row.id],
            tags=tags.get(row.id, []),
            has_readme=row.has_readme,
            child_count=children.get(row.id, 0),
            descendant_count=subtrees[row.path].collections,
            display_path=display[row.path],
        )
        for row in rows
    ]


def children(
    session: Session,
    user: User,
    *,
    parent_id: int | None,
    cursor: str | None,
    limit: int,
) -> CollectionPage:
    """One page of a collection's children, or of the caller's roots.

    The roots are the visible collections whose parent the caller cannot see:
    the top level for an administrator, the granted collections for anyone
    else. A parent the caller cannot see is not found, whether or not it exists.
    """
    visible = rbac.accessible_collection_ids_stmt(session, user)
    stmt = sa_select(*_columns()).where(Collection.id.in_(visible))  # type: ignore[union-attr]
    if parent_id is None:
        stmt = stmt.where(
            or_(
                Collection.parent_id.is_(None),  # type: ignore[union-attr]
                Collection.parent_id.not_in(visible),  # type: ignore[union-attr]
            )
        )
    else:
        parent_visible = session.exec(
            select(Collection.id).where(
                Collection.id == parent_id,
                Collection.id.in_(visible),  # type: ignore[union-attr]
            )
        ).first()
        if parent_visible is None:
            raise OperationError("collection_not_found", kind=ErrorKind.NOT_FOUND)
        stmt = stmt.where(Collection.parent_id == parent_id)
    rows, next_cursor = _page(session, stmt, cursor, limit)
    return CollectionPage(
        items=_nodes(session, user, rows, visible), next_cursor=next_cursor
    )


def lookup(session: Session, user: User, path: str) -> CollectionLookupRead:
    """The collection at *path* and its visible ancestors, root first."""
    visible = rbac.accessible_collection_ids_stmt(session, user)
    rows = [
        _Row(*row)
        for row in session.execute(
            sa_select(*_columns())
            .where(
                Collection.id.in_(visible),  # type: ignore[union-attr]
                Collection.path.in_(_prefixes(path)),  # type: ignore[union-attr]
            )
            .order_by(Collection.path)
        ).all()
    ]
    if not rows or rows[-1].path != path:
        raise OperationError("collection_not_found", kind=ErrorKind.NOT_FOUND)
    nodes = _nodes(session, user, rows, visible)
    return CollectionLookupRead(collection=nodes[-1], ancestors=nodes[:-1])


def lookup_by_id(
    session: Session, user: User, collection_id: int
) -> CollectionLookupRead:
    """Resolve one visible collection by id without loading a collection list."""
    visible = rbac.accessible_collection_ids_stmt(session, user)
    path = session.exec(
        select(Collection.path).where(
            Collection.id == collection_id,
            Collection.id.in_(visible),  # type: ignore[union-attr]
        )
    ).first()
    if path is None:
        raise OperationError("collection_not_found", kind=ErrorKind.NOT_FOUND)
    return lookup(session, user, path)


def search(
    session: Session,
    user: User,
    *,
    query: str,
    minimum: CollectionRole,
    cursor: str | None,
    limit: int,
) -> CollectionPage:
    """Collections whose name contains *query*, that the caller holds *minimum* on.

    For pickers: a destination for an upload needs ``edit``. An empty query
    pages through every such collection by name.
    """
    visible = rbac.accessible_collection_ids_stmt(session, user)
    allowed = rbac.accessible_collection_ids_stmt(session, user, minimum)
    stmt = sa_select(*_columns()).where(Collection.id.in_(allowed))  # type: ignore[union-attr]
    needle = query.strip().lower()
    if needle:
        stmt = stmt.where(func.lower(Collection.name).contains(needle, autoescape=True))
    rows, next_cursor = _page(session, stmt, cursor, limit)
    return CollectionPage(
        items=_nodes(session, user, rows, visible), next_cursor=next_cursor
    )


def nodes_for_ids(
    session: Session, user: User, ids: list[int]
) -> list[CollectionNodeRead]:
    """Project an already bounded page through the canonical collection read model."""
    if not ids:
        return []
    visible = rbac.accessible_collection_ids_stmt(session, user)
    rows = [
        _Row(*row)
        for row in session.execute(
            sa_select(*_columns()).where(
                Collection.id.in_(ids), Collection.id.in_(visible)
            )
        ).all()
    ]
    return _nodes(session, user, rows, visible)
