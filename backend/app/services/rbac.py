"""Collection-level RBAC helpers."""

from __future__ import annotations

from fastapi import HTTPException, status
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


def accessible_collection_ids(
    session: Session,
    user: User,
    minimum: CollectionRole = CollectionRole.VIEW,
) -> set[int]:
    collections = list(session.exec(select(Collection).where(live(Collection))).all())
    if user.is_superuser:
        return {int(c.id) for c in collections if c.id is not None}

    grants = session.exec(
        select(CollectionPermission, Collection)
        .join(Collection, Collection.id == CollectionPermission.collection_id)
        .where(CollectionPermission.user_id == user.id, live(Collection))
    ).all()
    out: set[int] = set()
    for collection in collections:
        if collection.id is None:
            continue
        for permission, granted_collection in grants:
            inherited = (
                collection.path == granted_collection.path
                or collection.path.startswith(granted_collection.path + "/")
            )
            if inherited and role_allows(permission.role, minimum):
                out.add(collection.id)
                break
    return out


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
