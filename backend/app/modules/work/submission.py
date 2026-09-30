"""Turning Jobs into engine executions, and nudging the reconciler.

Hot paths never submit a specific job. They record intent in the domain (and,
for request-originated work, a queued Job) in the same transaction as the
domain change, then call ``nudge``: a request to run one definition's
reconcile pass soon. If the nudge is lost, the next tick finds the same work;
nothing is lost with it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlmodel import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    ACTIVE_JOB_STATES,
    Job,
    JobKind,
    JobState,
    ReconcileCursor,
    WorkPriority,
)
from app.db.session import get_session_factory

from . import catalog as catalog_module
from .contracts import (
    Deduplicated,
    JobSubmission,
    Partitioned,
    PassSubmission,
    SubmitOutcome,
)

logger = get_logger(__name__)

# Engine priority: lower runs first. Interactive work always overtakes backfill.
PRIORITY_RANK = {WorkPriority.INTERACTIVE: 1, WorkPriority.BACKFILL: 1000}


def execution_id(job_id: str, attempt: int) -> str:
    """The engine key of one attempt: exactly once per (Job, attempt)."""
    return f"{job_id}:{attempt}"


def dedupe_key(definition: JobKind, subject_key: str) -> str:
    """At most one active execution per subject, whatever its Job id."""
    return f"{definition.value}|{subject_key}"


def submit(job_id: str, *, now: datetime | None = None) -> SubmitOutcome | None:
    """Submit the next attempt of an active Job. ``None`` when nothing to do.

    The attempt number is committed only after the engine accepted it. A crash
    in between re-derives the same attempt, so the same execution id, and the
    engine returns the execution it already has.
    """
    now = now or utcnow()
    engine = catalog_module.get_engine()
    catalog = catalog_module.get_catalog()
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is None or row.state not in ACTIVE_JOB_STATES:
            return None
        definition = catalog.definition(row.kind)
        if definition.admission(session) is not None:
            return None
        attempt = row.attempts + 1
        submission = JobSubmission(
            execution_id=execution_id(row.id, attempt),
            job_id=row.id,
            definition=row.kind,
            subject_key=row.subject_key,
            lane=definition.lane,
            priority=row.priority,
            attempt=attempt,
            routing=Deduplicated(dedupe_key(row.kind, row.subject_key))
            if definition.partition is None
            else Partitioned(definition.partition(row.subject_key)),
        )
    outcome = engine.submit(submission)
    if outcome is SubmitOutcome.DEDUPLICATED:
        # An older attempt of this subject is still active in the engine. The
        # reconciler cancels or settles it; this attempt is not recorded.
        return outcome
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is not None and row.attempts < attempt:
            row.attempts = attempt
            if row.state is JobState.INTERRUPTED:
                row.state = JobState.QUEUED
            row.updated_at = now
            session.add(row)
            session.commit()
    return outcome


def _cursor(session: Session, source: JobKind) -> ReconcileCursor:
    cursor = session.get(ReconcileCursor, source)
    if cursor is None:
        cursor = ReconcileCursor(source=source)
        session.add(cursor)
    return cursor


def _covered(cursor: ReconcileCursor, priority: WorkPriority, now: datetime) -> bool:
    """Whether a pass already queued, recently enough, serves this nudge."""
    if cursor.pass_queued_at is None:
        return False
    grace = timedelta(seconds=settings.jobs_submit_grace_seconds)
    if ensure_utc(cursor.pass_queued_at) <= now - grace:
        return False
    return (
        priority is WorkPriority.BACKFILL
        or cursor.pass_priority is WorkPriority.INTERACTIVE
    )


def nudge(
    source: JobKind,
    *,
    now: datetime | None = None,
    delay: float | None = None,
    priority: WorkPriority = WorkPriority.INTERACTIVE,
) -> None:
    """Ask for ``source``'s reconcile pass soon. Never raises into the caller.

    The dirty stamp is written first. A pass is enqueued only when none of at
    least this priority is already queued; a pass that is running will see the
    stamp when it tries to release its claim and run again. A hot path's nudge
    is interactive; the tick's sweep of every source is backfill. So an upload
    arriving just after startup gets its own interactive pass rather than
    waiting behind the backfill pass the sweep queued for the same source.
    A delayed nudge (a source's next due time) only enqueues its pass: the
    work it waits for is not due yet, so it marks nothing dirty.
    """
    if not catalog_module.bound():
        return
    now = now or utcnow()
    try:
        catalog_module.get_catalog().definition(source)
        if delay is None:
            with get_session_factory().scoped_session() as session:
                cursor = _cursor(session, source)
                cursor.nudged_at = now
                covered = _covered(cursor, priority, now)
                if not covered:
                    cursor.pass_queued_at = now
                    cursor.pass_priority = priority
                session.add(cursor)
                session.commit()
            if covered:
                return
        catalog_module.get_engine().submit(
            PassSubmission(
                execution_id=f"reconcile:{source.value}:{uuid.uuid4().hex}",
                source=source,
                priority=priority,
                delay_seconds=delay,
            )
        )
    except Exception:  # noqa: BLE001 - the tick recovers a lost nudge
        logger.exception("reconciler nudge failed", extra={"source": source.value})


def nudge_after_commit(session: Session, source: JobKind) -> None:
    """Nudge ``source`` once ``session``'s transaction commits (never before).

    For intent recorded inside a caller's transaction (a transactional
    outbox): nudging before the commit could run a pass that cannot see the
    row yet, and nudging after a rollback would be noise.
    """
    from sqlalchemy import event

    # One nudge per source per transaction, however many rows it recorded, and
    # one pair of listeners per session, however many transactions it runs.
    pending: set[JobKind] | None = session.info.get(_PENDING_NUDGES)
    if pending is None:
        pending = session.info[_PENDING_NUDGES] = set()
        event.listen(session, "after_commit", _nudge_committed)
        event.listen(session, "after_soft_rollback", _forget_rolled_back)
    pending.add(source)


_PENDING_NUDGES = "work_nudges_after_commit"


def _pending_nudges(session: Session) -> set[JobKind]:
    return session.info[_PENDING_NUDGES]


def _nudge_committed(session: Session) -> None:
    # A released savepoint is reported as a commit too. Its transaction still
    # holds the write lock that the nudge's own session would wait on.
    if session.get_nested_transaction() is not None:
        return
    pending = _pending_nudges(session)
    sources = sorted(pending)
    pending.clear()
    for source in sources:
        nudge(source)


def _forget_rolled_back(session: Session, previous_transaction) -> None:
    if previous_transaction.nested:
        return
    _pending_nudges(session).clear()


def forget_queued_passes() -> int:
    """Clear every cursor's queued-pass mark; returns how many were set.

    The mark suppresses duplicate nudges while a pass waits in the engine. A
    process that died (or an older version whose executions a startup sweep
    cancelled) can leave marks for passes that will never run, which would
    hold back this process's startup nudges for the whole grace window. The
    pass claim is single-flight, so at worst this costs one extra pass.
    """
    from sqlalchemy import update
    from sqlmodel import col

    from app.db.affected import affected

    with get_session_factory().scoped_session() as session:
        cleared = affected(
            session,
            update(ReconcileCursor)
            .where(col(ReconcileCursor.pass_queued_at).is_not(None))
            .values(pass_queued_at=None, pass_priority=None),
        )
        session.commit()
    return cleared


def nudge_all(*, now: datetime | None = None) -> None:
    """The tick: nudge every definition. Each pass is cheap when idle."""
    if not catalog_module.bound():
        return
    for name in sorted(catalog_module.get_catalog().definitions):
        nudge(name, now=now, priority=WorkPriority.BACKFILL)
