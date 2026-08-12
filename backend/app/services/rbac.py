"""Collection-level RBAC helpers."""

from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlmodel import Session, select

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
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="collection_permission_denied",
    )


def _like_prefix(path: str) -> str:
    """Build the descendant-matching LIKE pattern for *path*.

    ``slugify`` cannot currently emit ``%`` or ``_``, so the escaping is belt
    and braces — but this is an access-control boundary, and an unescaped ``_``
    is a single-character wildcard that would widen a grant to sibling trees.
    """
    escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "/%"


def accessible_collection_ids(
    session: Session,
    user: User,
    minimum: CollectionRole = CollectionRole.VIEW,
) -> set[int]:
    """Ids of every live collection *user* can reach at *minimum* or above.

    A grant cascades to descendants, which the materialised ``path`` turns into
    a prefix test. Grant filtering and cascade both run in SQL; the previous
    version compared every live collection against every grant in Python, on
    every permission check.
    """
    if user.is_superuser:
        rows = session.exec(select(Collection.id).where(live(Collection))).all()
        return {int(cid) for cid in rows if cid is not None}

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
    if not granted_paths:
        return set()

    reachable = or_(
        *[
            or_(
                Collection.path == path,
                Collection.path.like(_like_prefix(path), escape="\\"),  # type: ignore[union-attr]
            )
            for path in granted_paths
        ]
    )
    rows = session.exec(select(Collection.id).where(live(Collection), reachable)).all()
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

    grants = session.exec(
        select(Collection.path, CollectionPermission.role)
        .join(CollectionPermission, Collection.id == CollectionPermission.collection_id)  # type: ignore[arg-type]
        .where(CollectionPermission.user_id == user.id, live(Collection))
    ).all()
    if not grants:
        return out

    for cid, path in paths:
        best: CollectionRole | None = None
        for granted_path, role in grants:
            inherited = path == granted_path or path.startswith(granted_path + "/")
            if inherited and ROLE_ORDER[role] > ROLE_ORDER.get(best, 0):
                best = role
        out[int(cid)] = best
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="root_collection_admin_required",
        )
    return require_collection_role(session, user, collection_id, minimum)
