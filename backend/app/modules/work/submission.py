"""Turning Jobs into engine executions, and nudging the reconciler.

Hot paths never submit a specific job. They record intent in the domain (and,
for request-originated work, a queued Job) in the same transaction as the
domain change, then call ``nudge``: a request to run one definition's
reconcile pass soon. If the nudge is lost, the next tick finds the same work;
nothing is lost with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

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
    WorkPriority,
)
from app.db.session import get_session_factory
from app.db.transactions import begin_write

from . import catalog as catalog_module
from .contracts import (
    Deduplicated,
    ExecutionKind,
    JobSubmission,
    Partitioned,
    PassSubmission,
    SubmitOutcome,
)
from .jobs import TERMINAL_STATES, jobs

logger = get_logger(__name__)

# Engine priority: lower runs first. Interactive work always overtakes backfill.
PRIORITY_RANK = {WorkPriority.INTERACTIVE: 1, WorkPriority.BACKFILL: 1000}


def execution_id(job_id: str, attempt: int, execution_epoch: str) -> str:
    """The engine key of one attempt: exactly once per (Job, epoch, attempt)."""
    return f"{job_id}:{execution_epoch}:{attempt}"


def dedupe_key(definition: JobKind, subject_key: str) -> str:
    """At most one active execution per subject, whatever its Job id."""
    return f"{definition.value}|{subject_key}"


def submit(
    job_id: str,
    *,
    now: datetime | None = None,
    reserved_backfill_epoch: str | None = None,
) -> SubmitOutcome | None:
    """Submit the next attempt of an active Job. ``None`` when nothing to do.

    The attempt number is committed only after the engine accepted it. A crash
    in between re-derives the same attempt, so the same execution id, and the
    engine returns the execution it already has. A reserved backfill submission
    must still name the exact current admitted epoch when arguments are built.
    """
    now = now or utcnow()
    engine = catalog_module.get_engine()
    catalog = catalog_module.get_catalog()
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is None or row.state not in ACTIVE_JOB_STATES:
            return None
        if reserved_backfill_epoch is not None and (
            row.execution_epoch != reserved_backfill_epoch
            or row.backfill_admission_epoch != reserved_backfill_epoch
        ):
            return None
        definition = catalog.definition(row.kind)
        if definition.admission(session) is not None:
            return None
        attempt = row.attempts + 1
        submission = JobSubmission(
            execution_id=execution_id(row.id, attempt, row.execution_epoch),
            job_id=row.id,
            definition=row.kind,
            subject_key=row.subject_key,
            lane=definition.lane,
            priority=row.priority,
            attempt=attempt,
            execution_epoch=row.execution_epoch,
            routing=Deduplicated(dedupe_key(row.kind, row.subject_key))
            if definition.partition is None
            else Partitioned(definition.partition(row.subject_key)),
        )
        pending_epoch = row.submitted_epoch != row.execution_epoch
    outcome = engine.submit(submission)
    if pending_epoch and outcome is SubmitOutcome.DEDUPLICATED:
        _cancel_superseded(submission)
        outcome = engine.submit(submission)
    if outcome is SubmitOutcome.DEDUPLICATED:
        # An older attempt of this subject is still active in the engine. The
        # reconciler cancels or settles it; this attempt is not recorded.
        return outcome
    with get_session_factory().scoped_session() as session:
        row = jobs.lock_execution(session, job_id, epoch=submission.execution_epoch)
        # The body can legitimately finish before engine.submit returns. Its
        # terminal result is not evidence of retired authority; only cancellation
        # or a missing/replaced epoch warrants cancelling this exact execution.
        superseded = row is None or row.state is JobState.CANCELLED
        if (
            row is not None
            and row.state in ACTIVE_JOB_STATES
            and row.attempts < attempt
        ):
            row.attempts = attempt
            row.submitted_epoch = submission.execution_epoch
            if row.state is JobState.INTERRUPTED:
                row.state = JobState.QUEUED
            row.updated_at = now
            session.add(row)
            session.commit()
    if superseded:
        engine.cancel(submission.execution_id)

    return outcome


def _cancel_superseded(submission: JobSubmission) -> None:
    """Free subject dedupe after a retry or a replaced terminal Job.

    The engine contract only exposes an active catalogue, not subject lookup.
    Read it only when an epoch's first submission is deduplicated; successful
    submissions and ordinary resubmits use exact execution IDs. No database lock spans engine I/O.
    """
    engine = catalog_module.get_engine()
    # Observe executions first: a later DB snapshot can prove these exact IDs
    # obsolete, but cannot accidentally authorize cancellation of a future epoch.
    active = engine.active()
    with get_session_factory().scoped_session() as session:
        related = session.exec(
            select(Job).where(
                Job.kind == submission.definition,
                Job.subject_key == submission.subject_key,
            )
        ).all()
        epochs = {
            row.id: row.execution_epoch
            for row in related
            if row.id == submission.job_id or row.state in TERMINAL_STATES
        }
    for execution in active:
        if execution.kind is not ExecutionKind.JOB:
            continue
        for job_id, epoch in epochs.items():
            if not execution.execution_id.startswith(f"{job_id}:"):
                continue
            current = job_id == submission.job_id and execution.execution_id.startswith(
                f"{job_id}:{epoch}:"
            )
            if not current:
                engine.cancel(execution.execution_id)
            break


def _covered(
    queued_at: datetime | None,
    queued_priority: WorkPriority | None,
    priority: WorkPriority,
    now: datetime,
) -> bool:
    """Whether a pass already queued, recently enough, serves this nudge."""
    if queued_at is None:
        return False
    grace = timedelta(seconds=settings.jobs_submit_grace_seconds)
    if ensure_utc(queued_at) <= now - grace:
        return False
    return (
        priority is WorkPriority.BACKFILL or queued_priority is WorkPriority.INTERACTIVE
    )


def stamp(
    session: Session,
    source: JobKind,
    *,
    now: datetime,
    priority: WorkPriority = WorkPriority.INTERACTIVE,
) -> bool:
    """Mark ``source`` dirty in the caller's transaction; never commits.

    Returns whether a pass has to be queued once that transaction commits. A
    pass that is running sees the stamp when it tries to release its claim and
    runs again; one already queued at this priority or better covers it.
    Written as plain statements so a caller's session keeps no cursor object.
    """
    table = getattr(ReconcileCursor, "__table__")  # noqa: B009
    # Mark a live owner dirty atomically with its release. It will consume
    # this nudge before releasing; no extra engine workflow is needed merely
    # to lose the same single-flight claim.
    if affected(
        session,
        update(ReconcileCursor)
        .where(
            col(ReconcileCursor.source) == source,
            col(ReconcileCursor.holder).is_not(None),
            col(ReconcileCursor.holder_expires_at) > now,
        )
        .values(nudged_at=now)
        .execution_options(synchronize_session=False),
    ):
        return False
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    session.execute(
        insert(table)
        .values(source=source)
        .on_conflict_do_nothing(index_elements=["source"])
    )
    queued_at, queued_priority = session.execute(
        select(ReconcileCursor.pass_queued_at, ReconcileCursor.pass_priority).where(
            col(ReconcileCursor.source) == source
        )
    ).one()
    covered = _covered(queued_at, queued_priority, priority, now)
    values: dict[str, object] = {"nudged_at": now}
    if not covered:
        values.update(pass_queued_at=now, pass_priority=priority)
    session.execute(
        update(ReconcileCursor)
        .where(col(ReconcileCursor.source) == source)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return not covered


def queue_pass(
    source: JobKind,
    *,
    priority: WorkPriority = WorkPriority.INTERACTIVE,
    delay: float | None = None,
) -> None:
    """Queue one reconcile pass over ``source`` in the engine."""
    catalog_module.get_engine().submit(
        PassSubmission(
            execution_id=f"reconcile:{source.value}:{uuid.uuid4().hex}",
            source=source,
            priority=priority,
            delay_seconds=delay,
        )
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
                needed = stamp(session, source, now=now, priority=priority)
                session.commit()
            if not needed:
                return
        queue_pass(source, priority=priority, delay=delay)
    except Exception:  # noqa: BLE001 - the tick recovers a lost nudge
        logger.exception("reconciler nudge failed", extra={"source": source.value})


def queue_stamped(sources: Iterable[JobKind]) -> None:
    """Queue the passes ``stamp`` asked for, after its transaction committed.

    Never raises into the caller: the stamp is already durable, and the tick
    recovers a pass that could not be queued.
    """
    for source in sources:
        try:
            queue_pass(source)
        except Exception:  # noqa: BLE001 - the tick recovers a lost nudge
            logger.exception("reconciler nudge failed", extra={"source": source.value})


def nudge_after_commit(session: Session, source: JobKind) -> None:
    """Nudge ``source`` once ``session``'s transaction commits (never before).

    For intent recorded inside a caller's transaction (a transactional
    outbox): nudging before the commit could run a pass that cannot see the
    row yet, and nudging after a rollback would be noise. The dirty stamp is
    written as that transaction commits, so the nudge costs it no transaction
    of its own; only the pass is queued afterwards.
    """
    from sqlalchemy import event

    # One nudge per source per transaction, however many rows it recorded, and
    # one set of listeners per session, however many transactions it runs.
    pending: set[JobKind] | None = session.info.get(_PENDING_NUDGES)
    if pending is None:
        pending = session.info[_PENDING_NUDGES] = set()
        session.info[_STAMPED_NUDGES] = {}
        event.listen(session, "before_commit", _stamp_committing)
        event.listen(session, "after_commit", _nudge_committed)
        event.listen(session, "after_soft_rollback", _forget_rolled_back)
    pending.add(source)


_PENDING_NUDGES = "work_nudges_after_commit"
# source -> whether its pass must be queued; ``None`` when the stamp could not
# be written with the transaction and the nudge runs on its own afterwards.
_STAMPED_NUDGES = "work_nudges_stamped"


def _pending_nudges(session: Session) -> set[JobKind]:
    return session.info[_PENDING_NUDGES]


def _stamped_nudges(session: Session) -> dict[JobKind, bool | None]:
    return session.info[_STAMPED_NUDGES]


def _stamp_committing(session: Session) -> None:
    # A released savepoint is reported as a commit too; only the outermost
    # commit makes the intent durable.
    if session.get_nested_transaction() is not None:
        return
    pending = _pending_nudges(session)
    if not pending or not catalog_module.bound():
        return
    stamped = _stamped_nudges(session)
    now = utcnow()
    # Sorted, so two transactions stamping the same sources take the cursor
    # rows in one order.
    for source in sorted(pending):
        try:
            catalog_module.get_catalog().definition(source)
            begin_write(session)
            with session.begin_nested():
                stamped[source] = stamp(session, source, now=now)
        except Exception:  # noqa: BLE001 - a nudge never fails the caller's commit
            logger.exception("reconciler nudge failed", extra={"source": source.value})
            stamped[source] = None
    pending.clear()


def _nudge_committed(session: Session) -> None:
    # A released savepoint is reported as a commit too. Its transaction still
    # holds the write lock that the nudge's own session would wait on.
    if session.get_nested_transaction() is not None:
        return
    stamped = _stamped_nudges(session)
    outcomes = sorted(stamped.items())
    stamped.clear()
    queue_stamped(source for source, needed in outcomes if needed)
    for source, needed in outcomes:
        if needed is None:
            nudge(source)


def _forget_rolled_back(session: Session, previous_transaction) -> None:
    if previous_transaction.nested:
        return
    _pending_nudges(session).clear()
    _stamped_nudges(session).clear()


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
