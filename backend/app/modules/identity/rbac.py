"""Collection-level RBAC helpers."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import false, or_
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.core.errors import ErrorKind, OperationError
from app.db.models import Collection, CollectionPermission, CollectionRole, User
from app.db.scopes import live

ROLE_ORDER = {
    CollectionRole.VIEW: 1,
    CollectionRole.EDIT: 2,
    CollectionRole.ADMIN: 3,
}


def role_allows(role: CollectionRole | None, minimum: CollectionRole) -> bool:
    if role is None:
        return False
    return ROLE_ORDER[role] >= ROLE_ORDER[minimum]


def effective_collection_role(
    session: Session,
    user: User,
    collection_id: int | None,
) -> CollectionRole | None:
    if user.is_superuser:
        return CollectionRole.ADMIN
    if collection_id is None:
        return None

    collection = session.get(Collection, collection_id)
    if collection is None or collection.deleted_at is not None:
        return None

    grants = session.exec(
        select(CollectionPermission, Collection)
        .join(Collection, Collection.id == CollectionPermission.collection_id)
        .where(CollectionPermission.user_id == user.id, live(Collection))
    ).all()
    best: CollectionRole | None = None
    for permission, granted_collection in grants:
        inherited = (
            collection.path == granted_collection.path
            or collection.path.startswith(granted_collection.path + "/")
        )
        if inherited and ROLE_ORDER[permission.role] > ROLE_ORDER.get(best, 0):
            best = permission.role
    return best


def require_collection_role(
    session: Session,
    user: User,
    collection_id: int | None,
    minimum: CollectionRole,
) -> CollectionRole:
    role = effective_collection_role(session, user, collection_id)
    if role_allows(role, minimum):
        return role
    raise OperationError("collection_permission_denied", kind=ErrorKind.FORBIDDEN)


def _like_prefix(path: str) -> str:
    """Build the descendant-matching LIKE pattern for *path*.

    ``slugify`` cannot currently emit ``%`` or ``_``, so the escaping is belt
    and braces — but this is an access-control boundary, and an unescaped ``_``
    is a single-character wildcard that would widen a grant to sibling trees.
    """
    escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "/%"


def accessible_collection_ids_stmt(
    session: Session,
    user: User,
    minimum: CollectionRole = CollectionRole.VIEW,
) -> SelectOfScalar[int]:
    """SQL scope selecting every live collection id *user* reaches at *minimum*.

    Filter reads with it as a subquery (``column.in_(stmt)``). Binding the
    materialised id set into a query instead sends one parameter per visible
    collection with every statement, which is what made a 9k-collection
    library's reads grow with the size of the tree (#295).

    A grant cascades to descendants, which the materialised ``path`` turns into
    a prefix test. Only the user's own grants are read eagerly: they are few,
    and each one becomes a ``path`` predicate.
    """
    if user.is_superuser:
        return select(Collection.id).where(live(Collection))

    granted_paths = session.exec(
        select(Collection.path)
        .join(CollectionPermission, Collection.id == CollectionPermission.collection_id)  # type: ignore[arg-type]
        .where(
            CollectionPermission.user_id == user.id,
            CollectionPermission.role.in_(  # type: ignore[union-attr]
                [role for role in CollectionRole if role_allows(role, minimum)]
            ),
            live(Collection),
        )
    ).all()
    # No grant reaches nothing; an empty OR renders as false in both dialects.
    reachable = or_(
        false(),
        *[
            or_(
                Collection.path == path,
                Collection.path.like(_like_prefix(path), escape="\\"),  # type: ignore[union-attr]
            )
            for path in granted_paths
        ],
    )
    return select(Collection.id).where(live(Collection), reachable)


def accessible_collection_ids(
    session: Session,
    user: User,
    minimum: CollectionRole = CollectionRole.VIEW,
) -> set[int]:
    """Ids of every live collection *user* can reach at *minimum* or above.

    For Python-side membership checks only. Never bind the result into another
    query; filter with ``accessible_collection_ids_stmt`` as a subquery.
    """
    rows = session.exec(accessible_collection_ids_stmt(session, user, minimum)).all()
    return {int(cid) for cid in rows if cid is not None}


def effective_roles_for_collections(
    session: Session,
    user: User,
    collection_ids: Iterable[int | None],
) -> dict[int | None, CollectionRole | None]:
    """Resolve the effective role for many collections at once.

    Two queries regardless of how many ids are asked for. ``effective_collection_role``
    costs two queries *per call*, so resolving a page of rows one at a time turns
    a listing into an N+1. ``None`` maps to ``None`` (the root, which only
    superusers reach).
    """
    ids = {cid for cid in collection_ids if cid is not None}
    out: dict[int | None, CollectionRole | None] = {None: None}

    if user.is_superuser:
        out.update({cid: CollectionRole.ADMIN for cid in ids})
        out[None] = CollectionRole.ADMIN
        return out

    out.update({cid: None for cid in ids})
    if not ids:
        return out

    paths = session.exec(
        select(Collection.id, Collection.path).where(
            Collection.id.in_(ids),  # type: ignore[union-attr]
            live(Collection),
        )
    ).all()
    resolved = effective_roles_for_paths(
        session, user, ((int(cid), path) for cid, path in paths)
    )
    for cid, role in resolved.items():
        out[cid] = role
    return out


def effective_roles_for_paths(
    session: Session,
    user: User,
    collections: Iterable[tuple[int, str]],
) -> dict[int, CollectionRole | None]:
    """Resolve roles for live ``(id, path)`` rows the caller already loaded.

    One query (the user's grants) however many rows are passed, so a listing
    that has already read its collections does not bind their ids back into a
    second query to fetch the paths it holds.
    """
    if user.is_superuser:
        return {cid: CollectionRole.ADMIN for cid, _ in collections}

    grants = session.exec(
        select(Collection.path, CollectionPermission.role)
        .join(CollectionPermission, Collection.id == CollectionPermission.collection_id)  # type: ignore[arg-type]
        .where(CollectionPermission.user_id == user.id, live(Collection))
    ).all()
    out: dict[int, CollectionRole | None] = {}
    for cid, path in collections:
        best: CollectionRole | None = None
        for granted_path, role in grants:
            inherited = path == granted_path or path.startswith(granted_path + "/")
            if inherited and ROLE_ORDER[role] > ROLE_ORDER.get(best, 0):
                best = role
        out[cid] = best
    return out


def effective_roles_for_user_collection_pairs(
    session: Session,
    user_ids: Iterable[int],
    collection_ids: Iterable[int],
) -> dict[tuple[int, int], CollectionRole]:
    """Resolve inherited collection grants for many users and targets at once."""
    users = {int(user_id) for user_id in user_ids}
    collections = {int(collection_id) for collection_id in collection_ids}
    if not users or not collections:
        return {}
    target_paths = {
        int(collection_id): path
        for collection_id, path in session.exec(
            select(Collection.id, Collection.path).where(
                Collection.id.in_(collections),  # type: ignore[union-attr]
                live(Collection),
            )
        ).all()
    }
    grants: dict[int, list[tuple[str, CollectionRole]]] = {}
    for user_id, path, role in session.exec(
        select(
            CollectionPermission.user_id,
            Collection.path,
            CollectionPermission.role,
        )
        .join(Collection, Collection.id == CollectionPermission.collection_id)  # type: ignore[arg-type]
        .where(
            CollectionPermission.user_id.in_(users),  # type: ignore[union-attr]
            live(Collection),
        )
    ).all():
        grants.setdefault(int(user_id), []).append((path, role))

    result: dict[tuple[int, int], CollectionRole] = {}
    for user_id in users:
        for collection_id, path in target_paths.items():
            best: CollectionRole | None = None
            for granted_path, role in grants.get(user_id, []):
                inherited = path == granted_path or path.startswith(granted_path + "/")
                if inherited and ROLE_ORDER[role] > ROLE_ORDER.get(best, 0):
                    best = role
            if best is not None:
                result[(user_id, collection_id)] = best
    return result


def require_model_collection_role(
    session: Session,
    user: User,
    collection_id: int | None,
    minimum: CollectionRole,
) -> CollectionRole:
    if collection_id is None and not user.is_superuser:
        raise OperationError("root_collection_admin_required", kind=ErrorKind.FORBIDDEN)
    return require_collection_role(session, user, collection_id, minimum)
