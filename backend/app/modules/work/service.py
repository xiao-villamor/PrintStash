"""Operations on background work that routes and domain modules call.

``request`` records a queued Job in the caller's transaction and nudges after
the caller commits; ``cancel`` withdraws intent from the subject before
stopping the engine, so the reconciler cannot resurrect it; ``retry`` returns a
failed, cancelled or retryable partially completed Job's subject to pending
and queues the same Job again.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import and_, delete, false, func, or_
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    ACTIVE_JOB_STATES,
    ArtifactDerivative,
    DerivativeGroupRegeneration,
    DerivativeKind,
    DerivativeState,
    File,
    Job,
    JobKind,
    JobState,
    LaneName,
    User,
    WorkLaneOverride,
    WorkPriority,
)
from app.db.scopes import live
from app.db.session import get_session_factory
from app.db.transactions import begin_write
from app.schemas.jobs import (
    DefinitionRead,
    ExecutorRead,
    JobStatus,
    LaneRead,
    RegenerateMode,
    WorkOverview,
)

from . import catalog as catalog_module
from . import executors
from .contracts import JobOutcome
from .jobs import TERMINAL_STATES, ActiveJobExists, jobs, status_of
from .submission import execution_id, nudge

logger = get_logger(__name__)


def request(
    session: Session,
    *,
    definition: JobKind,
    subject_key: str,
    owner_user_id: int | None,
    priority: WorkPriority = WorkPriority.INTERACTIVE,
    job_id: str | None = None,
    status: dict | None = None,
) -> str:
    """Record a queued Job inside ``session``'s transaction.

    The caller commits it together with the intent it describes, then calls
    ``nudge(definition)``. Committing first is what makes the nudge safe to
    lose: the Job row is already the pending marker the reconciler finds.
    """
    return jobs.create(
        definition=definition,
        subject_key=subject_key,
        owner_user_id=owner_user_id,
        priority=priority,
        session=session,
        job_id=job_id,
        status=status,
    )


def visible_to(status: JobStatus, user: User) -> bool:
    return user.is_superuser or (
        status.owner_user_id is not None and status.owner_user_id == user.id
    )


def cancel(job_id: str, *, actor: User) -> JobStatus:
    status = jobs.get(job_id)
    if status is None or not visible_to(status, actor):
        raise OperationError("job_not_found", kind=ErrorKind.NOT_FOUND)
    if status.terminal:
        raise OperationError("job_not_active", kind=ErrorKind.CONFLICT)
    definition = catalog_module.get_catalog().definition(status.kind)
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is None:
            raise OperationError("job_not_found", kind=ErrorKind.NOT_FOUND)
        attempts, epoch, state = row.attempts, row.execution_epoch, row.state
    settled = jobs.cancel_attempt(
        job_id,
        attempts,
        execution_epoch=epoch,
        expected_state=state,
        withdraw=definition.cancel,
    )
    if settled is None:
        raise OperationError("job_not_active", kind=ErrorKind.CONFLICT)
    if attempts:
        try:
            catalog_module.get_engine().cancel(execution_id(job_id, attempts, epoch))
        except Exception:  # noqa: BLE001 - the Job is settled; the engine catches up
            logger.warning("engine cancel failed", extra={"job_id": job_id})
    result = jobs.get(job_id)
    assert result is not None
    return result


def supersede_restored() -> int:
    """Settle restored Jobs whose work a restore replaced.

    The restored database still shows the Jobs that were in flight when it
    was captured. Most are owed again and the reconciler reruns them; the
    ones whose definition does not survive a restore are cancelled here, so
    the snapshot's own backup request is not mistaken for one in progress.
    """
    catalog = catalog_module.get_catalog()
    superseded = [
        name
        for name, definition in catalog.definitions.items()
        if not definition.survives_restore
    ]
    if not superseded:
        return 0
    with get_session_factory().scoped_session() as session:
        rows = cast(
            list[tuple[str, JobKind, str]],
            list(
                session.exec(
                    select(Job.id, Job.kind, Job.subject_key).where(
                        col(Job.kind).in_(superseded),
                        col(Job.state).in_(ACTIVE_JOB_STATES),
                    )
                ).all()
            ),
        )
        # Withdraw each one's intent the way a cancel does, so what it had
        # started (a backup run, a destination retry) settles too.
        for _job_id, kind, subject in rows:
            catalog.definition(kind).cancel(session, subject)
        session.commit()
    ids = [job_id for job_id, _kind, _subject in rows]
    for job_id in ids:
        jobs.finish(
            job_id,
            JobOutcome.CANCELLED,
            error="superseded_by_restore",
            retryable=False,
        )
    return len(ids)


def cancel_queued(definition: JobKind, *, actor: User) -> int:
    """Withdraw every queued (not yet running) Job of one definition."""
    if not actor.is_superuser:
        raise OperationError("admin_required", kind=ErrorKind.FORBIDDEN)
    catalog_module.get_catalog().definition(definition)
    with get_session_factory().scoped_session() as session:
        ids = list(
            session.exec(
                select(Job.id).where(
                    Job.kind == definition, Job.state == JobState.QUEUED
                )
            ).all()
        )
    for job_id in ids:
        cancel(job_id, actor=actor)
    return len(ids)


def _retry_eligible(status: JobStatus) -> bool:
    return status.state in {JobState.FAILED, JobState.CANCELLED} or (
        status.state is JobState.COMPLETED and status.retryable and status.failed > 0
    )


def retry(job_id: str, *, actor: User) -> JobStatus:
    status = jobs.get(job_id)
    if status is None or not visible_to(status, actor):
        raise OperationError("job_not_found", kind=ErrorKind.NOT_FOUND)
    if not _retry_eligible(status):
        raise OperationError("job_not_retryable", kind=ErrorKind.CONFLICT)
    definition = catalog_module.get_catalog().definition(status.kind)
    with get_session_factory().scoped_session() as session:
        begin_write(session, immediate=True)
        refused = definition.admission(session)
        if refused is not None:
            raise OperationError(refused, kind=ErrorKind.CONFLICT)
        row = session.exec(
            select(Job).where(Job.id == job_id).with_for_update()
        ).first()
        if row is None:
            raise OperationError("job_not_found", kind=ErrorKind.NOT_FOUND)
        if not _retry_eligible(status_of(row)):
            raise OperationError("job_not_retryable", kind=ErrorKind.CONFLICT)
        other = session.exec(
            select(Job.id).where(
                Job.kind == row.kind,
                Job.subject_key == row.subject_key,
                col(Job.state).in_(ACTIVE_JOB_STATES),
            )
        ).first()
        if other is not None:
            raise OperationError("job_subject_busy", kind=ErrorKind.CONFLICT)
        if not definition.retry(session, row.subject_key):
            session.rollback()
            raise OperationError("job_subject_gone", kind=ErrorKind.GONE)
        now = utcnow()
        payload = json.loads(row.status_json)
        payload = {
            key: value
            for key, value in payload.items()
            if key in {"result", "current_item", "total"}
        }
        row.state = JobState.QUEUED
        row.execution_epoch = uuid.uuid4().hex
        row.resubmits = 0
        row.finished_at = None
        row.status_json = json.dumps(payload, separators=(",", ":"))
        row.updated_at = now
        session.add(row)
        session.commit()
    nudge(definition.name)
    result = jobs.get(job_id)
    assert result is not None
    return result


def set_lane_concurrency(
    lane: LaneName, concurrency: int | None, *, actor: User
) -> None:
    """Override one lane's concurrency; ``None`` returns it to its default."""
    catalog = catalog_module.get_catalog()
    with get_session_factory().scoped_session() as session:
        if concurrency is None:
            session.execute(
                delete(WorkLaneOverride).where(col(WorkLaneOverride.lane) == lane)
            )
        else:
            row = session.get(WorkLaneOverride, lane) or WorkLaneOverride(
                lane=lane, concurrency=concurrency
            )
            row.concurrency = concurrency
            row.updated_by = actor.id
            row.updated_at = utcnow()
            session.add(row)
        session.commit()
        catalog.apply_overrides(session)
    catalog_module.get_engine().set_lane_concurrency(
        lane, catalog.lanes[lane].concurrency
    )


def regenerate_derivatives(
    kind: DerivativeKind, *, mode: RegenerateMode, actor: User
) -> None:
    """``missing`` just nudges; ``all`` marks every output of ``kind`` stale."""
    from app.modules.derivatives import policy
    from app.modules.derivatives.kinds import definitions_for_kind
    from app.modules.work.contracts import SkipReason

    with get_session_factory().scoped_session() as session:
        policy.lock(session)
        controls = policy.resolve(session)
        enabled = [d for d in definitions_for_kind(kind) if controls[d].enabled]
        if not enabled:
            raise OperationError(
                SkipReason.DERIVATIVE_GROUP_DISABLED, kind=ErrorKind.CONFLICT
            )
        if mode is RegenerateMode.ALL:
            for definition in enabled:
                row = session.get(DerivativeGroupRegeneration, (definition, kind))
                if row is None:
                    row = DerivativeGroupRegeneration(definition=definition, kind=kind)
                row.requested_at = utcnow()
                row.requested_by = actor.id
                session.add(row)
        session.commit()
    for definition in enabled:
        nudge(definition)


def overview(*, now: datetime | None = None) -> WorkOverview:
    from app.modules.derivatives import kinds, policy

    now = now or utcnow()
    catalog = catalog_module.get_catalog()
    engine = catalog_module.get_engine()
    defaults = catalog_module.default_lanes()
    lanes: list[LaneRead] = []
    for name, lane in sorted(catalog.lanes.items()):
        depth = engine.lane_depth(name)
        lanes.append(
            LaneRead(
                name=name,
                concurrency=lane.concurrency,
                default_concurrency=defaults[name].concurrency,
                overridden=lane.concurrency != defaults[name].concurrency,
                scope=lane.scope,
                partitioned=lane.partitioned,
                queued=depth.queued,
                running=depth.running,
            )
        )
    counts = jobs.counts_by_definition()
    definitions: list[DefinitionRead] = []
    with get_session_factory().scoped_session() as session:
        controls = policy.resolve(session)
        disabled = [d for d, control in controls.items() if not control.enabled]
        for name, definition in sorted(catalog.definitions.items()):
            per_state = counts.get(name, {})
            last = session.exec(
                select(Job.finished_at)
                .where(Job.kind == name, col(Job.finished_at).is_not(None))
                .order_by(col(Job.finished_at).desc())
                .limit(1)
            ).first()
            next_due = (
                definition.source.next_due(session, now=now)
                if definition.source is not None
                else None
            )
            definitions.append(
                DefinitionRead(
                    name=name,
                    enabled=controls[name].enabled if name in controls else True,
                    default_enabled=controls[name].default_enabled
                    if name in controls
                    else True,
                    overridden=controls[name].overridden if name in controls else False,
                    label=definition.label,
                    lane=definition.lane,
                    queued=per_state.get(JobState.QUEUED, 0),
                    running=per_state.get(JobState.RUNNING, 0),
                    interrupted=per_state.get(JobState.INTERRUPTED, 0),
                    failed=0 if name in disabled else per_state.get(JobState.FAILED, 0),
                    completed=per_state.get(JobState.COMPLETED, 0),
                    derivative_kinds=list(kinds.group(name).kinds)
                    if kinds.is_derivative(name)
                    else [],
                    next_due_at=next_due,
                    last_finished_at=last,
                )
            )
        failed_derivatives = int(
            session.exec(
                select(func.count(col(ArtifactDerivative.id)))
                .join(File)
                .where(
                    or_(
                        false(),
                        *(
                            and_(g.applies(), col(ArtifactDerivative.kind).in_(g.kinds))
                            for g in kinds.GROUPS
                            if controls[g.definition].enabled
                        ),
                    ),
                    live(File),
                    ArtifactDerivative.state == DerivativeState.FAILED,
                )
            ).one()
        )
    executors_read = [
        ExecutorRead(
            executor_id=row.executor_id,
            role=row.role,
            hostname=row.hostname,
            app_version=row.app_version,
            lanes=executors.lanes_of(row),
            started_at=row.started_at,
            heartbeat_at=row.heartbeat_at,
            stale=executors.is_stale(row, now=now),
        )
        for row in executors.all_executors()
    ]
    return WorkOverview(
        lanes=lanes,
        definitions=definitions,
        executors=executors_read,
        failed_jobs=jobs.failed(limit=50, excluded_kinds=disabled),
        failed_derivatives=failed_derivatives,
    )


__all__ = [
    "ActiveJobExists",
    "TERMINAL_STATES",
    "cancel",
    "cancel_queued",
    "nudge",
    "overview",
    "regenerate_derivatives",
    "request",
    "retry",
    "set_lane_concurrency",
    "status_of",
    "visible_to",
]
