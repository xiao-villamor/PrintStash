from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session

from app.core.security import require_user
from app.db.models import (
    CollectionRole,
    File,
    Model,
    PrinterRole,
    PrintJob,
    RoutingStrategy,
    User,
)
from app.db.session import get_session
from app.schemas.fleet import (
    FleetSummary,
    MaintenanceLogCreate,
    MaintenanceLogRead,
    MaintenanceLogUpdate,
    MaintenanceWindowCreate,
    MaintenanceWindowRead,
    MaintenanceWindowUpdate,
    PrinterRoutingRead,
    PrinterRoutingUpdate,
    QueueJobCreate,
    QueueJobUpdate,
)
from app.schemas.printers import PrintJobRead
from app.services import fleet, printer_rbac, rbac
from app.services.task_queue import TaskEnvelope, task_queue

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("/summary", response_model=FleetSummary)
def get_fleet_summary(
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> FleetSummary:
    printer_ids = printer_rbac.accessible_printer_ids(session, current_user)
    return FleetSummary(**fleet.fleet_summary(session, printer_ids))


@router.get("/queue", response_model=list[PrintJobRead])
def get_queue(
    history_limit: int = Query(default=20, ge=0, le=100),
    history_offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[PrintJobRead]:
    rows = fleet.list_queue_page(
        session,
        history_limit=history_limit,
        history_offset=history_offset,
    )
    visible_ids = printer_rbac.accessible_printer_ids(session, current_user)
    rows = [job for job in rows if job.printer_id in visible_ids]
    return [PrintJobRead(**job.model_dump()) for job in rows]


@router.post(
    "/queue",
    response_model=PrintJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_queue_job(
    payload: QueueJobCreate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> PrintJobRead:
    if not current_user.is_superuser:
        if payload.strategy != RoutingStrategy.MANUAL or payload.printer_id is None:
            raise HTTPException(status_code=403, detail="printer_permission_denied")
        printer_rbac.require_printer_role(
            session, current_user, payload.printer_id, PrinterRole.PRINT
        )
    elif payload.printer_id is not None:
        printer_rbac.require_printer_role(
            session, current_user, payload.printer_id, PrinterRole.PRINT
        )
    artifact = session.get(File, payload.file_id)
    if artifact is not None:
        model = session.get(Model, artifact.model_id)
        if model is not None:
            rbac.require_model_collection_role(
                session, current_user, model.collection_id, CollectionRole.EDIT
            )
    try:
        job = fleet.enqueue_job(session, payload, current_user)
    except fleet.FleetError as exc:
        status_code = (
            404 if exc.code in {"file_not_found", "printer_not_found"} else 400
        )
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
    await task_queue.enqueue(
        TaskEnvelope(job_id=str(job.id), kind="fleet_dispatch", payload={})
    )
    return PrintJobRead(**job.model_dump())


def _queue_error(exc: fleet.FleetError) -> HTTPException:
    if exc.code in {"queue_job_not_found", "printer_not_found"}:
        return HTTPException(status_code=404, detail=exc.code)
    if exc.code in {"queue_job_not_editable", "queue_job_changed"}:
        return HTTPException(status_code=409, detail=exc.code)
    return HTTPException(status_code=400, detail=exc.code)


def _require_queue_job_role(
    session: Session, current_user: User, job_id: int, minimum: PrinterRole
) -> PrintJob:
    job = session.get(PrintJob, job_id)
    if job is None or job.deleted_at is not None:
        raise HTTPException(status_code=404, detail="queue_job_not_found")
    if job.printer_id is None:
        if current_user.is_superuser:
            return job
        raise HTTPException(status_code=404, detail="queue_job_not_found")
    printer_rbac.require_printer_role(session, current_user, job.printer_id, minimum)
    return job


@router.patch("/queue/{job_id}", response_model=PrintJobRead)
def patch_queue_job(
    job_id: int,
    payload: QueueJobUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> PrintJobRead:
    _require_queue_job_role(session, current_user, job_id, PrinterRole.PRINT)
    if not current_user.is_superuser and payload.strategy not in {
        None,
        RoutingStrategy.MANUAL,
    }:
        raise HTTPException(status_code=403, detail="printer_permission_denied")
    if payload.printer_id is not None:
        printer_rbac.require_printer_role(
            session, current_user, payload.printer_id, PrinterRole.PRINT
        )
    try:
        job = fleet.update_queue_job(session, job_id, payload, current_user)
    except fleet.FleetError as exc:
        raise _queue_error(exc) from exc
    return PrintJobRead(**job.model_dump())


@router.delete("/queue/{job_id}", response_model=PrintJobRead)
def delete_queue_job(
    job_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> PrintJobRead:
    _require_queue_job_role(session, current_user, job_id, PrinterRole.PRINT)
    try:
        job = fleet.cancel_queue_job(session, job_id, current_user)
    except fleet.FleetError as exc:
        raise _queue_error(exc) from exc
    return PrintJobRead(**job.model_dump())


@router.post("/queue/{job_id}/retry", response_model=PrintJobRead)
async def retry_queue_job(
    job_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> PrintJobRead:
    _require_queue_job_role(session, current_user, job_id, PrinterRole.PRINT)
    try:
        job = fleet.retry_queue_job(session, job_id, current_user)
    except fleet.FleetError as exc:
        raise _queue_error(exc) from exc
    await task_queue.enqueue(
        TaskEnvelope(job_id=str(job.id), kind="fleet_dispatch", payload={})
    )
    return PrintJobRead(**job.model_dump())


@router.patch(
    "/printers/{printer_id}/routing",
    response_model=PrinterRoutingRead,
)
def patch_printer_routing(
    printer_id: int,
    payload: PrinterRoutingUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> PrinterRoutingRead:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.ADMIN
    )
    try:
        printer = fleet.update_routing(session, printer_id, payload, current_user)
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return PrinterRoutingRead(
        printer_id=printer.id,  # type: ignore[arg-type]
        is_default=printer.is_default,
        drain_mode=printer.drain_mode,
        drain_reason=printer.drain_reason,
        drain_updated_at=printer.drain_updated_at,
    )


@router.get(
    "/printers/{printer_id}/maintenance-windows",
    response_model=list[MaintenanceWindowRead],
)
def get_maintenance_windows(
    printer_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[MaintenanceWindowRead]:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.VIEW
    )
    try:
        rows = fleet.list_maintenance_windows(session, printer_id)
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return [MaintenanceWindowRead(**row.model_dump()) for row in rows]


@router.post(
    "/printers/{printer_id}/maintenance-windows",
    response_model=MaintenanceWindowRead,
    status_code=status.HTTP_201_CREATED,
)
def post_maintenance_window(
    printer_id: int,
    payload: MaintenanceWindowCreate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MaintenanceWindowRead:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.ADMIN
    )
    try:
        row = fleet.create_maintenance_window(
            session, printer_id, payload, current_user
        )
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return MaintenanceWindowRead(**row.model_dump())


@router.patch(
    "/printers/{printer_id}/maintenance-windows/{window_id}",
    response_model=MaintenanceWindowRead,
)
def patch_maintenance_window(
    printer_id: int,
    window_id: int,
    payload: MaintenanceWindowUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MaintenanceWindowRead:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.ADMIN
    )
    try:
        row = fleet.update_maintenance_window(
            session, printer_id, window_id, payload, current_user
        )
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return MaintenanceWindowRead(**row.model_dump())


@router.delete(
    "/printers/{printer_id}/maintenance-windows/{window_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_maintenance_window(
    printer_id: int,
    window_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.ADMIN
    )
    try:
        fleet.delete_maintenance_window(session, printer_id, window_id, current_user)
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/printers/{printer_id}/maintenance-log",
    response_model=list[MaintenanceLogRead],
)
def get_maintenance_log(
    printer_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[MaintenanceLogRead]:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.VIEW
    )
    try:
        rows = fleet.list_maintenance_log(session, printer_id)
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return [MaintenanceLogRead(**row.model_dump()) for row in rows]


@router.post(
    "/printers/{printer_id}/maintenance-log",
    response_model=MaintenanceLogRead,
    status_code=status.HTTP_201_CREATED,
)
def post_maintenance_log(
    printer_id: int,
    payload: MaintenanceLogCreate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MaintenanceLogRead:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.ADMIN
    )
    try:
        row = fleet.create_maintenance_log(session, printer_id, payload, current_user)
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return MaintenanceLogRead(**row.model_dump())


@router.patch(
    "/printers/{printer_id}/maintenance-log/{log_id}",
    response_model=MaintenanceLogRead,
)
def patch_maintenance_log(
    printer_id: int,
    log_id: int,
    payload: MaintenanceLogUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MaintenanceLogRead:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.ADMIN
    )
    try:
        row = fleet.update_maintenance_log(
            session, printer_id, log_id, payload, current_user
        )
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return MaintenanceLogRead(**row.model_dump())


@router.delete(
    "/printers/{printer_id}/maintenance-log/{log_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_maintenance_log(
    printer_id: int,
    log_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    printer_rbac.require_printer_role(
        session, current_user, printer_id, PrinterRole.ADMIN
    )
    try:
        fleet.delete_maintenance_log(session, printer_id, log_id, current_user)
    except fleet.FleetError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
