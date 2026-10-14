"""The body of one Job attempt, identical under every engine.

An engine calls ``execute_job(job_id, attempt, runner)`` for each execution
it runs. The body re-reads the Job, declines a superseded attempt, waits out a
restore fence, runs the definition's steps and records the outcome. Every
decision taken from changing state goes through ``runner.run`` so a durable
engine replays the same path on recovery.
"""

from __future__ import annotations

import contextvars
import heapq
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock
from typing import Any

from sqlalchemy import bindparam
from sqlmodel import col, select

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.core.work_priority import priority_scope
from app.db.models import ACTIVE_JOB_STATES, Job, JobKind, JobState, WorkPriority
from app.db.session import get_session_factory

from . import catalog as catalog_module
from .contracts import NO_RETRY, JobOutcome, Step, StepRunner, no_hook
from .jobs import Settlement, failure_of, jobs

logger = get_logger(__name__)

_ADMISSION_WAIT_MAX_S = 30.0
# Cache only the query shape. Every forced cancellation checkpoint reads fresh
# state; progress payloads, timestamps, and ORM objects are unnecessary for
# authority.
_WITHDRAWAL_QUERY = select(Job.state, Job.attempts, Job.execution_epoch).where(
    Job.id == bindparam("withdrawal_job_id")
)
# How old an answer a step's paced checkpoint may be given. Steps that only
# wait (for a worker, a permit, a lane) each probe twice a second; one read
# for all of them, no older than this, replaces one read each.
_WATCH_SECONDS = 0.25


class StepOutcome(StrEnum):
    """What one checkpointed step attempt came to; the checkpoint records it."""

    DONE = "done"
    CANCELLED = "cancelled"
    # A restore holds the fence: the step waits and is admitted again later.
    DEFERRED = "deferred"


@dataclass
class ExecutionContext:
    """The ``JobContext`` a step receives."""

    job_id: str
    definition: JobKind
    subject_key: str
    priority: WorkPriority
    execution_id: str
    attempt: int
    execution_epoch: str
    started: float = field(default_factory=time.monotonic, repr=False, compare=False)
    # Reports held back since the last write, oldest first.
    _held: list[dict[str, Any]] = field(
        default_factory=list, init=False, repr=False, compare=False
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)
    # What this execution knows it has already written. Both live in memory
    # only: an execution recovered after a crash settles in full again.
    _ended: bool = field(default=False, init=False, repr=False, compare=False)
    # Whether the flusher has been asked to write this attempt's held reports.
    _watched: bool = field(default=False, init=False, repr=False, compare=False)
    _cleaned: bool = field(default=False, init=False, repr=False, compare=False)

    def _take_held(self) -> list[dict[str, Any]]:
        with self._lock:
            held, self._held = self._held, []
        return held

    def update(self, **fields: Any) -> None:
        with self._lock:
            due = self.started + settings.jobs_progress_hold_seconds
            if time.monotonic() < due:
                self._held.append(fields)
                watch = not self._watched
                self._watched = True
            else:
                watch = None
                held, self._held = self._held, []
        if watch is not None:
            if watch:
                # Written when the hold ends even if the step never reports
                # again: a step blocked on a slow download still shows what it
                # had done before it blocked.
                _flusher.watch(self, due)
            return
        jobs.update(
            self.job_id,
            expected_attempt=self.attempt,
            expected_epoch=self.execution_epoch,
            earlier=held,
            **fields,
        )

    def flush_due(self) -> None:
        """Write held reports once the attempt has run long enough to be watched."""
        with self._lock:
            if (
                not self._held
                or time.monotonic() - self.started < settings.jobs_progress_hold_seconds
            ):
                return
            held, self._held = self._held, []
        jobs.update(
            self.job_id,
            expected_attempt=self.attempt,
            expected_epoch=self.execution_epoch,
            earlier=held[:-1],
            **held[-1],
        )

    def finish(self, outcome: JobOutcome, **fields: Any) -> None:
        self._end(outcome, cleanup=False, **fields)

    def settle(self, outcome: JobOutcome, **fields: Any) -> None:
        self._end(outcome, cleanup=True, **fields)

    def _end(
        self,
        outcome: JobOutcome,
        *,
        cleanup: bool,
        error: str | None = None,
        **fields: Any,
    ) -> Settlement | None:
        """One transaction for what this attempt's end owes.

        The terminal write, the subject's failure hook and the dirty stamps of
        the sources its completion nudges commit together; with ``cleanup``,
        so does the definition's settled cleanup. The passes are queued once
        that commit is durable.
        """
        from .submission import queue_stamped, stamp

        definition = catalog_module.get_catalog().definition(self.definition)
        passes: list[JobKind] = []

        def stamp_completion(session: Any) -> None:
            now = utcnow()
            sources = set(definition.completion_nudges)
            # A pass over this definition finds the next subject, or submits a
            # Job the engine has not taken yet. Request-originated work has no
            # subjects to find, so with nothing waiting to be submitted the
            # pass would only read; the tick still repairs what a crash left.
            if definition.source is not None or jobs.awaits_submission(
                session, definition.name, besides=self.job_id
            ):
                sources.add(definition.name)
            for source in sorted(sources):
                if stamp(session, source, now=now):
                    passes.append(source)

        owes_cleanup = definition.on_settled is not no_hook
        settlement = jobs.settle(
            self.job_id,
            self.attempt,
            outcome,
            execution_epoch=self.execution_epoch,
            on_failure=definition.on_failure,
            on_settled=definition.on_settled if cleanup and owes_cleanup else None,
            on_transition=stamp_completion,
            error=error,
            earlier=self._take_held(),
            **fields,
        )
        if settlement is None:
            return None
        if settlement.transitioned:
            queue_stamped(passes)
            self._ended = True
        if settlement.cleaned or not owes_cleanup:
            self._cleaned = True
        return settlement

    @property
    def ended(self) -> bool:
        """Whether a write of this execution made the Job terminal."""
        return self._ended

    @property
    def cleaned(self) -> bool:
        """Whether the settled cleanup committed (or the definition has none)."""
        return self._cleaned

    def cancelled(self) -> bool:
        return _withdrawn(self.job_id, self.attempt, self.execution_epoch)

    def nudge(self, source: JobKind) -> None:
        from .submission import nudge

        nudge(source)


class _HeldProgressFlusher:
    """Writes held progress when its hold ends, for every attempt in the process.

    One thread, started when something is first held and gone again after a
    while with nothing to do. An attempt that ends inside its hold has already
    carried its reports into its terminal write; its turn here finds nothing.
    """

    _IDLE_SECONDS = 30.0

    def __init__(self) -> None:
        self._wake = threading.Condition()
        self._due: list[tuple[float, int, contextvars.Context, ExecutionContext]] = []
        self._sequence = 0
        self._thread: threading.Thread | None = None

    def watch(self, context: ExecutionContext, due: float) -> None:
        with self._wake:
            self._sequence += 1
            # The caller's context carries its session factory to the write.
            heapq.heappush(
                self._due, (due, self._sequence, contextvars.copy_context(), context)
            )
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="work-progress-flush", daemon=True
                )
                self._thread.start()
            self._wake.notify()

    def forget(self) -> None:
        with self._wake:
            self._due.clear()

    def _run(self) -> None:
        while True:
            with self._wake:
                while not self._due or self._due[0][0] > time.monotonic():
                    timeout = (
                        self._due[0][0] - time.monotonic()
                        if self._due
                        else self._IDLE_SECONDS
                    )
                    if not self._wake.wait(timeout) and not self._due:
                        self._thread = None
                        return
                _due, _sequence, caller, context = heapq.heappop(self._due)
            try:
                caller.run(context.flush_due)
            except Exception:  # noqa: BLE001 - progress is display only
                logger.exception(
                    "held progress was not written", extra={"job_id": context.job_id}
                )


_flusher = _HeldProgressFlusher()


def forget_held_progress() -> None:
    """Drop every pending flush. For a process whose database was replaced."""
    _flusher.forget()


def _withdrawn(
    job_id: str, attempt: int | None = None, execution_epoch: str | None = None
) -> bool:
    """Whether this execution is cancelled, missing or superseded."""
    with get_session_factory().scoped_session() as session:
        # A typed scalar read needs no ORM execution/identity-map machinery.
        # Keep the factory and the fresh, short transaction for every probe.
        row = (
            session.connection()
            .execute(_WITHDRAWAL_QUERY, {"withdrawal_job_id": job_id})
            .first()
        )
        if row is None:
            return True
        state, current_attempt, current_epoch = row
        return (
            state is JobState.CANCELLED
            or attempt is not None
            and (
                current_attempt != attempt
                or state is JobState.QUEUED
                or current_epoch != execution_epoch
            )
        )


def _is_withdrawn(
    row: tuple[JobState, int, str] | None, attempt: int, execution_epoch: str
) -> bool:
    if row is None:
        return True
    state, current_attempt, current_epoch = row
    return (
        state is JobState.CANCELLED
        or current_attempt != attempt
        or state is JobState.QUEUED
        or current_epoch != execution_epoch
    )


class _WithdrawalWatch:
    """One read answers the paced checkpoints of every step this process runs.

    A step registers its attempt for as long as it runs. A paced checkpoint
    takes the last answer read for it when that is recent enough, and
    otherwise reads every registered attempt at once. Only waiting is served
    from here: a forced checkpoint, the one that guards a publication, reads
    its own Job afresh.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        # (job id, attempt, epoch) -> the last answer read, if any
        self._attempts: dict[tuple[str, int, str], bool | None] = {}
        self._read_at = 0.0

    def add(self, job_id: str, attempt: int, execution_epoch: str) -> None:
        with self._lock:
            self._attempts[(job_id, attempt, execution_epoch)] = None

    def remove(self, job_id: str, attempt: int, execution_epoch: str) -> None:
        with self._lock:
            self._attempts.pop((job_id, attempt, execution_epoch), None)

    def withdrawn(self, job_id: str, attempt: int, execution_epoch: str) -> bool:
        key = (job_id, attempt, execution_epoch)
        with self._lock:
            answer = self._attempts[key]
            if answer is None or time.monotonic() - self._read_at >= _WATCH_SECONDS:
                self._read()
                answer = self._attempts[key]
            assert answer is not None
            return answer

    def _read(self) -> None:
        with get_session_factory().scoped_session() as session:
            rows = {
                job_id: (state, attempts, epoch)
                for job_id, state, attempts, epoch in session.connection().execute(
                    select(Job.id, Job.state, Job.attempts, Job.execution_epoch).where(
                        col(Job.id).in_(sorted({key[0] for key in self._attempts}))
                    )
                )
            }
        for key in self._attempts:
            job_id, attempt, epoch = key
            self._attempts[key] = _is_withdrawn(rows.get(job_id), attempt, epoch)
        self._read_at = time.monotonic()


_watch = _WithdrawalWatch()


def _begin(job_id: str, attempt: int, execution_epoch: str) -> dict[str, str] | None:
    """Claim the attempt: ``None`` when it is superseded or the Job is settled.

    The claim is the checkpoint a durable engine replays, so it returns plain
    strings; ``execute_job`` parses them back into the Job's typed identity.
    """
    with get_session_factory().scoped_session() as session:
        row = jobs.lock_execution(
            session, job_id, epoch=execution_epoch, states=ACTIVE_JOB_STATES
        )
        if row is None:
            return None
        if row.attempts > attempt:
            return None
        now = utcnow()
        row.attempts = max(row.attempts, attempt)
        row.submitted_epoch = execution_epoch
        row.state = JobState.RUNNING
        row.started_at = row.started_at or now
        row.updated_at = now
        # Read before the commit expires the row: reading it afterwards is a
        # second transaction.
        claim = {
            "kind": row.kind.value,
            "subject_key": row.subject_key,
            "priority": row.priority.value,
        }
        session.add(row)
        session.commit()
        return claim


def _run_step(step: Step, context: ExecutionContext, *, mutating: bool) -> str:
    """One admitted step attempt; returns a ``StepOutcome`` value.

    A step of a mutating definition is a write-capable operation for
    maintenance purposes: a restore drains it like any other mutation, and a
    step that cannot be admitted (a restore holds the fence) is deferred, not
    failed. What the step itself returns is not kept: its effects are in the
    domain, and the Job's status is what it reported.
    """
    from app.core.metrics import record_step
    from app.runtime.maintenance import begin_mutating_operation, end_mutating_operation

    if mutating and not begin_mutating_operation():
        # A withdrawn attempt ends here rather than wait out the restore.
        if _withdrawn(context.job_id, context.attempt, context.execution_epoch):
            return StepOutcome.CANCELLED.value
        return StepOutcome.DEFERRED.value
    started = time.monotonic()
    try:
        definition = catalog_module.get_catalog().definition(context.definition)
        with get_session_factory().scoped_session() as session:
            # A read is the whole check: the session ends before the step
            # runs, so a row lock taken here protected nothing and only made
            # every step queue behind the database's single writer.
            if not jobs.owns_execution(
                session,
                context.job_id,
                epoch=context.execution_epoch,
                attempt=context.attempt,
                state=JobState.RUNNING,
            ):
                return StepOutcome.CANCELLED.value
            refused = definition.admission(session)
        if refused is not None:
            context.finish(JobOutcome.CANCELLED, error=refused, retryable=False)
            return StepOutcome.CANCELLED.value

        def withdrawn() -> bool:
            # The probe is the one thing a long step is sure to keep calling,
            # so it is also what lets held progress out.
            context.flush_due()
            return _withdrawn(context.job_id, context.attempt, context.execution_epoch)

        def waiting() -> bool:
            context.flush_due()
            return _watch.withdrawn(*attempt)

        attempt = (context.job_id, context.attempt, context.execution_epoch)
        _watch.add(*attempt)
        try:
            with (
                cancellation_scope(withdrawn, idle=waiting),
                priority_scope(context.priority),
            ):
                step.fn(context)
        finally:
            _watch.remove(*attempt)
    except OperationCancelled:
        record_step(
            context.definition, step.name, "cancelled", time.monotonic() - started
        )
        return StepOutcome.CANCELLED.value
    except BaseException:
        record_step(context.definition, step.name, "error", time.monotonic() - started)
        raise
    finally:
        if mutating:
            end_mutating_operation()
    record_step(context.definition, step.name, "ok", time.monotonic() - started)
    return StepOutcome.DONE.value


def _settle(context: ExecutionContext, error: str | None) -> None:
    """Record the attempt's outcome and nudge whoever waits on completion.

    A step may already have finished the Job (a recorded failure, a partial
    completion); the first terminal write wins and already carried the
    completion stamps. What is still owed is written in one transaction: the
    terminal write if no step made it, and the definition's settled cleanup if
    it has one and no step ran it. Nothing owed means no transaction at all.
    An execution recovered after a crash knows none of this and settles in
    full; every part is idempotent.
    """
    if context.ended and context.cleaned:
        return
    recovered = not context.ended
    settlement = context._end(
        JobOutcome.COMPLETED if error is None else JobOutcome.FAILED,
        cleanup=True,
        error=error,
    )
    if settlement is None:
        return
    definition = catalog_module.get_catalog().definition(context.definition)
    if not context.cleaned:
        jobs.reconcile_settled_attempt(
            context.job_id,
            context.attempt,
            definition.on_settled,
            execution_epoch=context.execution_epoch,
        )
    if recovered and not settlement.transitioned:
        # An earlier execution ended the Job; whether it queued its passes is
        # not known here, and a repeated nudge costs one covered stamp.
        from .submission import nudge

        for source in dict.fromkeys((definition.name, *definition.completion_nudges)):
            nudge(source)


def execute_job(
    job_id: str, attempt: int, runner: StepRunner, *, execution_epoch: str | None = None
) -> None:
    """Run one attempt of ``job_id`` to its recorded outcome."""
    if execution_epoch is None:
        return
    begun = runner.run(
        f"work.begin#{attempt}",
        lambda: _begin(job_id, attempt, execution_epoch),
        NO_RETRY,
    )
    if begun is None:
        return
    definition = catalog_module.get_catalog().definition(JobKind(begun["kind"]))
    subject_key = begun["subject_key"]
    context = ExecutionContext(
        job_id=job_id,
        definition=definition.name,
        subject_key=subject_key,
        priority=WorkPriority(begun["priority"]),
        execution_id=f"{job_id}:{execution_epoch}:{attempt}",
        attempt=attempt,
        execution_epoch=execution_epoch,
    )
    error: str | None = None
    try:
        for step in definition.steps:
            wait = 1.0
            while True:
                outcome = StepOutcome(
                    runner.run(
                        step.name,
                        lambda step=step: _run_step(
                            step, context, mutating=definition.mutating
                        ),
                        step.retry,
                    )
                )
                if outcome is not StepOutcome.DEFERRED:
                    break
                runner.sleep(wait)
                wait = min(wait * 2, _ADMISSION_WAIT_MAX_S)
            if outcome is StepOutcome.CANCELLED:
                return
    except BaseException as exc:
        if runner.is_cancellation(exc):
            raise
        if not isinstance(exc, Exception):
            raise
        logger.exception(
            "job step failed", extra={"job_id": job_id, "kind": definition.name.value}
        )
        # An exception with no message still names what failed.
        error = failure_of(exc)
    runner.run("work.settle", lambda: _settle(context, error), NO_RETRY)
