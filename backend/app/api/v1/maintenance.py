from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.core.security import require_superuser
from app.db.models import User, VaultAuditRun
from app.db.session import get_session
from app.schemas.maintenance import (
    VaultAuditCreate,
    VaultAuditFindingRead,
    VaultAuditRunRead,
)
from app.services import vault_audit

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.post("/audits", response_model=VaultAuditRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_audit(
    payload: VaultAuditCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditRunRead:
    row, created = vault_audit.create_run(session, current_user.id, payload.mode)
    if created:
        background_tasks.add_task(vault_audit.execute_run, row.id)
    return vault_audit.read_run(session, row)


@router.get("/audits", response_model=list[VaultAuditRunRead])
def list_audits(
    limit: int = Query(25, ge=1, le=100),
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> list[VaultAuditRunRead]:
    return vault_audit.list_runs(session, limit)


@router.get("/audits/latest", response_model=VaultAuditRunRead)
def latest_audit(
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditRunRead:
    row = vault_audit.latest_run(session)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")
    return vault_audit.read_run(session, row)


@router.get("/audits/{run_id}", response_model=VaultAuditRunRead)
def get_audit(
    run_id: int,
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditRunRead:
    row = session.get(VaultAuditRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")
    return vault_audit.read_run(session, row)


@router.post("/audits/{run_id}/cancel", response_model=VaultAuditRunRead)
def cancel_audit(
    run_id: int,
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditRunRead:
    row = vault_audit.request_cancel(session, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")
    return vault_audit.read_run(session, row)


@router.post("/findings/{finding_id}/repair", response_model=VaultAuditFindingRead)
def repair_finding(
    finding_id: int,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditFindingRead:
    row = vault_audit.repair_finding(session, finding_id, current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_finding_not_found")
    if row.state.value != "resolved":
        raise HTTPException(status_code=409, detail="audit_repair_not_available")
    return vault_audit.finding_read(row)


@router.post("/findings/{finding_id}/ignore", response_model=VaultAuditFindingRead)
def ignore_finding(
    finding_id: int,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditFindingRead:
    row = vault_audit.ignore_finding(session, finding_id, current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_finding_not_found")
    return vault_audit.finding_read(row)
