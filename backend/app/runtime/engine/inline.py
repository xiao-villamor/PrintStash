"""A deterministic in-process engine with the same guarantees as the durable one.

It keeps every execution in memory and runs nothing until ``drain`` is called,
so a test decides exactly when work happens and asserts on what it did. It
honours the port contract: ``execution_id`` idempotency, ``dedupe_key``
single-flight, priority order, delays, lane concurrency accounting, step
retries and cancellation. Sleeps and delays advance a virtual clock instead of
blocking.
"""

from __future__ import annotations

import asyncio
import contextvars
import itertools
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.db.models import LaneName, WorkPriority
from app.modules.work.catalog import WorkCatalog
from app.modules.work.contracts import (
    ActiveExecution,
    Deduplicated,
    EngineEvidence,
    EngineStatus,
    ExecutionKind,
    JobEngine,
    JobSubmission,
    LaneDepth,
    LaneOrder,
    PassSubmission,
    RetryPolicy,
    Submission,
    SubmitOutcome,
)
from app.modules.work.submission import PRIORITY_RANK

_ACTIVE = {EngineStatus.QUEUED, EngineStatus.DELAYED, EngineStatus.RUNNING}
_MAX_SLEEPS = 1000


class InlineCancelled(BaseException):
    """Unwinds an execution whose cancellation the engine observed."""


def _execute(submission: Submission, runner: _Runner) -> None:
    if isinstance(submission, PassSubmission):
        from app.modules.work.reconciler import execute_pass

        execute_pass(submission.source, runner)
    else:
        from app.modules.work.runner import execute_job

        execute_job(
            submission.job_id,
            submission.attempt,
            runner,
            execution_epoch=submission.execution_epoch,
        )


def _dedupe_key(submission: Submission) -> str | None:
    if isinstance(submission, JobSubmission) and isinstance(
        submission.routing, Deduplicated
    ):
        return submission.routing.key
    return None


def _off_loop(fn: Callable[[], None]) -> None:
    """Run ``fn`` where the durable engine runs steps: never on an event loop.

    DBOS executes steps on its own worker threads, so a step may drive its own
    loop (an SFTP listing does). A test that drains from inside an async flow
    would otherwise run the step on the test's loop thread, where that fails.
    The caller's context goes along, so its settings and session factory hold.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        fn()
        return
    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="inline-step") as pool:
        pool.submit(context.run, fn).result()


@dataclass
class _Execution:
    submission: Submission
    sequence: int
    status: EngineStatus
    app_version: str
    executor_id: str
    ready_at: float = 0.0
    error: BaseException | None = None


class _Runner:
    def __init__(self, engine: InlineJobEngine, execution: _Execution) -> None:
        self.engine = engine
        self.execution = execution
        self.sleeps = 0

    def run(self, name: str, fn: Callable[[], Any], retry: RetryPolicy) -> Any:
        if self.execution.status is EngineStatus.CANCELLED:
            raise InlineCancelled(self.execution.submission.execution_id)
        attempt = 1
        while True:
            try:
                return fn()
            except Exception as exc:
                if attempt >= retry.max_attempts or not retry.should_retry(exc):
                    raise
                self.engine.clock += retry.delay_before(attempt)
                self.engine.step_retries.append((name, attempt, exc))
                attempt += 1

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        if self.sleeps > _MAX_SLEEPS:
            raise RuntimeError("inline_engine_sleep_limit")
        self.engine.clock += seconds

    def is_cancellation(self, error: BaseException) -> bool:
        return isinstance(error, InlineCancelled)


class InlineJobEngine(JobEngine):
    """Runs executions synchronously, one at a time, when ``drain`` is called."""

    def __init__(
        self,
        catalog: WorkCatalog,
        *,
        executor_id: str = "inline",
        app_version: str | None = None,
    ) -> None:
        self.catalog = catalog
        self._executor_id = executor_id
        self.app_version = app_version or settings.app_version
        self.executions: dict[str, _Execution] = {}
        self.concurrency = {
            name: lane.concurrency for name, lane in catalog.lanes.items()
        }
        self.clock = 0.0
        self.step_retries: list[tuple[str, int, BaseException]] = []
        self.launched = False
        self.listen: set[LaneName] | None = None
        self._sequence = itertools.count()

    @property
    def executor_id(self) -> str:
        return self._executor_id

    def launch(self, *, listen_lanes: Sequence[LaneName] | None) -> None:
        self.launched = True
        self.listen = set(listen_lanes) if listen_lanes is not None else None

    def shutdown(self) -> None:
        self.launched = False

    def submit(self, submission: Submission) -> SubmitOutcome:
        if submission.execution_id in self.executions:
            return SubmitOutcome.EXISTING
        key = _dedupe_key(submission)
        if key is not None and any(
            _dedupe_key(other.submission) == key
            and other.submission.lane == submission.lane
            and other.status in _ACTIVE
            for other in self.executions.values()
        ):
            return SubmitOutcome.DEDUPLICATED
        delayed = bool(submission.delay_seconds)
        self.executions[submission.execution_id] = _Execution(
            submission=submission,
            sequence=next(self._sequence),
            status=EngineStatus.DELAYED if delayed else EngineStatus.QUEUED,
            app_version=self.app_version,
            executor_id=self._executor_id,
            ready_at=self.clock + (submission.delay_seconds or 0.0),
        )
        return SubmitOutcome.ACCEPTED

    def cancel(self, execution_id: str) -> None:
        execution = self.executions.get(execution_id)
        if execution is not None and execution.status in _ACTIVE:
            execution.status = EngineStatus.CANCELLED

    def evidence(self, execution_ids: Sequence[str]) -> dict[str, EngineEvidence]:
        return {
            execution_id: EngineEvidence(
                execution.status, execution.app_version, execution.executor_id
            )
            for execution_id in execution_ids
            if (execution := self.executions.get(execution_id)) is not None
        }

    def active(self) -> list[ActiveExecution]:
        return [
            ActiveExecution(
                execution_id=execution_id,
                kind=ExecutionKind.PASS
                if isinstance(execution.submission, PassSubmission)
                else ExecutionKind.JOB,
                status=execution.status,
                app_version=execution.app_version,
                executor_id=execution.executor_id,
            )
            for execution_id, execution in self.executions.items()
            if execution.status in _ACTIVE
        ]

    def lane_depth(self, lane: LaneName) -> LaneDepth:
        queued = running = 0
        for execution in self.executions.values():
            if execution.submission.lane != lane:
                continue
            if execution.status in {EngineStatus.QUEUED, EngineStatus.DELAYED}:
                queued += 1
            elif execution.status is EngineStatus.RUNNING:
                running += 1
        return LaneDepth(queued=queued, running=running)

    def set_lane_concurrency(self, lane: LaneName, concurrency: int) -> None:
        self.concurrency[lane] = concurrency

    def foreign_version_executions(self) -> list[str]:
        return [
            execution_id
            for execution_id, execution in self.executions.items()
            if execution.status in _ACTIVE and execution.app_version != self.app_version
        ]

    def prune_history(self, *, older_than: datetime) -> int:
        del older_than
        settled = [
            execution_id
            for execution_id, execution in self.executions.items()
            if execution.status not in _ACTIVE
        ]
        for execution_id in settled:
            del self.executions[execution_id]
        return len(settled)

    def reset(self) -> None:
        self.executions.clear()

    # -- test controls -------------------------------------------------------

    def advance(self, seconds: float) -> None:
        self.clock += seconds

    def _runnable(self) -> list[_Execution]:
        ready: list[_Execution] = []
        for execution in self.executions.values():
            if (
                execution.status is EngineStatus.DELAYED
                and execution.ready_at <= self.clock
            ):
                execution.status = EngineStatus.QUEUED
            if execution.status is not EngineStatus.QUEUED:
                continue
            if self.listen is not None and execution.submission.lane not in self.listen:
                continue
            ready.append(execution)
        return sorted(
            ready,
            key=lambda item: (
                (
                    PRIORITY_RANK[WorkPriority.INTERACTIVE]
                    if self.catalog.lanes[item.submission.lane].queue_order
                    is LaneOrder.FIFO
                    else PRIORITY_RANK[item.submission.priority]
                ),
                item.sequence,
            ),
        )

    def run_one(self) -> _Execution | None:
        ready = self._runnable()
        if not ready:
            return None
        execution = ready[0]
        execution.status = EngineStatus.RUNNING
        runner = _Runner(self, execution)
        submission = execution.submission
        try:
            _off_loop(lambda: _execute(submission, runner))
        except InlineCancelled:
            execution.status = EngineStatus.CANCELLED
            return execution
        except BaseException as exc:
            execution.status = EngineStatus.FAILED
            execution.error = exc
            raise
        if execution.status is EngineStatus.RUNNING:
            execution.status = EngineStatus.SUCCEEDED
        return execution

    def drain(self, *, max_executions: int = 10_000) -> int:
        """Run executions until none is runnable; returns how many ran."""
        ran = 0
        while ran < max_executions:
            if self.run_one() is None:
                return ran
            ran += 1
        raise RuntimeError("inline_engine_did_not_quiesce")

    def interrupt_everything(self) -> None:
        """Simulate a crash: every active execution vanishes from the engine."""
        for execution_id in [
            execution_id
            for execution_id, execution in self.executions.items()
            if execution.status in _ACTIVE
        ]:
            del self.executions[execution_id]
