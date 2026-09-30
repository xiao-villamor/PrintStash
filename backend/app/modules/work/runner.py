"""The body of one Job attempt, identical under every engine.

An engine calls ``execute_job(job_id, attempt, runner)`` for each execution
it runs. The body re-reads the Job, declines a superseded attempt, waits out a
restore fence, runs the definition's steps and records the outcome. Every
decision taken from changing state goes through ``runner.run`` so a durable
engine replays the same path on recovery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import ACTIVE_JOB_STATES, Job, JobKind, JobState, WorkPriority
from app.db.session import get_session_factory

from . import catalog as catalog_module
from .contracts import NO_RETRY, JobDefinition, JobOutcome, Step, StepRunner
from .jobs import failure_of, jobs

logger = get_logger(__name__)

_ADMISSION_WAIT_MAX_S = 30.0


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

    def update(self, **fields: Any) -> None:
        jobs.update(self.job_id, **fields)

    def finish(self, outcome: JobOutcome, **fields: Any) -> None:
        jobs.finish(self.job_id, outcome, **fields)

    def cancelled(self) -> bool:
        return _withdrawn(self.job_id)

    def nudge(self, source: JobKind) -> None:
        from .submission import nudge

        nudge(source)


def _withdrawn(job_id: str, attempt: int | None = None) -> bool:
    """Whether this execution is cancelled, missing or superseded."""
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        return (
            row is None
            or row.state is JobState.CANCELLED
            or attempt is not None
            and (row.attempts != attempt or row.state is JobState.QUEUED)
        )


def _begin(job_id: str, attempt: int) -> dict[str, str] | None:
    """Claim the attempt: ``None`` when it is superseded or the Job is settled.

    The claim is the checkpoint a durable engine replays, so it returns plain
    strings; ``execute_job`` parses them back into the Job's typed identity.
    """
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        if row is None or row.state not in ACTIVE_JOB_STATES:
            return None
        if row.attempts > attempt:
            return None
        now = utcnow()
        row.attempts = max(row.attempts, attempt)
        row.state = JobState.RUNNING
        row.started_at = row.started_at or now
        row.updated_at = now
        session.add(row)
        session.commit()
        return {
            "kind": row.kind.value,
            "subject_key": row.subject_key,
            "priority": row.priority.value,
        }


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

    if _withdrawn(context.job_id):
        return StepOutcome.CANCELLED.value
    if mutating and not begin_mutating_operation():
        return StepOutcome.DEFERRED.value
    started = time.monotonic()
    try:
        with cancellation_scope(lambda: _withdrawn(context.job_id, context.attempt)):
            step.fn(context)
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


def _settle(
    job_id: str, definition: JobDefinition, subject_key: str, error: str | None
) -> None:
    """Record the attempt's outcome and nudge whoever waits on completion.

    A step may already have finished the Job (a recorded failure, a partial
    completion); the first terminal write wins, so these are no-ops then.
    """
    if error is None:
        jobs.finish(job_id, JobOutcome.COMPLETED)
    else:
        jobs.finish(job_id, JobOutcome.FAILED, error=error, retryable=True)
    status = jobs.get(job_id)
    if status is not None and status.state is JobState.FAILED:
        if status.error is None:
            raise RuntimeError(f"failed_job_without_error:{job_id}")
        with get_session_factory().scoped_session() as session:
            try:
                definition.on_failure(session, subject_key, status.error)
                session.commit()
            except Exception:  # noqa: BLE001 - the Job already records the failure
                session.rollback()
                logger.exception(
                    "job failure hook failed",
                    extra={"job_id": job_id, "kind": definition.name.value},
                )
    if status is not None and status.terminal:
        with get_session_factory().scoped_session() as session:
            try:
                definition.on_settled(session, subject_key)
                session.commit()
            except Exception:  # noqa: BLE001 - settlement is durable; startup repairs cleanup
                session.rollback()
                logger.exception(
                    "job settlement cleanup failed", extra={"job_id": job_id}
                )
    from .submission import nudge

    for source in dict.fromkeys((definition.name, *definition.completion_nudges)):
        nudge(source)


def execute_job(job_id: str, attempt: int, runner: StepRunner) -> None:
    """Run one attempt of ``job_id`` to its recorded outcome."""
    begun = runner.run(
        f"work.begin#{attempt}", lambda: _begin(job_id, attempt), NO_RETRY
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
        execution_id=f"{job_id}:{attempt}",
        attempt=attempt,
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
    runner.run(
        "work.settle",
        lambda: _settle(job_id, definition, subject_key, error),
        NO_RETRY,
    )
