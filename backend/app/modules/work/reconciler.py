"""The reconciler: the load-bearing path that makes background work converge.

Every guarantee that work eventually happens lives here. A pass over one
definition does two bounded things, in order, over one captured ``now``:

1. **Repair.** For each non-terminal Job, ``decide`` compares the Job with the
   engine's evidence about its current attempt and returns one verdict:
   submit the first attempt, leave it alone, interrupt it (the execution was
   lost, superseded by an upgrade, or stranded on a dead executor) and
   resubmit it, fail it (resubmits exhausted, or the engine failed it), or
   complete it (the engine finished but the terminal write was lost).
2. **Discover.** A definition with a source asks it for pending subjects,
   creates one Job per subject that has no active Job, and submits them, up to
   the smaller of the batch size and the lane's headroom.

A pass is single-flight per definition through the cursor claim, and it re-runs
itself while nudges arrive during it (see ``ReconcileCursor``). A batch that
came back full with lane room to spare continues immediately; a full lane stops,
and each completion nudges its own definition again, so a backfill flows
without ever filling the engine with the whole library.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import and_, case, func, or_, update
from sqlmodel import Session, col, select
from sqlmodel.sql.expression import SelectOfScalar

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.affected import affected
from app.db.models import (
    ACTIVE_JOB_STATES,
    Job,
    JobKind,
    JobState,
    ReconcileCursor,
    WorkFence,
)
from app.db.session import get_session_factory
from app.db.transactions import begin_write

from . import catalog as catalog_module
from . import executors, fences
from .contracts import (
    DiscoveryBudget,
    EngineEvidence,
    EngineStatus,
    ExecutionKind,
    JobDefinition,
    JobOutcome,
    LaneOrder,
    PrioritizedWorkSource,
    SkipReason,
    StepRunner,
    SubmitOutcome,
    WorkItem,
    WorkPriority,
)
from .jobs import TERMINAL_STATES, ActiveJobExists, active_job_predicate, jobs
from .submission import execution_id, nudge, submit

logger = get_logger(__name__)
_TERMINAL_RECOVERY_BATCH = 500

_MAX_LOOPS = 20


class Verdict(StrEnum):
    NONE = "none"
    SUBMIT = "submit"
    INTERRUPT = "interrupt"
    FAIL = "fail"
    COMPLETE = "complete"


class Reason(StrEnum):
    """Why ``decide`` reached its verdict; it lands on an interrupted or failed Job."""

    INTERRUPTED_REPEATEDLY = "interrupted_repeatedly"
    RESUBMIT = "resubmit"
    FIRST_ATTEMPT = "first_attempt"
    RETRY_PENDING = "retry_pending"
    SUBMISSION_IN_FLIGHT = "submission_in_flight"
    EXECUTION_LOST = "execution_lost"
    TERMINAL_WRITE_LOST = "terminal_write_lost"
    ENGINE_FAILED = "engine_failed"
    EXECUTION_CANCELLED = "execution_cancelled"
    APPLICATION_UPGRADED = "application_upgraded"
    EXECUTOR_LOST = "executor_lost"
    IN_PROGRESS = "in_progress"


class PassNote(StrEnum):
    """What a pass observed besides a verdict, counted for its log line."""

    LANE_FULL = "lane_full"
    COOLING_DOWN = "cooling_down"
    ALREADY_ACTIVE = "already_active"
    CLAIMED_ELSEWHERE = "claimed_elsewhere"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: Reason
    cancel_engine: bool = False


def decide(
    job: Job,
    evidence: EngineEvidence | None,
    *,
    now: datetime,
    app_version: str,
    stale_executors: set[str],
    max_resubmits: int,
    grace: timedelta,
) -> Decision:
    """The pure decision table for one non-terminal Job.

    ``evidence`` is what the engine recorded about the Job's current attempt,
    ``None`` when it has no record of it. Every branch names why.
    """
    if job.state is JobState.INTERRUPTED:
        if job.resubmits > max_resubmits:
            return Decision(Verdict.FAIL, Reason.INTERRUPTED_REPEATEDLY)
        return Decision(Verdict.SUBMIT, Reason.RESUBMIT)
    if job.attempts == 0:
        return Decision(Verdict.SUBMIT, Reason.FIRST_ATTEMPT)
    if job.submitted_epoch != job.execution_epoch:
        return Decision(Verdict.SUBMIT, Reason.RETRY_PENDING)
    if evidence is None:
        if now - ensure_utc(job.updated_at) < grace:
            return Decision(Verdict.NONE, Reason.SUBMISSION_IN_FLIGHT)
        return Decision(Verdict.INTERRUPT, Reason.EXECUTION_LOST)
    status = evidence.status
    if status is EngineStatus.SUCCEEDED:
        return Decision(Verdict.COMPLETE, Reason.TERMINAL_WRITE_LOST)
    if status is EngineStatus.FAILED:
        return Decision(Verdict.FAIL, Reason.ENGINE_FAILED)
    if status is EngineStatus.CANCELLED:
        return Decision(Verdict.INTERRUPT, Reason.EXECUTION_CANCELLED)
    if evidence.app_version is not None and evidence.app_version != app_version:
        return Decision(
            Verdict.INTERRUPT, Reason.APPLICATION_UPGRADED, cancel_engine=True
        )
    if (
        status is EngineStatus.RUNNING
        and evidence.executor_id is not None
        and evidence.executor_id in stale_executors
    ):
        return Decision(Verdict.INTERRUPT, Reason.EXECUTOR_LOST, cancel_engine=True)
    return Decision(Verdict.NONE, Reason.IN_PROGRESS)


@dataclass
class PassResult:
    submitted: int = 0
    deferred: int = 0
    interrupted: int = 0
    failed: int = 0
    completed: int = 0
    skipped: int = 0
    full: bool = False
    # The earliest moment a subject held back by the resubmit cooldown is due.
    cooling_until: datetime | None = None
    outcomes: dict[str, int] = field(default_factory=dict)

    def count(self, outcome: Reason | PassNote | SkipReason) -> None:
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1


def _interrupt(job: Job, reason: Reason, *, now: datetime) -> bool:
    from app.core.metrics import record_resubmit

    with get_session_factory().scoped_session() as session:
        row = jobs.lock_execution(
            session,
            job.id,
            epoch=job.execution_epoch,
            attempt=job.attempts,
            states=(job.state,),
        )
        if row is None or row.state not in ACTIVE_JOB_STATES:
            return False
        row.state = JobState.INTERRUPTED
        row.resubmits += 1
        row.updated_at = now
        session.add(row)
        session.commit()
    jobs.update(
        job.id,
        expected_attempt=job.attempts,
        expected_epoch=job.execution_epoch,
        error=reason,
        retryable=True,
    )
    record_resubmit(job.kind)
    return True


def _repair(definition: JobDefinition, *, now: datetime, result: PassResult) -> None:
    engine = catalog_module.get_engine()
    with get_session_factory().scoped_session() as session:
        refused = definition.admission(session)
        ordering = (
            [
                case((col(Job.state) != JobState.RUNNING, 0), else_=1),
                col(Job.updated_at),
            ]
            if refused is not None
            else [col(Job.updated_at)]
        )
        rows = list(
            session.exec(
                select(Job)
                .where(Job.kind == definition.name, active_job_predicate())
                .order_by(*ordering)
                .limit(settings.jobs_reconcile_batch)
            ).all()
        )
        for row in rows:
            session.expunge(row)
    if not rows:
        return
    admission = _RecoveryAdmission(definition)
    lane = catalog_module.get_catalog().lanes[definition.lane]
    if lane.queue_order is LaneOrder.FIFO:
        owner = admission.load()
        if owner is not None and all(row.id != owner for row in rows):
            # The ordinary update-order page may hide its creation-order owner.
            # Add at most that one own-definition Job: the bound is batch + 1.
            with get_session_factory().scoped_session() as session:
                recovery = session.exec(
                    select(Job)
                    .where(
                        col(Job.id) == owner,
                        col(Job.kind) == definition.name,
                        active_job_predicate(),
                    )
                    .limit(1)
                ).first()
                if recovery is not None:
                    session.expunge(recovery)
                    rows.append(recovery)
    ids = [
        execution_id(row.id, row.attempts, row.execution_epoch)
        for row in rows
        if row.attempts > 0
    ]
    evidence = engine.evidence(ids) if ids else {}
    stale = executors.stale_ids(now=now)
    grace = timedelta(seconds=settings.jobs_submit_grace_seconds)
    for row in rows:
        seen = (
            evidence.get(execution_id(row.id, row.attempts, row.execution_epoch))
            if row.attempts
            else None
        )
        decision = decide(
            row,
            seen,
            now=now,
            app_version=settings.app_version,
            stale_executors=stale,
            max_resubmits=settings.jobs_max_resubmits,
            grace=grace,
        )
        if refused is not None and decision.verdict is not Verdict.COMPLETE:
            # Only a healthy execution already running may finish. An execution
            # lost to a crash or upgrade is a new processing attempt.
            healthy = (
                row.state is JobState.RUNNING
                and seen is not None
                and seen.status is EngineStatus.RUNNING
                and decision.reason is Reason.IN_PROGRESS
            )
            if healthy:
                # Rotate checked healthy attempts behind older lost attempts.
                # A full batch of long producers must not hide the lost backlog.
                with get_session_factory().scoped_session() as session:
                    session.execute(
                        update(Job)
                        .where(
                            col(Job.id) == row.id,
                            col(Job.state) == JobState.RUNNING,
                            col(Job.execution_epoch) == row.execution_epoch,
                            col(Job.attempts) == row.attempts,
                        )
                        .values(updated_at=utcnow())
                    )
                    session.commit()
                continue
            if row.attempts and seen is not None:
                try:
                    engine.cancel(
                        execution_id(row.id, row.attempts, row.execution_epoch)
                    )
                except Exception:  # noqa: BLE001 - admission blocks execution; next pass retries cancellation
                    logger.warning("engine cancel failed", extra={"job_id": row.id})
                    result.deferred += 1
                    continue
            settled = jobs.settle_attempt(
                row.id,
                row.attempts,
                JobOutcome.CANCELLED,
                error=refused,
                execution_epoch=row.execution_epoch,
                expected_state=row.state,
                on_failure=definition.on_failure,
            )
            if settled is None:
                result.deferred += 1
                continue
            admission.settled(row.id)
            result.skipped += 1
            result.count(refused)
            result.full = len(rows) >= settings.jobs_reconcile_batch
            continue
        result.count(decision.reason)
        if decision.verdict is Verdict.NONE:
            continue
        if decision.verdict in (
            Verdict.SUBMIT,
            Verdict.INTERRUPT,
        ) and not admission.allows(row):
            # Legacy queued intent stays durable without spending retry attempts
            # or occupying every FIFO execution slot ahead of interactive work.
            result.deferred += 1
            result.count(PassNote.LANE_FULL)
            continue
        if decision.cancel_engine and row.attempts:
            try:
                engine.cancel(execution_id(row.id, row.attempts, row.execution_epoch))
            except Exception:  # noqa: BLE001 - the next pass retries the cancel
                logger.warning("engine cancel failed", extra={"job_id": row.id})
                result.deferred += 1
                continue
        if decision.verdict is Verdict.COMPLETE:
            settled = jobs.settle_attempt(
                row.id,
                row.attempts,
                JobOutcome.COMPLETED,
                execution_epoch=row.execution_epoch,
                expected_state=row.state,
                on_failure=definition.on_failure,
            )
            if settled is None:
                result.deferred += 1
                continue
            admission.settled(row.id)
            result.completed += 1
            continue
        if decision.verdict is Verdict.FAIL:
            settled = jobs.settle_attempt(
                row.id,
                row.attempts,
                JobOutcome.FAILED,
                error=decision.reason,
                execution_epoch=row.execution_epoch,
                expected_state=row.state,
                on_failure=definition.on_failure,
            )
            if settled is None:
                result.deferred += 1
                continue
            admission.settled(row.id)
            result.failed += 1
            continue
        if decision.verdict is Verdict.INTERRUPT:
            if not _interrupt(row, decision.reason, now=now):
                result.deferred += 1
                continue
            result.interrupted += 1
            with get_session_factory().scoped_session() as session:
                fresh = session.get(Job, row.id)
                exhausted = (
                    fresh is not None
                    and fresh.execution_epoch == row.execution_epoch
                    and fresh.attempts == row.attempts
                    and fresh.state is JobState.INTERRUPTED
                    and fresh.resubmits > settings.jobs_max_resubmits
                )
            if exhausted:
                assert fresh is not None
                settled = jobs.settle_attempt(
                    fresh.id,
                    fresh.attempts,
                    JobOutcome.FAILED,
                    error=Reason.INTERRUPTED_REPEATEDLY,
                    execution_epoch=fresh.execution_epoch,
                    expected_state=fresh.state,
                    on_failure=definition.on_failure,
                )
                if settled is not None:
                    admission.settled(fresh.id)
                result.failed += 1
                continue
        _submit(row.id, result)


@dataclass
class _RecoveryAdmission:
    """One lazy owner lookup per batch, including a cached empty result."""

    definition: JobDefinition
    loaded: bool = field(default=False, init=False)
    owner: str | None = field(default=None, init=False)

    def allows(self, row: Job) -> bool:
        lane = catalog_module.get_catalog().lanes[self.definition.lane]
        if (
            row.priority is not WorkPriority.BACKFILL
            or lane.queue_order is not LaneOrder.FIFO
        ):
            return True
        return self.load() == row.id

    def load(self) -> str | None:
        if not self.loaded:
            self.owner = _recovery_owner(self.definition)
            self.loaded = True
        return self.owner

    def settled(self, job_id: str) -> None:
        if self.loaded and self.owner == job_id:
            self.owner = None
            self.loaded = False


def _backfill_owner_select(definition: JobDefinition) -> SelectOfScalar[str]:
    """The same bounded owner selection governs observation and reservation."""
    catalog = catalog_module.get_catalog()
    definitions = [
        item.name
        for item in catalog.definitions.values()
        if item.lane == definition.lane
    ]
    admitted = or_(
        col(Job.backfill_admission_epoch) == col(Job.execution_epoch),
        and_(
            col(Job.attempts) > 0, col(Job.submitted_epoch) == col(Job.execution_epoch)
        ),
    )
    return (
        select(Job.id)
        .where(
            col(Job.kind).in_(definitions),
            col(Job.priority) == WorkPriority.BACKFILL,
            active_job_predicate(),
        )
        .order_by(
            case((col(Job.state) == JobState.RUNNING, 0), (admitted, 1), else_=2),
            col(Job.created_at),
            col(Job.id),
        )
        .limit(1)
    )


def _recovery_owner(definition: JobDefinition) -> str | None:
    with get_session_factory().scoped_session() as session:
        return session.exec(_backfill_owner_select(definition)).first()


def _reserve_backfill_epoch(
    job_id: str, definition: JobDefinition, lease: _DiscoveryLease, result: PassResult
) -> str | None:
    """Commit current-epoch admission before engine I/O, under live lane authority."""
    with get_session_factory().scoped_session() as session:
        begin_write(session, immediate=True)
        if not _renew_discovery_lease(session, lease):
            result.count(PassNote.CLAIMED_ELSEWHERE)
            return None
        row = session.get(Job, job_id)
        if row is None or row.state not in ACTIVE_JOB_STATES:
            return None
        epoch = row.execution_epoch
        owner = _backfill_owner_select(definition).correlate(None).scalar_subquery()
        claimed = affected(
            session,
            update(Job)
            .where(
                col(Job.id) == job_id,
                col(Job.kind) == definition.name,
                col(Job.execution_epoch) == epoch,
                col(Job.priority) == WorkPriority.BACKFILL,
                active_job_predicate(),
                col(Job.id) == owner,
            )
            .values(backfill_admission_epoch=epoch)
            .execution_options(synchronize_session=False),
        )
        if not claimed:
            result.count(PassNote.LANE_FULL)
            return None
        session.commit()
        return epoch


def _submit(
    job_id: str, result: PassResult, *, lease: _DiscoveryLease | None = None
) -> None:
    owned: _DiscoveryLease | None = None
    try:
        with get_session_factory().scoped_session() as session:
            row = session.get(Job, job_id)
            if row is None or row.state not in ACTIVE_JOB_STATES:
                return
            definition = catalog_module.get_catalog().definition(row.kind)
            lane = catalog_module.get_catalog().lanes[definition.lane]
            reserve = (
                row.priority is WorkPriority.BACKFILL
                and lane.queue_order is LaneOrder.FIFO
            )
        if reserve:
            if lease is None:
                candidate = _DiscoveryLease(
                    name=f"discovery:{definition.lane.value}", holder=uuid.uuid4().hex
                )
                fences.acquire(
                    candidate.name,
                    holder=candidate.holder,
                    reason="backfill submission",
                )
                owned = candidate
                lease = candidate
            epoch = _reserve_backfill_epoch(job_id, definition, lease, result)
            if epoch is None:
                result.deferred += 1
                return
            outcome = submit(job_id, reserved_backfill_epoch=epoch)
        else:
            outcome = submit(job_id)
    except fences.FenceHeld:
        result.count(PassNote.CLAIMED_ELSEWHERE)
        result.deferred += 1
        return
    except Exception:  # noqa: BLE001 - committed admission retains intent across an ACK crash
        logger.exception("job submission failed", extra={"job_id": job_id})
        result.deferred += 1
        return
    finally:
        if owned is not None:
            fences.release(owned.name, holder=owned.holder)
    if outcome is None:
        return
    if outcome is SubmitOutcome.DEDUPLICATED:
        result.deferred += 1
    else:
        result.submitted += 1


def _headroom(definition: JobDefinition) -> int:
    catalog = catalog_module.get_catalog()
    lane = catalog.lanes[definition.lane]
    depth = catalog_module.get_engine().lane_depth(lane.name)
    return max(0, lane.headroom - depth.queued)


def _discovery_budget(session: Session, definition: JobDefinition) -> DiscoveryBudget:
    catalog = catalog_module.get_catalog()
    lane = catalog.lanes[definition.lane]
    definitions = [
        item.name
        for item in catalog.definitions.values()
        if item.lane == definition.lane
    ]
    counts = dict(
        session.exec(
            select(Job.priority, func.count(col(Job.id)))
            .where(
                col(Job.kind).in_(definitions),
                active_job_predicate(),
                or_(
                    col(Job.priority) == WorkPriority.BACKFILL,
                    col(Job.state).in_([JobState.QUEUED, JobState.INTERRUPTED]),
                ),
            )
            .group_by(col(Job.priority))
        ).all()
    )
    interactive = max(0, lane.concurrency - counts.get(WorkPriority.INTERACTIVE, 0))
    backfill = max(0, 1 - counts.get(WorkPriority.BACKFILL, 0))
    return DiscoveryBudget(
        total=min(settings.jobs_reconcile_batch, interactive + backfill),
        interactive=interactive,
        backfill=backfill,
    )


def _discover(definition: JobDefinition, *, now: datetime, result: PassResult) -> None:
    source = definition.source
    with get_session_factory().scoped_session() as session:
        if definition.admission(session) is not None:
            return
    if source is None:
        return
    with get_session_factory().scoped_session() as session:
        if isinstance(source, PrioritizedWorkSource):
            budget = _discovery_budget(session, definition)
            limit = budget.total
            if limit <= 0:
                result.count(PassNote.LANE_FULL)
                return
            items: Sequence[WorkItem] = source.pending_prioritized(
                session, now=now, budget=budget
            )
        else:
            limit = min(settings.jobs_reconcile_batch, _headroom(definition))
            if limit <= 0:
                result.count(PassNote.LANE_FULL)
                return
            items = source.pending(session, now=now, limit=limit)
        finished = (
            {}
            if definition.drain
            else _recently_finished(
                session,
                definition.name,
                [item.subject_key for item in items],
                now=now,
            )
        )
    lease: _DiscoveryLease | None = None
    if isinstance(source, PrioritizedWorkSource) and items:
        lease = _DiscoveryLease(
            name=f"discovery:{definition.lane.value}", holder=uuid.uuid4().hex
        )
        try:
            fences.acquire(lease.name, holder=lease.holder, reason="priority discovery")
        except fences.FenceHeld:
            result.count(PassNote.CLAIMED_ELSEWHERE)
            return
    try:
        cooldown = timedelta(seconds=settings.jobs_resubmit_cooldown_seconds)
        created = 0
        for item in items:
            last = finished.get(item.subject_key)
            if last is not None:
                due = last + cooldown
                result.deferred += 1
                result.count(PassNote.COOLING_DOWN)
                if result.cooling_until is None or due < result.cooling_until:
                    result.cooling_until = due
                continue
            created += _create_and_submit(
                definition, item, now=now, result=result, lease=lease
            )
        # A full batch continues straight away only when it made progress. The same
        # subjects would come straight back if it did not: ones held back by the
        # cooldown, or ones whose Jobs are still queued (the source is ahead of the
        # engine, and those Jobs' completions nudge the next pass).
        result.full = (
            len(items) >= limit and created > 0 and result.cooling_until is None
        )
    finally:
        if lease is not None:
            fences.release(lease.name, holder=lease.holder)


def _recently_finished(
    session: Session, definition: JobKind, subject_keys: list[str], *, now: datetime
) -> dict[str, datetime]:
    """Subjects of ``subject_keys`` whose Jobs of ``definition`` already
    finished ``jobs_resubmit_burst`` times within the cooldown window, with
    when the latest one finished."""
    window = settings.jobs_resubmit_cooldown_seconds
    if not subject_keys or window <= 0:
        return {}
    since = now - timedelta(seconds=window)
    rows = session.exec(
        select(Job.subject_key, func.max(Job.finished_at), func.count(col(Job.id)))
        .where(
            col(Job.kind) == definition,
            col(Job.subject_key).in_(subject_keys),
            col(Job.state).in_(TERMINAL_STATES),
            col(Job.finished_at) > since,
            ~col(Job.status_json).contains(SkipReason.DERIVATIVE_GROUP_DISABLED.value),
        )
        .group_by(col(Job.subject_key))
    ).all()
    burst = settings.jobs_resubmit_burst
    return {
        subject: ensure_utc(at)
        for subject, at, count in rows
        if at is not None and count >= burst
    }


@dataclass(frozen=True)
class _DiscoveryLease:
    name: str
    holder: str


def _renew_discovery_lease(session: Session, lease: _DiscoveryLease) -> bool:
    """The successful conditional write locks admission authority until commit."""
    now = utcnow()
    return bool(
        affected(
            session,
            update(WorkFence)
            .where(
                col(WorkFence.name) == lease.name,
                col(WorkFence.holder) == lease.holder,
                col(WorkFence.expires_at) > now,
            )
            .values(
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=settings.fence_ttl_seconds),
            ),
        )
    )


def _create_and_submit(
    definition: JobDefinition,
    item: WorkItem,
    *,
    now: datetime,
    result: PassResult,
    lease: _DiscoveryLease | None = None,
) -> int:
    """Create (and submit) the item's Job; 1 when a Job was created."""
    try:
        with get_session_factory().scoped_session() as session:
            if lease is not None:
                begin_write(session, immediate=True)
                if not _renew_discovery_lease(session, lease):
                    result.count(PassNote.CLAIMED_ELSEWHERE)
                    return 0
                budget = _discovery_budget(session, definition)
                room = (
                    budget.interactive
                    if item.priority is WorkPriority.INTERACTIVE
                    else budget.backfill
                )
                if room == 0:
                    result.count(PassNote.LANE_FULL)
                    return 0
            # Domain-owned hooks decide admission; coordinator knows no settings.
            if definition.admission(session) is not None:
                return 0
            job_id = jobs.create(
                definition=definition.name,
                subject_key=item.subject_key,
                owner_user_id=item.owner_user_id,
                priority=item.priority,
                session=session,
            )
            session.commit()
    except ActiveJobExists:
        result.count(PassNote.ALREADY_ACTIVE)
        return 0
    if item.occurrence_at is not None:
        _record_occurrence(definition.name, item.occurrence_at)
    if item.skip is not None:
        jobs.finish(job_id, JobOutcome.CANCELLED, error=item.skip)
        result.skipped += 1
        result.count(item.skip)
        return 1
    _submit(job_id, result, lease=lease)
    return 1


def _record_occurrence(source: JobKind, occurrence: datetime) -> None:
    with get_session_factory().scoped_session() as session:
        cursor = session.get(ReconcileCursor, source) or ReconcileCursor(source=source)
        if cursor.last_occurrence_at is None or ensure_utc(
            cursor.last_occurrence_at
        ) < ensure_utc(occurrence):
            cursor.last_occurrence_at = occurrence
        session.add(cursor)
        session.commit()


def _claim(source: JobKind, holder: str, *, now: datetime) -> bool:
    ttl = timedelta(seconds=max(settings.fence_ttl_seconds, 60))
    with get_session_factory().scoped_session() as session:
        if session.get(ReconcileCursor, source) is None:
            session.add(ReconcileCursor(source=source))
            session.commit()
        claimed = affected(
            session,
            update(ReconcileCursor)
            .where(
                col(ReconcileCursor.source) == source,
                or_(
                    col(ReconcileCursor.holder).is_(None),
                    col(ReconcileCursor.holder_expires_at) < now,
                ),
            )
            .values(
                holder=holder,
                holder_expires_at=now + ttl,
                pass_queued_at=None,
                pass_priority=None,
                last_pass_started_at=now,
            ),
        )
        if not claimed:
            # This queued execution has reached the owner and lost single-flight.
            # Its marker must stop covering later nudges once it returns. Keep
            # newer queued stamps, the live holder and the dirty nudge unchanged.
            session.execute(
                update(ReconcileCursor)
                .where(
                    col(ReconcileCursor.source) == source,
                    col(ReconcileCursor.pass_queued_at) <= now,
                )
                .values(pass_queued_at=None, pass_priority=None)
            )
        session.commit()
    return bool(claimed)


def _release(
    source: JobKind, holder: str, *, started: datetime, result: PassResult
) -> bool:
    """Release the claim unless a nudge arrived after ``started``."""
    now = utcnow()
    with get_session_factory().scoped_session() as session:
        released = affected(
            session,
            update(ReconcileCursor)
            .where(
                col(ReconcileCursor.source) == source,
                col(ReconcileCursor.holder) == holder,
                or_(
                    col(ReconcileCursor.nudged_at).is_(None),
                    col(ReconcileCursor.nudged_at) <= started,
                ),
            )
            .values(
                holder=None,
                holder_expires_at=None,
                last_pass_finished_at=now,
                last_pass_submitted=result.submitted,
                last_pass_deferred=result.deferred,
            ),
        )
        if not released:
            ttl = timedelta(seconds=max(settings.fence_ttl_seconds, 60))
            session.execute(
                update(ReconcileCursor)
                .where(
                    col(ReconcileCursor.source) == source,
                    col(ReconcileCursor.holder) == holder,
                )
                .values(holder_expires_at=now + ttl, last_pass_started_at=now)
            )
        session.commit()
    return bool(released)


def _force_release(source: JobKind, holder: str) -> None:
    with get_session_factory().scoped_session() as session:
        session.execute(
            update(ReconcileCursor)
            .where(
                col(ReconcileCursor.source) == source,
                col(ReconcileCursor.holder) == holder,
            )
            .values(holder=None, holder_expires_at=None, last_pass_finished_at=utcnow())
        )
        session.commit()


def run_pass(source: JobKind, *, holder: str | None = None) -> PassResult:
    """One claimed, bounded, self-continuing pass over one definition."""
    from app.core.metrics import record_reconcile_pass

    definition = catalog_module.get_catalog().definition(source)
    holder = holder or executors.executor_id()
    total = PassResult()
    started_clock = time.monotonic()
    now = utcnow()
    if not _claim(source, holder, now=now):
        total.count(PassNote.CLAIMED_ELSEWHERE)
        return total
    try:
        for _ in range(_MAX_LOOPS):
            started = now
            result = PassResult()
            _repair(definition, now=now, result=result)
            _discover(definition, now=now, result=result)
            _merge(total, result)
            if result.full:
                now = utcnow()
                continue
            if _release(source, holder, started=started, result=result):
                break
            now = utcnow()
        else:
            _force_release(source, holder)
            nudge(source)
    except Exception:
        _force_release(source, holder)
        raise
    _schedule_next(definition, also=total.cooling_until)
    record_reconcile_pass(
        source,
        time.monotonic() - started_clock,
        {
            "submitted": total.submitted,
            "deferred": total.deferred,
            "interrupted": total.interrupted,
            "failed": total.failed,
            "completed": total.completed,
            "skipped": total.skipped,
        },
    )
    return total


def _merge(total: PassResult, result: PassResult) -> None:
    total.submitted += result.submitted
    total.deferred += result.deferred
    total.interrupted += result.interrupted
    total.failed += result.failed
    total.completed += result.completed
    total.skipped += result.skipped
    total.full = result.full
    if result.cooling_until is not None and (
        total.cooling_until is None or result.cooling_until < total.cooling_until
    ):
        total.cooling_until = result.cooling_until
    for key, value in result.outcomes.items():
        total.outcomes[key] = total.outcomes.get(key, 0) + value


def _schedule_next(definition: JobDefinition, *, also: datetime | None = None) -> None:
    """Ask for a delayed pass at the source's next due time, if it has one.

    ``also`` is a due time the pass itself found (a subject cooling down); the
    earlier of the two wins.
    """
    if definition.source is None:
        return
    now = utcnow()
    with get_session_factory().scoped_session() as session:
        due = definition.source.next_due(session, now=now)
    if also is not None and (due is None or ensure_utc(also) < ensure_utc(due)):
        due = also
    if due is None:
        return
    delay = (ensure_utc(due) - now).total_seconds()
    if 0 < delay <= settings.jobs_reconcile_interval_seconds:
        nudge(definition.name, delay=delay)


def execute_pass(source: JobKind, runner: StepRunner) -> None:
    """Engine entry point for one reconcile execution."""
    from .contracts import NO_RETRY

    runner.run("work.reconcile", lambda: _pass_summary(source), NO_RETRY)


def _pass_summary(source: JobKind) -> int:
    return run_pass(source).submitted


def sweep_lost_passes(*, now: datetime | None = None) -> int:
    """Cancel reconcile passes a dead executor was running.

    Passes are not Jobs, so repair never interrupts them, and their lane is
    global: each one a killed process left running holds a slot everywhere
    until it is cancelled. A pass is idempotent and every source is nudged
    again (at startup, and by each tick), so cancelling one loses nothing.
    """
    stale = executors.stale_ids(now=now)
    if not stale:
        return 0
    engine = catalog_module.get_engine()
    cancelled = 0
    for execution in engine.active():
        if (
            execution.kind is not ExecutionKind.PASS
            or execution.status is not EngineStatus.RUNNING
            or execution.executor_id not in stale
        ):
            continue
        try:
            engine.cancel(execution.execution_id)
            cancelled += 1
        except Exception:  # noqa: BLE001 - the next sweep retries
            logger.warning(
                "lost pass cancel failed", extra={"id": execution.execution_id}
            )
    return cancelled


def sweep_lost_terminal_attempts(*, now: datetime | None = None) -> int:
    """Free engine slots after a terminal SQL write outlives its executor.

    A Job can commit its outcome just before the engine records workflow
    success. Normal repair only sees active Jobs, so this exact dead attempt
    needs engine cancellation without changing its durable domain result.
    """
    stale = executors.stale_ids(now=now)
    if not stale:
        return 0
    engine = catalog_module.get_engine()
    candidates = [
        execution
        for execution in engine.active()
        if execution.kind is ExecutionKind.JOB
        and execution.status is EngineStatus.RUNNING
        and execution.executor_id in stale
    ]
    cancelled = 0
    # Bound every IN query while examining the entire candidate set. Missing
    # or superseded attempts in an early batch cannot hide a later owner.
    batch_size = min(settings.jobs_reconcile_batch, _TERMINAL_RECOVERY_BATCH)
    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset : offset + batch_size]
        identifiers = {execution.execution_id.partition(":")[0] for execution in batch}
        with get_session_factory().scoped_session() as session:
            terminal = session.exec(
                select(Job.id, Job.attempts, Job.execution_epoch).where(
                    col(Job.id).in_(identifiers), col(Job.state).in_(TERMINAL_STATES)
                )
            ).all()
            owned = {
                execution_id(job_id, attempt, epoch)
                for job_id, attempt, epoch in terminal
            }
        targets = [execution for execution in batch if execution.execution_id in owned]
        if not targets:
            continue
        # Engine calls own no caller SQL transaction. A restarted executor may
        # have registered or recovered this workflow since the first snapshot.
        evidence = engine.evidence([execution.execution_id for execution in targets])
        still_stale = executors.stale_ids(now=now)
        for execution in targets:
            seen = evidence.get(execution.execution_id)
            if (
                seen is None
                or seen.status is not EngineStatus.RUNNING
                or seen.executor_id != execution.executor_id
                or seen.executor_id not in still_stale
            ):
                continue
            try:
                engine.cancel(execution.execution_id)
                cancelled += 1
            except Exception:  # noqa: BLE001 - the periodic sweep retries
                logger.warning(
                    "lost terminal attempt cancel failed",
                    extra={"id": execution.execution_id},
                )
    return cancelled


def tick() -> None:
    """Free lost passes and terminal attempt wrappers, then nudge every source."""
    from .submission import nudge_all

    swept = sweep_lost_passes()
    if swept:
        logger.warning("cancelled %d reconcile pass(es) of a lost executor", swept)
    terminal = sweep_lost_terminal_attempts()
    if terminal:
        logger.warning("cancelled %d terminal attempt(s) of a lost executor", terminal)
    nudge_all()


def sweep_foreign_versions() -> int:
    """Cancel executions another application version left behind.

    Their Jobs stay active; the next pass sees the cancellation (or the version
    mismatch) and resubmits them on this version's code.
    """
    engine = catalog_module.get_engine()
    cancelled = 0
    for execution in engine.foreign_version_executions():
        try:
            engine.cancel(execution)
            cancelled += 1
        except Exception:  # noqa: BLE001 - the repair pass settles the rest
            logger.warning("foreign version cancel failed", extra={"id": execution})
    return cancelled


__all__ = [
    "Decision",
    "PassResult",
    "Verdict",
    "decide",
    "execute_pass",
    "run_pass",
    "sweep_foreign_versions",
    "sweep_lost_passes",
    "sweep_lost_terminal_attempts",
    "tick",
]
