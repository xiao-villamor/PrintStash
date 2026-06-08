from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import Session, select

from app.core.security import require_superuser
from app.core.time import utcnow
from app.db.models import (
    AuditLog,
    Category,
    File,
    Model,
    PrintJob,
    Printer,
    PrinterProfile,
    Tag,
    User,
)
from app.db.session import get_session
from app.services.lifecycle import gc_soft_deleted
from app.services.storage_backend import get_backend

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_superuser)])

_RESOURCE_MODEL = {
    "models": Model,
    "files": File,
    "printers": Printer,
    "printer_profiles": PrinterProfile,
    "print_jobs": PrintJob,
    "users": User,
    "tags": Tag,
    "categories": Category,
}


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
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    if resource:
        stmt = stmt.where(AuditLog.resource_type == resource)
    if resource_id is not None:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    rows = session.exec(stmt).all()
    return [r.model_dump() for r in rows]


@router.post("/gc")
def run_gc() -> dict[str, int]:
    return gc_soft_deleted()
