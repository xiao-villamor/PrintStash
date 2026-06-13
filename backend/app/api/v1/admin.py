from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import Session, select

from app.core.security import require_superuser
from app.core.time import utcnow
from app.db.models import (
    AuditLog,
    Collection,
    File,
    Model,
    PrintJob,
    Printer,
    PrinterProfile,
    Tag,
    User,
)
from app.db.session import get_session
from app.services.trash import gc_soft_deleted
from app.services.storage_backend import get_backend
from app.schemas.auth import UserCreate, UserPasswordUpdate, UserRead, UserUpdate
from app.services.auth import get_user_by_username, hash_password

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_superuser)]
)

_RESOURCE_MODEL = {
    "models": Model,
    "files": File,
    "printers": Printer,
    "printer_profiles": PrinterProfile,
    "print_jobs": PrintJob,
    "users": User,
    "tags": Tag,
    "collections": Collection,
}


@router.get("/users", response_model=list[UserRead])
def list_users(session: Session = Depends(get_session)) -> list[UserRead]:
    rows = session.exec(
        select(User).where(User.deleted_at.is_(None)).order_by(User.username)
    ).all()
    return [UserRead.model_validate(row) for row in rows]


def _active_superuser_count(session: Session) -> int:
    return len(
        session.exec(
            select(User).where(
                User.deleted_at.is_(None),
                User.is_active == True,  # noqa: E712
                User.is_superuser == True,  # noqa: E712
            )
        ).all()
    )


def _prevent_last_superuser_lockout(
    session: Session,
    user: User,
    *,
    next_is_superuser: bool | None = None,
    next_is_active: bool | None = None,
) -> None:
    if not user.is_superuser or not user.is_active:
        return
    will_be_superuser = (
        user.is_superuser if next_is_superuser is None else next_is_superuser
    )
    will_be_active = user.is_active if next_is_active is None else next_is_active
    if will_be_superuser and will_be_active:
        return
    if _active_superuser_count(session) <= 1:
        raise HTTPException(status_code=400, detail="last_superuser_required")


@router.post("/users", response_model=UserRead, status_code=201)
def create_user(
    payload: UserCreate, session: Session = Depends(get_session)
) -> UserRead:
    username = payload.username.strip()
    if get_user_by_username(session, username) is not None:
        raise HTTPException(status_code=409, detail="user_already_exists")
    user = User(
        username=username,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        is_superuser=False,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return UserRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    session: Session = Depends(get_session),
) -> UserRead:
    user = session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=404, detail="user_not_found")
    _prevent_last_superuser_lockout(
        session,
        user,
        next_is_superuser=payload.is_superuser,
        next_is_active=payload.is_active,
    )
    if "email" in payload.model_fields_set:
        user.email = payload.email
    if payload.is_superuser is not None:
        user.is_superuser = payload.is_superuser
    if payload.is_active is not None:
        user.is_active = payload.is_active
    user.updated_at = utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)
    return UserRead.model_validate(user)


@router.post("/users/{user_id}/password", response_model=UserRead)
def reset_user_password(
    user_id: int,
    payload: UserPasswordUpdate,
    session: Session = Depends(get_session),
) -> UserRead:
    user = session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=404, detail="user_not_found")
    user.hashed_password = hash_password(payload.password)
    user.updated_at = utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)
    return UserRead.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
def deactivate_user(user_id: int, session: Session = Depends(get_session)) -> Response:
    user = session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=404, detail="user_not_found")
    _prevent_last_superuser_lockout(session, user, next_is_active=False)
    user.is_active = False
    user.updated_at = utcnow()
    session.add(user)
    session.commit()
    return Response(status_code=204)


@router.delete("/{resource}/{resource_id}")
def admin_delete_resource(
    resource: str,
    resource_id: int,
    hard: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> Response:
    model = _RESOURCE_MODEL.get(resource)
    if model is None:
        raise HTTPException(status_code=404, detail="resource_not_found")
    row = session.get(model, resource_id)
    if row is None:
        raise HTTPException(status_code=404, detail="resource_id_not_found")
    if hard:
        if isinstance(row, File):
            get_backend().delete(row.path)
        session.delete(row)
    else:
        row.deleted_at = utcnow()
        session.add(row)
    session.commit()
    return Response(status_code=204)


@router.post("/{resource}/{resource_id}/restore")
def restore_resource(
    resource: str,
    resource_id: int,
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    model = _RESOURCE_MODEL.get(resource)
    if model is None:
        raise HTTPException(status_code=404, detail="resource_not_found")
    row = session.get(model, resource_id)
    if row is None:
        raise HTTPException(status_code=404, detail="resource_id_not_found")
    row.deleted_at = None
    row.deleted_by = None
    session.add(row)
    session.commit()
    return {"restored": True}


@router.get("/audit")
def list_audit(
    resource: str | None = None,
    resource_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[dict]:
    stmt = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if resource:
        stmt = stmt.where(AuditLog.resource_type == resource)
    if resource_id is not None:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    rows = session.exec(stmt).all()
    return [r.model_dump() for r in rows]


@router.post("/gc")
def run_gc() -> dict[str, int]:
    return gc_soft_deleted()
