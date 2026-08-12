from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    File,
    FileType,
    Printer,
    PrinterMaintenanceLog,
    PrinterMaintenanceWindow,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
    User,
)
from app.db.scopes import live
from app.schemas.fleet import (
    MaintenanceLogCreate,
    MaintenanceLogUpdate,
    MaintenanceWindowCreate,
    MaintenanceWindowUpdate,
    PrinterRoutingUpdate,
    QueueJobCreate,
    QueueJobUpdate,
)
from app.services.printer_files import build_traceable_remote_filename
from app.services.printer_provider import capabilities_for_provider


class FleetError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_ACTIVE_STATES = {
    PrintJobState.QUEUED,
    PrintJobState.UPLOADING,
    PrintJobState.STARTED,
    PrintJobState.PRINTING,
    PrintJobState.PAUSED,
}


@dataclass(frozen=True)
class RoutingSnapshot:
    printers: tuple[Printer, ...]
    active_maintenance_ids: frozenset[int]
    active_counts: dict[int, int]


def build_routing_snapshot(session: Session) -> RoutingSnapshot:
    now = utcnow()
    printers = tuple(
        session.exec(select(Printer).where(live(Printer)).order_by(Printer.id)).all()
    )
    printer_ids = {int(row.id) for row in printers if row.id is not None}
    maintenance_ids = (
        {
            int(printer_id)
            for printer_id in session.exec(
                select(PrinterMaintenanceWindow.printer_id).where(
                    PrinterMaintenanceWindow.printer_id.in_(printer_ids),  # type: ignore[union-attr]
                    live(PrinterMaintenanceWindow),
                    PrinterMaintenanceWindow.starts_at <= now,
                    PrinterMaintenanceWindow.ends_at > now,
                )
            ).all()
        }
        if printer_ids
        else set()
    )
    counts = {
        int(printer_id): int(count)
        for printer_id, count in session.exec(
            select(PrintJob.printer_id, func.count(PrintJob.id))
            .where(
                PrintJob.printer_id.in_(printer_ids),  # type: ignore[union-attr]
                PrintJob.state.in_(_ACTIVE_STATES),
            )
            .group_by(PrintJob.printer_id)
        ).all()
        if printer_id is not None
    }
    return RoutingSnapshot(
        printers=printers,
        active_maintenance_ids=frozenset(maintenance_ids),
        active_counts=counts,
    )


def _active_maintenance(session: Session, printer_id: int) -> bool:
    now = utcnow()
    return (
        session.exec(
            select(PrinterMaintenanceWindow).where(
                PrinterMaintenanceWindow.printer_id == printer_id,
                live(PrinterMaintenanceWindow),
                PrinterMaintenanceWindow.starts_at <= now,
                PrinterMaintenanceWindow.ends_at > now,
            )
        ).first()
        is not None
    )


def _eligible(
    session: Session,
    printer: Printer,
    snapshot: RoutingSnapshot | None = None,
) -> bool:
    caps = capabilities_for_provider(printer.provider)
    maintained = (
        int(printer.id or 0) in snapshot.active_maintenance_ids
        if snapshot is not None
        else _active_maintenance(session, printer.id or 0)
    )
    return (
        printer.deleted_at is None
        and not printer.drain_mode
        and printer.status == PrinterStatus.READY
        and caps.can_upload
        and caps.can_start
        and not maintained
    )


def _active_counts(session: Session) -> dict[int, int]:
    return {
        int(printer_id): int(count)
        for printer_id, count in session.exec(
            select(PrintJob.printer_id, func.count(PrintJob.id))
            .where(PrintJob.state.in_(_ACTIVE_STATES))
            .group_by(PrintJob.printer_id)
        ).all()
        if printer_id is not None
    }


def choose_printer(
    session: Session,
    strategy: RoutingStrategy,
    requested_printer_id: int | None,
    *,
    snapshot: RoutingSnapshot | None = None,
) -> tuple[Printer | None, str | None]:
    printers = list(
        snapshot.printers
        if snapshot is not None
        else session.exec(
            select(Printer).where(live(Printer)).order_by(Printer.id)
        ).all()
    )
    if strategy == RoutingStrategy.MANUAL:
        printer = next(
            (row for row in printers if row.id == requested_printer_id), None
        )
        if printer is None:
            raise FleetError("printer_not_found")
        return (
            printer,
            None if _eligible(session, printer, snapshot) else "printer_unavailable",
        )
    if strategy == RoutingStrategy.DEFAULT:
        printer = next((row for row in printers if row.is_default), None)
        if printer is None:
            return None, "default_printer_missing"
        return (
            printer,
            None
            if _eligible(session, printer, snapshot)
            else "default_printer_unavailable",
        )

    eligible = [row for row in printers if _eligible(session, row, snapshot)]
    if not eligible:
        return None, "no_eligible_printer"
    counts = snapshot.active_counts if snapshot is not None else _active_counts(session)
    oldest = datetime.min
    eligible.sort(
        key=lambda row: (
            counts.get(row.id or 0, 0),
            row.last_seen_at or oldest,
            row.id or 0,
        )
    )
    return eligible[0], None


def enqueue_job(
    session: Session,
    payload: QueueJobCreate,
    current_user: User,
) -> PrintJob:
    artifact = session.get(File, payload.file_id)
    if artifact is None or artifact.deleted_at is not None:
        raise FleetError("file_not_found")
    if artifact.file_type != FileType.GCODE:
        raise FleetError("file_not_gcode")
    if Path(artifact.original_filename).suffix.lower() == ".bgcode":
        raise FleetError("binary_gcode_not_printable")

    printer, blocked_reason = choose_printer(
        session, payload.strategy, payload.printer_id
    )
    queued = session.exec(
        select(PrintJob).where(PrintJob.state == PrintJobState.QUEUED)
    ).all()
    job = PrintJob(
        printer_id=printer.id if printer else None,
        printer_name=printer.name if printer else None,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename=build_traceable_remote_filename(artifact),
        state=PrintJobState.QUEUED,
        routing_strategy=payload.strategy,
        queue_position=max((row.queue_position for row in queued), default=0) + 1,
        blocked_reason=blocked_reason,
        spool_id=payload.spool_id,
        spool_name=payload.spool_name,
        spool_filament_id=payload.spool_filament_id,
        requested_by=current_user.id,
        created_by=current_user.id,
        updated_by=current_user.id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def list_queue(session: Session) -> list[PrintJob]:
    return list_queue_page(session)


def list_queue_page(
    session: Session,
    *,
    history_limit: int = 20,
    history_offset: int = 0,
    visible_printer_ids: set[int] | None = None,
) -> list[PrintJob]:
    if visible_printer_ids is not None and not visible_printer_ids:
        return []
    visibility = (
        PrintJob.printer_id.in_(visible_printer_ids)  # type: ignore[union-attr]
        if visible_printer_ids is not None
        else True
    )
    active = list(
        session.exec(
            select(PrintJob)
            .where(PrintJob.state.in_(_ACTIVE_STATES), visibility)
            .order_by(
                PrintJob.queue_position,
                PrintJob.created_at,
                PrintJob.id,
            )
        ).all()
    )
    terminal = list(
        session.exec(
            select(PrintJob)
            .where(
                PrintJob.state.notin_(_ACTIVE_STATES),  # type: ignore[union-attr]
                visibility,
            )
            .order_by(
                PrintJob.finished_at.desc(),  # type: ignore[union-attr]
                PrintJob.created_at.desc(),  # type: ignore[attr-defined]
                PrintJob.id.desc(),  # type: ignore[union-attr]
            )
            .offset(history_offset)
            .limit(history_limit)
        ).all()
    )
    return [*active, *terminal]


def _queued_job(session: Session, job_id: int) -> PrintJob:
    job = session.get(PrintJob, job_id)
    if job is None or job.deleted_at is not None:
        raise FleetError("queue_job_not_found")
    if job.state != PrintJobState.QUEUED:
        raise FleetError("queue_job_not_editable")
    return job


def update_queue_job(
    session: Session,
    job_id: int,
    payload: QueueJobUpdate,
    current_user: User,
) -> PrintJob:
    job = _queued_job(session, job_id)
    if payload.expected_updated_at and job.updated_at != payload.expected_updated_at:
        raise FleetError("queue_job_changed")
    if payload.strategy is not None or "printer_id" in payload.model_fields_set:
        strategy = payload.strategy or job.routing_strategy
        if strategy == RoutingStrategy.MANUAL and payload.printer_id is None:
            raise FleetError("printer_id_required")
        printer, blocked = choose_printer(session, strategy, payload.printer_id)
        job.routing_strategy = strategy
        job.printer_id = printer.id if printer else None
        job.printer_name = printer.name if printer else None
        job.blocked_reason = blocked
    if payload.queue_position is not None:
        queued = list(
            session.exec(
                select(PrintJob)
                .where(PrintJob.state == PrintJobState.QUEUED)
                .order_by(PrintJob.queue_position, PrintJob.created_at, PrintJob.id)
            ).all()
        )
        queued = [row for row in queued if row.id != job.id]
        index = min(payload.queue_position - 1, len(queued))
        queued.insert(index, job)
        for position, row in enumerate(queued, start=1):
            row.queue_position = position
            row.updated_at = utcnow()
            session.add(row)
    job.updated_by = current_user.id
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def cancel_queue_job(session: Session, job_id: int, current_user: User) -> PrintJob:
    job = _queued_job(session, job_id)
    job.state = PrintJobState.CANCELLED
    job.finished_at = utcnow()
    job.blocked_reason = None
    job.updated_by = current_user.id
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def retry_queue_job(session: Session, job_id: int, current_user: User) -> PrintJob:
    job = session.get(PrintJob, job_id)
    if job is None or job.deleted_at is not None:
        raise FleetError("queue_job_not_found")
    if job.state != PrintJobState.FAILED or not job.retryable:
        raise FleetError("queue_job_not_retryable")
    requested_printer_id = (
        job.printer_id if job.routing_strategy == RoutingStrategy.MANUAL else None
    )
    printer, blocked = choose_printer(
        session, job.routing_strategy, requested_printer_id
    )
    job.printer_id = printer.id if printer else None
    job.printer_name = printer.name if printer else None
    job.blocked_reason = blocked
    job.state = PrintJobState.QUEUED
    job.error = None
    job.retryable = False
    job.finished_at = None
    job.dispatch_claimed_at = None
    job.updated_by = current_user.id
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def update_routing(
    session: Session,
    printer_id: int,
    payload: PrinterRoutingUpdate,
    current_user: User,
) -> Printer:
    printer = session.get(Printer, printer_id)
    if printer is None or printer.deleted_at is not None:
        raise FleetError("printer_not_found")
    if payload.is_default is not None:
        if payload.is_default:
            for row in session.exec(
                select(Printer).where(Printer.is_default == True)  # noqa: E712
            ).all():
                row.is_default = False
                row.updated_by = current_user.id
                row.updated_at = utcnow()
                session.add(row)
        printer.is_default = payload.is_default
    if payload.drain_mode is not None:
        printer.drain_mode = payload.drain_mode
        printer.drain_updated_at = utcnow()
        if not payload.drain_mode and "drain_reason" not in payload.model_fields_set:
            printer.drain_reason = None
    if "drain_reason" in payload.model_fields_set:
        printer.drain_reason = payload.drain_reason
    printer.updated_by = current_user.id
    printer.updated_at = utcnow()
    session.add(printer)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise FleetError("default_printer_conflict") from exc
    session.refresh(printer)
    return printer


def _printer_or_error(session: Session, printer_id: int) -> Printer:
    printer = session.get(Printer, printer_id)
    if printer is None or printer.deleted_at is not None:
        raise FleetError("printer_not_found")
    return printer


def create_maintenance_window(
    session: Session,
    printer_id: int,
    payload: MaintenanceWindowCreate,
    current_user: User,
) -> PrinterMaintenanceWindow:
    _printer_or_error(session, printer_id)
    row = PrinterMaintenanceWindow(
        printer_id=printer_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        reason=payload.reason,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_maintenance_windows(
    session: Session, printer_id: int
) -> list[PrinterMaintenanceWindow]:
    _printer_or_error(session, printer_id)
    return list(
        session.exec(
            select(PrinterMaintenanceWindow)
            .where(
                PrinterMaintenanceWindow.printer_id == printer_id,
                live(PrinterMaintenanceWindow),
            )
            .order_by(PrinterMaintenanceWindow.starts_at)
        ).all()
    )


def update_maintenance_window(
    session: Session,
    printer_id: int,
    window_id: int,
    payload: MaintenanceWindowUpdate,
    current_user: User,
) -> PrinterMaintenanceWindow:
    row = session.get(PrinterMaintenanceWindow, window_id)
    if row is None or row.printer_id != printer_id or row.deleted_at is not None:
        raise FleetError("maintenance_window_not_found")
    starts_at = payload.starts_at or row.starts_at
    ends_at = payload.ends_at or row.ends_at
    if ends_at <= starts_at:
        raise FleetError("maintenance_window_invalid")
    row.starts_at = starts_at
    row.ends_at = ends_at
    if "reason" in payload.model_fields_set:
        row.reason = payload.reason
    row.updated_by = current_user.id
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_maintenance_window(
    session: Session, printer_id: int, window_id: int, current_user: User
) -> None:
    row = session.get(PrinterMaintenanceWindow, window_id)
    if row is None or row.printer_id != printer_id or row.deleted_at is not None:
        raise FleetError("maintenance_window_not_found")
    row.deleted_at = utcnow()
    row.deleted_by = current_user.id
    row.updated_by = current_user.id
    row.updated_at = utcnow()
    session.add(row)
    session.commit()


def create_maintenance_log(
    session: Session,
    printer_id: int,
    payload: MaintenanceLogCreate,
    current_user: User,
) -> PrinterMaintenanceLog:
    _printer_or_error(session, printer_id)
    row = PrinterMaintenanceLog(
        printer_id=printer_id,
        performed_at=payload.performed_at or utcnow(),
        category=payload.category.strip(),
        note=payload.note.strip(),
        counter_value=payload.counter_value,
        counter_unit=payload.counter_unit,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_maintenance_log(
    session: Session, printer_id: int
) -> list[PrinterMaintenanceLog]:
    _printer_or_error(session, printer_id)
    return list(
        session.exec(
            select(PrinterMaintenanceLog)
            .where(
                PrinterMaintenanceLog.printer_id == printer_id,
                live(PrinterMaintenanceLog),
            )
            .order_by(PrinterMaintenanceLog.performed_at.desc())
        ).all()
    )


def update_maintenance_log(
    session: Session,
    printer_id: int,
    log_id: int,
    payload: MaintenanceLogUpdate,
    current_user: User,
) -> PrinterMaintenanceLog:
    row = session.get(PrinterMaintenanceLog, log_id)
    if row is None or row.printer_id != printer_id or row.deleted_at is not None:
        raise FleetError("maintenance_log_not_found")
    for field in (
        "performed_at",
        "category",
        "note",
        "counter_value",
        "counter_unit",
    ):
        if field in payload.model_fields_set:
            value = getattr(payload, field)
            if isinstance(value, str):
                value = value.strip()
            setattr(row, field, value)
    row.updated_by = current_user.id
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_maintenance_log(
    session: Session, printer_id: int, log_id: int, current_user: User
) -> None:
    row = session.get(PrinterMaintenanceLog, log_id)
    if row is None or row.printer_id != printer_id or row.deleted_at is not None:
        raise FleetError("maintenance_log_not_found")
    row.deleted_at = utcnow()
    row.deleted_by = current_user.id
    row.updated_by = current_user.id
    row.updated_at = utcnow()
    session.add(row)
    session.commit()


def fleet_summary(
    session: Session, printer_ids: set[int] | None = None
) -> dict[str, int]:
    printer_stmt = select(Printer).where(live(Printer))
    job_stmt = select(PrintJob).where(live(PrintJob))
    window_stmt = select(PrinterMaintenanceWindow).where(live(PrinterMaintenanceWindow))
    if printer_ids is not None:
        printer_stmt = printer_stmt.where(Printer.id.in_(printer_ids))  # type: ignore[union-attr]
        job_stmt = job_stmt.where(PrintJob.printer_id.in_(printer_ids))  # type: ignore[union-attr]
        window_stmt = window_stmt.where(
            PrinterMaintenanceWindow.printer_id.in_(printer_ids)  # type: ignore[union-attr]
        )
    printers = list(session.exec(printer_stmt).all()) if printer_ids != set() else []
    jobs = list(session.exec(job_stmt).all()) if printer_ids != set() else []
    now = utcnow()
    maintenance_printers = {
        row.printer_id
        for row in session.exec(
            window_stmt.where(
                PrinterMaintenanceWindow.starts_at <= now,
                PrinterMaintenanceWindow.ends_at > now,
            )
        ).all()
    }
    active_states = {
        PrintJobState.UPLOADING,
        PrintJobState.STARTED,
        PrintJobState.PRINTING,
        PrintJobState.PAUSED,
    }
    return {
        "total_printers": len(printers),
        "queued_jobs": sum(row.state == PrintJobState.QUEUED for row in jobs),
        "active_jobs": sum(row.state in active_states for row in jobs),
        "draining_printers": sum(row.drain_mode for row in printers),
        "maintenance_printers": len(maintenance_printers),
        "attention_jobs": sum(
            row.state == PrintJobState.FAILED
            or (row.state == PrintJobState.QUEUED and row.blocked_reason is not None)
            for row in jobs
        ),
    }
