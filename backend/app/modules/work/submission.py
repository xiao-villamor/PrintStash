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
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import event, update
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
    expedite: bool = False,
) -> SubmitOutcome | None:
    """Submit the next attempt of an active Job. ``None`` when nothing to do.

    The attempt number is committed only after the engine accepted it. A crash
    in between re-derives the same attempt, so the same execution id, and the
    engine returns the execution it already has. A reserved backfill submission
    must still name the exact current admitted epoch when arguments are built.
    """
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is None:
            return None
        prepared = prepare_submission(
            session,
            row,
            reserved_backfill_epoch=reserved_backfill_epoch,
            expedite=expedite,
        )
    return None if prepared is None else hand_over(prepared, now=now)


@dataclass(frozen=True)
class PreparedSubmission:
    """What one read of an active Job decided to hand the engine."""

    submission: JobSubmission
    # The Job's current epoch was never submitted: an execution the engine
    # still holds for this subject belongs to a superseded one.
    pending_epoch: bool


def prepare_submission(
    session: Session,
    row: Job,
    *,
    reserved_backfill_epoch: str | None = None,
    expedite: bool = False,
) -> PreparedSubmission | None:
    """The next attempt of ``row`` as read in ``session``; ``None`` when it is
    settled, refused admission, or no longer the reserved backfill epoch."""
    if row.state not in ACTIVE_JOB_STATES:
        return None
    if reserved_backfill_epoch is not None and (
        row.execution_epoch != reserved_backfill_epoch
        or row.backfill_admission_epoch != reserved_backfill_epoch
    ):
        return None
    definition = catalog_module.get_catalog().definition(row.kind)
    if definition.admission(session) is not None:
        return None
    attempt = row.attempts + 1
    return PreparedSubmission(
        submission=JobSubmission(
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
            expedite=expedite,
        ),
        pending_epoch=row.submitted_epoch != row.execution_epoch,
    )


def hand_over(
    prepared: PreparedSubmission, *, now: datetime | None = None
) -> SubmitOutcome:
    """Submit a prepared attempt and record it once the engine accepted it."""
    now = now or utcnow()
    engine = catalog_module.get_engine()
    submission = prepared.submission
    pending_epoch = prepared.pending_epoch
    job_id = submission.job_id
    attempt = submission.attempt
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
    row yet, and nudging after a rollback would be noise.

    On SQLite the dirty mark is written as that transaction commits, so the
    nudge costs its caller no second turn at the single writer; only the pass
    is queued afterwards. It is written at the commit and not here, because a
    write here would take the writer from this call until whenever the caller
    commits. Elsewhere writers do not queue behind one another and the whole
    nudge happens after the commit.
    """
    # One nudge per source per transaction, however many rows it recorded.
    session.info.setdefault(_PENDING_NUDGES, {}).setdefault(source, None)


# source -> whether its pass must be queued once the transaction commits;
# ``None`` while no mark has been written with it, in which case the nudge
# runs on its own after the commit.
_PENDING_NUDGES = "work_nudges_after_commit"


def _pending_nudges(session: Session) -> dict[JobKind, bool | None]:
    return session.info.get(_PENDING_NUDGES) or {}


def _stamp_committing(session: Session) -> None:
    """Write the dirty marks of a committing transaction's nudges, on SQLite.

    A nudge never fails the commit it rides on: a mark that cannot be written
    is left for the nudge after the commit. No savepoint guards it, because a
    session that is committing cannot open one; on SQLite a statement that
    fails undoes only itself.
    """
    pending = _pending_nudges(session)
    # A released savepoint is reported as a commit too; only the outermost
    # commit makes the intent durable.
    if not pending or session.get_nested_transaction() is not None:
        return
    if session.get_bind().dialect.name != "sqlite" or not catalog_module.bound():
        return
    now = utcnow()
    for source in sorted(pending):
        try:
            catalog_module.get_catalog().definition(source)
            pending[source] = stamp(session, source, now=now)
        except Exception:  # noqa: BLE001 - the nudge after the commit still happens
            logger.exception("reconciler nudge failed", extra={"source": source.value})
            pending[source] = None


def _nudge_committed(session: Session) -> None:
    # A released savepoint is reported as a commit too. Its transaction still
    # holds the write lock that the nudge's own session would wait on.
    pending = _pending_nudges(session)
    if not pending or session.get_nested_transaction() is not None:
        return
    outcomes = sorted(pending.items())
    pending.clear()
    queue_stamped(source for source, needed in outcomes if needed)
    for source, needed in outcomes:
        if needed is None:
            nudge(source)


def _forget_rolled_back(session: Session, previous_transaction) -> None:
    if previous_transaction.nested:
        return
    _pending_nudges(session).clear()


# Registered once, for every session: a listener added to one session while
# that session is dispatching an event would change the list being walked.
event.listen(Session, "before_commit", _stamp_committing)
event.listen(Session, "after_commit", _nudge_committed)
event.listen(Session, "after_soft_rollback", _forget_rolled_back)


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
    """The tick: nudge every definition that a pass could find work for.

    A definition with a source is always nudged: its source decides what is
    pending. One without (requested work) has nothing but its own Jobs to
    repair, so it is nudged only while one of them is active. Its pass would
    otherwise be an engine workflow that reads two empty results, every tick,
    for every such definition, and stays in the engine's history for a week.
    """
    if not catalog_module.bound():
        return
    catalog = catalog_module.get_catalog()
    with get_session_factory().scoped_session() as session:
        active = set(
            session.exec(
                select(Job.kind)
                .where(col(Job.state).in_(ACTIVE_JOB_STATES))
                .distinct()
            ).all()
        )
    for name, definition in sorted(catalog.definitions.items()):
        if definition.source is None and name not in active:
            continue
        nudge(name, now=now, priority=WorkPriority.BACKFILL)
