from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    ArtifactMaterialRequirement,
    CompatibilityPolicy,
    File,
    FileType,
    JobPriority,
    MaterialSlotState,
    MaterialSource,
    Metadata,
    OperatorGateState,
    PrintBatch,
    Printer,
    PrinterMaintenanceLog,
    PrinterMaintenanceWindow,
    PrinterMaterialSlot,
    PrinterStatus,
    PrinterTool,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
    User,
)
from app.db.scopes import live
from app.schemas.fleet import (
    BatchCreate,
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
    pending_release_ids: frozenset[int]
    slots_by_printer: dict[int, tuple[PrinterMaterialSlot, ...]]
    tools_by_printer: dict[int, tuple[PrinterTool, ...]]
    requirements_by_file: dict[int, tuple[ArtifactMaterialRequirement, ...]]
    nozzle_by_file: dict[int, float | None]


def build_routing_snapshot(
    session: Session, file_ids: set[int] | None = None
) -> RoutingSnapshot:
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
    pending_release_ids = {
        int(printer_id)
        for printer_id in session.exec(
            select(PrintJob.printer_id).where(
                PrintJob.printer_id.in_(printer_ids),  # type: ignore[union-attr]
                PrintJob.operator_gate_state == OperatorGateState.PENDING,
            )
        ).all()
        if printer_id is not None
    }
    slots_by_printer: dict[int, list[PrinterMaterialSlot]] = {}
    tools_by_printer: dict[int, list[PrinterTool]] = {}
    if printer_ids:
        for row in session.exec(
            select(PrinterMaterialSlot).where(
                PrinterMaterialSlot.printer_id.in_(printer_ids)  # type: ignore[union-attr]
            )
        ).all():
            slots_by_printer.setdefault(row.printer_id, []).append(row)
        for row in session.exec(
            select(PrinterTool).where(
                PrinterTool.printer_id.in_(printer_ids)  # type: ignore[union-attr]
            )
        ).all():
            tools_by_printer.setdefault(row.printer_id, []).append(row)
    requirements_by_file: dict[int, list[ArtifactMaterialRequirement]] = {}
    nozzle_by_file: dict[int, float | None] = {}
    if file_ids:
        for row in session.exec(
            select(ArtifactMaterialRequirement).where(
                ArtifactMaterialRequirement.file_id.in_(file_ids)  # type: ignore[union-attr]
            )
        ).all():
            requirements_by_file.setdefault(row.file_id, []).append(row)
        for file_id, nozzle in session.exec(
            select(Metadata.file_id, Metadata.nozzle_diameter_mm).where(
                Metadata.file_id.in_(file_ids)  # type: ignore[union-attr]
            )
        ).all():
            nozzle_by_file[int(file_id)] = nozzle
    return RoutingSnapshot(
        printers=printers,
        active_maintenance_ids=frozenset(maintenance_ids),
        active_counts=counts,
        pending_release_ids=frozenset(pending_release_ids),
        slots_by_printer={key: tuple(value) for key, value in slots_by_printer.items()},
        tools_by_printer={key: tuple(value) for key, value in tools_by_printer.items()},
        requirements_by_file={
            key: tuple(value) for key, value in requirements_by_file.items()
        },
        nozzle_by_file=nozzle_by_file,
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
        and (
            snapshot is None or int(printer.id or 0) not in snapshot.pending_release_ids
        )
    )


def _compatibility_rank(
    printer: Printer,
    file_id: int | None,
    snapshot: RoutingSnapshot | None,
) -> int:
    """Return 0 compatible, 1 unknown, 2 known mismatch."""

    if file_id is None or snapshot is None or printer.id is None:
        return 1
    requirements = snapshot.requirements_by_file.get(file_id, ())
    required = {
        row.material_type.strip().casefold()
        for row in requirements
        if row.material_type and row.material_type.strip()
    }
    nozzle = snapshot.nozzle_by_file.get(file_id)
    if not required and nozzle is None:
        return 1
    rows = snapshot.slots_by_printer.get(int(printer.id), ())
    provider_keys = {
        row.slot_key
        for row in rows
        if row.source != MaterialSource.MANUAL
        and printer.provider_material_sync_enabled
        and row.observed_at is not None
        and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
    }
    effective = [
        row
        for row in rows
        if (row.source == MaterialSource.MANUAL and row.slot_key not in provider_keys)
        or (
            row.source != MaterialSource.MANUAL
            and printer.provider_material_sync_enabled
            and row.observed_at is not None
            and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
        )
    ]
    explicit = [row for row in effective if row.state != MaterialSlotState.UNKNOWN]
    has_unknown = any(row.state == MaterialSlotState.UNKNOWN for row in effective)
    has_incomplete_loaded = any(
        row.state == MaterialSlotState.LOADED and not row.material_type
        for row in effective
    )
    loaded = {
        row.material_type.strip().casefold()
        for row in effective
        if row.state == MaterialSlotState.LOADED
        and row.material_type
        and row.material_type.strip()
    }
    if (
        required
        and explicit
        and not has_unknown
        and not has_incomplete_loaded
        and not required.issubset(loaded)
    ):
        return 2
    if required and (not explicit or not required.issubset(loaded)):
        return 1
    known_requirements = [row for row in requirements if row.material_type]
    if len(known_requirements) > 1:
        mapped_tools = {
            row.tool_key
            for row in effective
            if row.state == MaterialSlotState.LOADED and row.tool_key
        }
        if not {f"tool{row.tool_index}" for row in known_requirements}.issubset(
            mapped_tools
        ):
            return 1
    if nozzle is not None:
        tool_rows = snapshot.tools_by_printer.get(int(printer.id), ())
        provider_tool_keys = {
            row.tool_key
            for row in tool_rows
            if row.source != MaterialSource.MANUAL
            and printer.provider_material_sync_enabled
            and row.observed_at is not None
            and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
        }
        effective_tools = [
            row
            for row in tool_rows
            if (
                row.source == MaterialSource.MANUAL
                and row.tool_key not in provider_tool_keys
            )
            or (
                row.source != MaterialSource.MANUAL
                and printer.provider_material_sync_enabled
                and row.observed_at is not None
                and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
            )
        ]
        nozzles = [
            row.nozzle_diameter_mm
            for row in effective_tools
            if row.nozzle_diameter_mm is not None
        ]
        if not nozzles:
            return 1
        if not any(abs(value - nozzle) <= 0.01 for value in nozzles):
            return 2
    return 0


def _unambiguous_tracking_spool(
    printer: Printer,
    file_id: int,
    snapshot: RoutingSnapshot,
) -> tuple[int | None, str | None, int | None]:
    """Resolve usage tracking from one matching effective loaded slot."""

    if printer.id is None:
        return None, None, None
    required = {
        row.material_type.strip().casefold()
        for row in snapshot.requirements_by_file.get(file_id, ())
        if row.material_type and row.material_type.strip()
    }
    rows = snapshot.slots_by_printer.get(int(printer.id), ())
    provider_keys = {
        row.slot_key
        for row in rows
        if row.source != MaterialSource.MANUAL
        and printer.provider_material_sync_enabled
        and row.observed_at is not None
        and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
    }
    candidates = []
    for row in rows:
        provider_fresh = (
            row.source != MaterialSource.MANUAL
            and printer.provider_material_sync_enabled
            and row.observed_at is not None
            and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
        )
        if row.source != MaterialSource.MANUAL and not provider_fresh:
            continue
        if row.source == MaterialSource.MANUAL and row.slot_key in provider_keys:
            continue
        if row.state != MaterialSlotState.LOADED or row.spool_id is None:
            continue
        material = row.material_type.strip().casefold() if row.material_type else None
        if required and material not in required:
            continue
        candidates.append(row)
    unique = {row.spool_id: row for row in candidates}
    if len(unique) != 1:
        return None, None, None
    row = next(iter(unique.values()))
    return row.spool_id, row.spool_name, row.spool_filament_id


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
    file_id: int | None = None,
    target_group: str | None = None,
    compatibility_policy: CompatibilityPolicy = CompatibilityPolicy.SAFE,
) -> tuple[Printer | None, str | None]:
    printers = list(
        snapshot.printers
        if snapshot is not None
        else session.exec(
            select(Printer).where(live(Printer)).order_by(Printer.id)
        ).all()
    )
    if target_group is not None:
        printers = [row for row in printers if row.group == target_group]
    if strategy == RoutingStrategy.MANUAL:
        printer = next(
            (row for row in printers if row.id == requested_printer_id), None
        )
        if printer is None:
            raise FleetError("printer_not_found")
        rank = _compatibility_rank(printer, file_id, snapshot)
        if rank == 2 and compatibility_policy == CompatibilityPolicy.SAFE:
            return printer, "material_mismatch_confirmation_required"
        return (
            printer,
            None if _eligible(session, printer, snapshot) else "printer_unavailable",
        )
    if strategy == RoutingStrategy.DEFAULT:
        printer = next((row for row in printers if row.is_default), None)
        if printer is None:
            return None, "default_printer_missing"
        if (
            _compatibility_rank(printer, file_id, snapshot) == 2
            and compatibility_policy == CompatibilityPolicy.SAFE
        ):
            return None, "no_material_compatible_printer"
        return (
            printer,
            None
            if _eligible(session, printer, snapshot)
            else "default_printer_unavailable",
        )

    eligible = [row for row in printers if _eligible(session, row, snapshot)]
    if not eligible:
        return None, "no_eligible_printer"
    if compatibility_policy == CompatibilityPolicy.SAFE:
        eligible = [
            row for row in eligible if _compatibility_rank(row, file_id, snapshot) < 2
        ]
    if not eligible:
        return None, "no_material_compatible_printer"
    counts = snapshot.active_counts if snapshot is not None else _active_counts(session)
    oldest = datetime.min
    eligible.sort(
        key=lambda row: (
            _compatibility_rank(row, file_id, snapshot),
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

    snapshot = build_routing_snapshot(session, {int(artifact.id)})
    printer, blocked_reason = choose_printer(
        session,
        payload.strategy,
        payload.printer_id,
        snapshot=snapshot,
        file_id=int(artifact.id),
        target_group=payload.target_group,
        compatibility_policy=payload.compatibility_policy,
    )
    if blocked_reason == "material_mismatch_confirmation_required":
        raise FleetError(blocked_reason)
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
        priority=payload.priority,
        target_group=payload.target_group,
        compatibility_policy=payload.compatibility_policy,
        material_override_by=(
            current_user.id
            if payload.compatibility_policy == CompatibilityPolicy.ALLOW_MISMATCH
            else None
        ),
        material_override_at=(
            utcnow()
            if payload.compatibility_policy == CompatibilityPolicy.ALLOW_MISMATCH
            else None
        ),
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


def create_batch(
    session: Session,
    payload: BatchCreate,
    current_user: User,
) -> tuple[PrintBatch, list[PrintJob]]:
    if payload.quantity > settings.fleet_batch_max_quantity:
        raise FleetError("batch_quantity_exceeds_limit")
    artifact = session.get(File, payload.file_id)
    if artifact is None or artifact.deleted_at is not None:
        raise FleetError("file_not_found")
    if artifact.file_type != FileType.GCODE:
        raise FleetError("file_not_gcode")
    if Path(artifact.original_filename).suffix.lower() == ".bgcode":
        raise FleetError("binary_gcode_not_printable")

    now = utcnow()
    batch = PrintBatch(
        file_id=int(artifact.id),
        model_id=artifact.model_id,
        quantity=payload.quantity,
        routing_strategy=payload.strategy,
        priority=payload.priority,
        target_group=payload.target_group,
        compatibility_policy=payload.compatibility_policy,
        requested_by=current_user.id,
        created_by=current_user.id,
        updated_by=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(batch)
    session.flush()
    snapshot = build_routing_snapshot(session, {int(artifact.id)})
    queued = session.exec(
        select(PrintJob).where(PrintJob.state == PrintJobState.QUEUED)
    ).all()
    position = max((row.queue_position for row in queued), default=0)
    jobs: list[PrintJob] = []
    for copy_index in range(1, payload.quantity + 1):
        printer, blocked = choose_printer(
            session,
            payload.strategy,
            payload.printer_id,
            snapshot=snapshot,
            file_id=int(artifact.id),
            target_group=payload.target_group,
            compatibility_policy=payload.compatibility_policy,
        )
        if blocked == "material_mismatch_confirmation_required":
            session.rollback()
            raise FleetError(blocked)
        tracking = (
            _unambiguous_tracking_spool(printer, int(artifact.id), snapshot)
            if printer is not None and payload.strategy != RoutingStrategy.MANUAL
            else (payload.spool_id, payload.spool_name, payload.spool_filament_id)
        )
        position += 1
        job = PrintJob(
            printer_id=printer.id if printer else None,
            printer_name=printer.name if printer else None,
            file_id=int(artifact.id),
            model_id=artifact.model_id,
            batch_id=batch.id,
            copy_index=copy_index,
            remote_filename=build_traceable_remote_filename(artifact),
            state=PrintJobState.QUEUED,
            routing_strategy=payload.strategy,
            queue_position=position,
            priority=payload.priority,
            target_group=payload.target_group,
            compatibility_policy=payload.compatibility_policy,
            blocked_reason=blocked,
            spool_id=tracking[0],
            spool_name=tracking[1],
            spool_filament_id=tracking[2],
            requested_by=current_user.id,
            material_override_by=(
                current_user.id
                if payload.compatibility_policy == CompatibilityPolicy.ALLOW_MISMATCH
                else None
            ),
            material_override_at=(
                now
                if payload.compatibility_policy == CompatibilityPolicy.ALLOW_MISMATCH
                else None
            ),
            created_by=current_user.id,
            updated_by=current_user.id,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        jobs.append(job)
        if printer is not None and printer.id is not None:
            printer_id = int(printer.id)
            snapshot.active_counts[printer_id] = (
                snapshot.active_counts.get(printer_id, 0) + 1
            )
    session.commit()
    session.refresh(batch)
    for job in jobs:
        session.refresh(job)
    return batch, jobs


def operator_decision(
    session: Session,
    job_id: int,
    action: str,
    current_user: User,
) -> PrintJob:
    job = session.get(PrintJob, job_id)
    if job is None or job.deleted_at is not None:
        raise FleetError("queue_job_not_found")
    if job.operator_gate_state != OperatorGateState.PENDING:
        raise FleetError("operator_decision_not_pending")
    if job.printer_id is None:
        raise FleetError("printer_not_found")
    printer = _printer_or_error(session, job.printer_id)
    now = utcnow()
    if action == "release":
        job.operator_gate_state = OperatorGateState.RELEASED
    elif action == "hold":
        job.operator_gate_state = OperatorGateState.HELD
        printer.drain_mode = True
        printer.drain_reason = f"Operator hold after job {job.id}"
        printer.drain_updated_at = now
        printer.updated_by = current_user.id
        printer.updated_at = now
        session.add(printer)
    else:
        raise FleetError("operator_decision_invalid")
    job.operator_decided_by = current_user.id
    job.operator_decided_at = now
    job.updated_by = current_user.id
    job.updated_at = now
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
                case(
                    (PrintJob.priority == JobPriority.RUSH, 0),
                    (PrintJob.priority == JobPriority.NORMAL, 1),
                    else_=2,
                ),
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
        printer, blocked = choose_printer(
            session,
            strategy,
            payload.printer_id,
            snapshot=build_routing_snapshot(session, {job.file_id}),
            file_id=job.file_id,
            target_group=(
                payload.target_group
                if "target_group" in payload.model_fields_set
                else job.target_group
            ),
            compatibility_policy=payload.compatibility_policy
            or job.compatibility_policy,
        )
        if blocked == "material_mismatch_confirmation_required":
            raise FleetError(blocked)
        job.routing_strategy = strategy
        job.printer_id = printer.id if printer else None
        job.printer_name = printer.name if printer else None
        job.blocked_reason = blocked
    if payload.priority is not None and payload.priority != job.priority:
        job.priority = payload.priority
        lane = session.exec(
            select(PrintJob).where(
                PrintJob.state == PrintJobState.QUEUED,
                PrintJob.priority == payload.priority,
            )
        ).all()
        job.queue_position = max((row.queue_position for row in lane), default=0) + 1
    if "target_group" in payload.model_fields_set:
        job.target_group = payload.target_group
    if payload.compatibility_policy is not None:
        job.compatibility_policy = payload.compatibility_policy
        if payload.compatibility_policy == CompatibilityPolicy.ALLOW_MISMATCH:
            job.material_override_by = current_user.id
            job.material_override_at = utcnow()
    if payload.queue_position is not None:
        queued = list(
            session.exec(
                select(PrintJob)
                .where(
                    PrintJob.state == PrintJobState.QUEUED,
                    PrintJob.priority == job.priority,
                )
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
        session,
        job.routing_strategy,
        requested_printer_id,
        snapshot=build_routing_snapshot(session, {job.file_id}),
        file_id=job.file_id,
        target_group=job.target_group,
        compatibility_policy=job.compatibility_policy,
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
) -> dict[str, object]:
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
    ids = {int(row.id) for row in printers if row.id is not None}
    slots = (
        list(
            session.exec(
                select(PrinterMaterialSlot).where(
                    PrinterMaterialSlot.printer_id.in_(ids)  # type: ignore[union-attr]
                )
            ).all()
        )
        if ids
        else []
    )
    tools = (
        list(
            session.exec(
                select(PrinterTool).where(
                    PrinterTool.printer_id.in_(ids)  # type: ignore[union-attr]
                )
            ).all()
        )
        if ids
        else []
    )
    priority_rank = {JobPriority.RUSH: 0, JobPriority.NORMAL: 1, JobPriority.LOW: 2}
    board: list[dict[str, object]] = []
    for printer in printers:
        printer_id = int(printer.id or 0)
        printer_jobs = [row for row in jobs if row.printer_id == printer_id]
        current = next(
            (row for row in printer_jobs if row.state in active_states), None
        )
        queued = sorted(
            (row for row in printer_jobs if row.state == PrintJobState.QUEUED),
            key=lambda row: (
                priority_rank.get(row.priority, 1),
                row.queue_position,
                row.created_at,
                row.id or 0,
            ),
        )
        next_job = queued[0] if queued else None
        printer_slots = [row for row in slots if row.printer_id == printer_id]
        provider_keys = {
            row.slot_key
            for row in printer_slots
            if row.source != MaterialSource.MANUAL
            and printer.provider_material_sync_enabled
            and row.observed_at is not None
            and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
        }
        effective_slots = [
            row
            for row in printer_slots
            if (
                row.source == MaterialSource.MANUAL
                and row.slot_key not in provider_keys
            )
            or (
                row.source != MaterialSource.MANUAL
                and printer.provider_material_sync_enabled
                and row.observed_at is not None
                and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
            )
        ]
        printer_tools = [row for row in tools if row.printer_id == printer_id]
        provider_tool_keys = {
            row.tool_key
            for row in printer_tools
            if row.source != MaterialSource.MANUAL
            and printer.provider_material_sync_enabled
            and row.observed_at is not None
            and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
        }
        effective_tools = [
            row
            for row in printer_tools
            if (
                row.source == MaterialSource.MANUAL
                and row.tool_key not in provider_tool_keys
            )
            or (
                row.source != MaterialSource.MANUAL
                and printer.provider_material_sync_enabled
                and row.observed_at is not None
                and printer.status not in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
            )
        ]
        known_nozzle = next(
            (
                row.nozzle_diameter_mm
                for row in effective_tools
                if row.nozzle_diameter_mm is not None
            ),
            None,
        )
        board.append(
            {
                "printer_id": printer_id,
                "name": printer.name,
                "status": printer.status.value,
                "progress": current.progress if current else None,
                "group": printer.group,
                "loaded_slots": [
                    " · ".join(
                        value
                        for value in (row.label, row.material_type, row.color_hex)
                        if value
                    )
                    for row in effective_slots
                    if row.state == MaterialSlotState.LOADED
                ],
                "nozzle_diameter_mm": known_nozzle,
                "current_job_id": current.id if current else None,
                "current_job_name": current.remote_filename if current else None,
                "current_priority": current.priority if current else None,
                "next_job_id": next_job.id if next_job else None,
                "next_job_name": next_job.remote_filename if next_job else None,
                "next_priority": next_job.priority if next_job else None,
                "drain_mode": printer.drain_mode,
                "maintenance": printer_id in maintenance_printers,
                "pending_operator_release": any(
                    row.operator_gate_state == OperatorGateState.PENDING
                    for row in printer_jobs
                ),
            }
        )
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
        "printers": board,
    }
