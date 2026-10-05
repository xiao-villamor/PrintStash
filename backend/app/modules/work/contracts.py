"""The engine-agnostic vocabulary of background work.

Nothing here knows which engine runs the work. Job definitions, their steps,
lanes and sources are declared with these types by the module that owns the
work; an engine adapter (``app.runtime.engine``) maps them onto its own
primitives. A capability outside this contract is not available to
definitions, which is what keeps the engine an implementation detail.

Two guarantees are named explicitly because each has exactly one owner:

``execution_id``
    Exactly once per key. Submitting an id the engine already knows returns
    that execution instead of starting another.
``dedupe_key``
    At most one *active* execution per key. A second submission while one is
    queued, delayed or running is rejected rather than queued behind it.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from app.db.models.types import JobKind, JobState, LaneName, WorkPriority

if TYPE_CHECKING:
    from sqlmodel import Session

__all__ = [
    "ActiveExecution",
    "Deduplicated",
    "DiscoveryBudget",
    "EngineEvidence",
    "EngineStatus",
    "ExecutionKind",
    "JobContext",
    "JobDefinition",
    "JobEngine",
    "JobOutcome",
    "JobSubmission",
    "Lane",
    "LaneDepth",
    "LaneOrder",
    "Partitioned",
    "PassSubmission",
    "RetryPolicy",
    "PrioritizedWorkSource",
    "SkipReason",
    "Step",
    "StepRunner",
    "SubmitOutcome",
    "Submission",
    "WorkItem",
    "WorkPriority",
    "WorkSource",
    "narrower_priority",
]


class JobOutcome(StrEnum):
    """How a Job ended: the terminal subset of ``JobState``, and nothing else."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def state(self) -> JobState:
        return JobState(self.value)


def narrower_priority(parent: WorkPriority, requested: WorkPriority) -> WorkPriority:
    """A child may lower its parent's priority, never raise it."""
    if parent is WorkPriority.BACKFILL:
        return WorkPriority.BACKFILL
    return requested


@dataclass(frozen=True)
class RetryPolicy:
    """How a step reacts to an exception before its Job gives up.

    ``max_attempts`` counts the first try, so 1 means "never retry". Only
    exceptions matching ``retry_on`` are retried; anything else fails the step
    immediately, because retrying a deterministic failure only repeats it.
    """

    max_attempts: int = 1
    interval_seconds: float = 1.0
    backoff_rate: float = 2.0
    retry_on: tuple[type[BaseException], ...] = ()

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("retry_max_attempts_below_one")
        if self.interval_seconds < 0 or self.backoff_rate < 1:
            raise ValueError("retry_interval_invalid")

    def should_retry(self, error: BaseException) -> bool:
        return bool(self.retry_on) and isinstance(error, self.retry_on)

    def delay_before(self, attempt: int) -> float:
        """Seconds to wait before retry number ``attempt`` (1-based)."""
        return self.interval_seconds * self.backoff_rate ** max(attempt - 1, 0)


NO_RETRY = RetryPolicy()


class LaneOrder(StrEnum):
    PRIORITY = "priority"
    FIFO = "fifo"


@dataclass(frozen=True)
class Lane:
    """A concurrency class of work.

    ``scope`` says whether ``concurrency`` bounds each worker process or the
    whole deployment. A partitioned lane bounds each partition (one printer, one
    notification channel) separately; engines cannot deduplicate on it, so its
    definitions rely on ``execution_id`` idempotency and the active-subject
    claim instead.
    """

    name: LaneName
    concurrency: int
    scope: Literal["worker", "global"] = "worker"
    partitioned: bool = False
    rate_limit: tuple[int, float] | None = None
    queue_order: LaneOrder = LaneOrder.PRIORITY

    def __post_init__(self) -> None:
        if not isinstance(self.queue_order, LaneOrder):
            raise TypeError("lane_queue_order_requires_enum")
        if self.concurrency < 1:
            raise ValueError("lane_concurrency_below_one")

    @property
    def headroom(self) -> int:
        """Pending slots: FIFO reserves concurrency interactive slots plus one backfill."""
        if self.queue_order is LaneOrder.FIFO:
            return self.concurrency + 1
        from app.core.config import settings

        return self.concurrency * settings.jobs_lane_headroom_factor


@dataclass(frozen=True)
class JobExecution:
    """The immutable authority carried by one engine execution."""

    job_id: str
    attempt: int
    execution_epoch: str


class JobContext(Protocol):
    """What a running step may ask of the engine. Nothing else is available."""

    job_id: str
    definition: JobKind
    subject_key: str
    priority: WorkPriority
    execution_id: str
    attempt: int
    execution_epoch: str

    def update(self, **fields: Any) -> None:
        """Merge display-safe progress, counts or result into the Job."""
        ...

    def finish(self, outcome: JobOutcome, **fields: Any) -> None:
        """Record the Job's terminal outcome from inside a step."""
        ...

    def cancelled(self) -> bool: ...

    def nudge(self, source: JobKind) -> None:
        """Ask the reconciler to run one source's pass soon."""
        ...


StepFn = Callable[[JobContext], Any]


@dataclass(frozen=True)
class Step:
    """An idempotent unit of a job. It re-reads domain state on every attempt."""

    name: str
    fn: StepFn
    retry: RetryPolicy = NO_RETRY


class SkipReason(StrEnum):
    """Why a source declined an occurrence it reports."""

    DERIVATIVE_GROUP_DISABLED = "derivative_group_disabled"
    PREVIOUS_STILL_RUNNING = "previous_still_running"


@dataclass(frozen=True)
class WorkItem:
    """One subject a source reports as needing work, as the source renders it."""

    subject_key: str
    priority: WorkPriority = WorkPriority.BACKFILL
    owner_user_id: int | None = None
    # A schedule occurrence the source consciously declines is recorded as a
    # cancelled Job carrying this reason: a skip is a verdict, not an absence.
    skip: SkipReason | None = None
    # A schedule occurrence the reconciler records once its Job exists.
    occurrence_at: datetime | None = None


class WorkSource(Protocol):
    """Computes pending work for one definition from domain state.

    ``pending`` must be one bounded, indexed query. It never scans a whole
    table: a source over Artifacts uses an anti-join on its derivative rows.
    ``next_due`` lets a schedule-shaped source ask for a delayed nudge at its
    earliest next occurrence, so wall-clock work does not wait for a tick.
    """

    def pending(
        self, session: Session, *, now: datetime, limit: int
    ) -> Sequence[WorkItem]: ...

    def next_due(self, session: Session, *, now: datetime) -> datetime | None: ...


@dataclass(frozen=True)
class DiscoveryBudget:
    """Finite pending slots, independently reserved for each priority."""

    total: int
    interactive: int
    backfill: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.total, self.interactive, self.backfill)
        ):
            raise ValueError("invalid_discovery_budget")
        if self.total > self.interactive + self.backfill:
            raise ValueError("discovery_total_exceeds_priority_caps")


@runtime_checkable
class PrioritizedWorkSource(WorkSource, Protocol):
    """A source that respects separate finite pending quotas."""

    def pending_prioritized(
        self, session: Session, *, now: datetime, budget: DiscoveryBudget
    ) -> Sequence[WorkItem]: ...


FailureHook = Callable[["Session", str, str], None]
CancelHook = Callable[["Session", str], None]
RetryHook = Callable[["Session", str], bool]


def _no_hook(_session: Session, *_args: str) -> None:
    return None


def _allow_admission(_session: Session) -> SkipReason | None:
    return None


def _no_retry_hook(_session: Session, _subject_key: str) -> bool:
    return True


@dataclass(frozen=True)
class JobDefinition:
    """A named unit of background work and everything the reconciler needs.

    ``cancel`` withdraws intent from the subject so the source stops reporting
    it; a cancel that only stopped the engine would be resurrected by the next
    pass. ``on_failure`` marks the subject failed for the same reason, and
    ``retry`` returns it to pending (``False`` when the subject is gone).
    ``source`` is ``None`` for request-originated work, whose Job row is
    created by the request and is its own pending marker.
    """

    name: JobKind
    lane: LaneName
    steps: tuple[Step, ...]
    # What administrators see for this kind of work (Settings → Background work).
    label: str
    source: WorkSource | None = None
    cancel: CancelHook = _no_hook
    on_failure: FailureHook = _no_hook
    on_settled: CancelHook = _no_hook
    retry: RetryHook = _no_retry_hook
    # Sources this definition's completions should nudge (always its own).
    completion_nudges: tuple[JobKind, ...] = ()
    # On a partitioned lane: the partition a subject belongs to (a printer id,
    # a notification channel). Required there and refused elsewhere.
    partition: Callable[[str], str] | None = None
    # Whether each step is admitted as a write-capable operation (drained by a
    # restore, deferred while one holds the fence). Only work that itself takes
    # the restore fence (a vault migration's cutover) opts out and gates itself.
    mutating: bool = True
    # Whether the source's one subject stands for all its work (a drain: fleet
    # dispatch, similarity, projection, indexing). A drain's Job finishes and
    # its subject comes straight back whenever new work arrives, so the
    # resubmit cooldown, which holds back a subject that keeps returning, would
    # stall real work; a drain that makes no progress parks itself instead
    # (``work.sources.mark_idle``).
    drain: bool = False
    # Whether a Job still in flight in a restored database is owed afterwards.
    # Most work is (an import, a derivative), so the reconciler reruns it. A
    # backup request is not: the restored database is its own snapshot, taken
    # while that very request ran, and a restore supersedes it.
    survives_restore: bool = True
    # Evaluated from current application state, never captured in engine input.
    admission: Callable[["Session"], SkipReason | None] = _allow_admission

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("job_definition_without_steps")
        if len({step.name for step in self.steps}) != len(self.steps):
            raise ValueError("job_definition_duplicate_step")
        if not self.label.strip():
            raise ValueError("job_definition_without_label")


class EngineStatus(StrEnum):
    """The engine's own view of one execution, reduced to what decisions need."""

    QUEUED = "queued"
    DELAYED = "delayed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class EngineEvidence:
    """What the engine knows about one execution it has a record of.

    ``app_version`` and ``executor_id`` are what the engine recorded, which a
    durable engine may not have for an execution no process has claimed yet.
    """

    status: EngineStatus
    app_version: str | None = None
    executor_id: str | None = None


class ExecutionKind(StrEnum):
    """The two things an engine executes: a Job attempt or a reconcile pass."""

    JOB = "job"
    PASS = "pass"


@dataclass(frozen=True)
class ActiveExecution:
    execution_id: str
    kind: ExecutionKind
    status: EngineStatus
    app_version: str | None
    executor_id: str | None


@dataclass(frozen=True)
class LaneDepth:
    queued: int
    running: int


class SubmitOutcome(StrEnum):
    ACCEPTED = "accepted"
    EXISTING = "existing"
    DEDUPLICATED = "deduplicated"


@dataclass(frozen=True)
class Deduplicated:
    """A Job on an ordinary lane: at most one active execution per ``key``."""

    key: str


@dataclass(frozen=True)
class Partitioned:
    """A Job on a partitioned lane, bounded per ``key`` (a printer, a channel).

    Engines cannot deduplicate a partitioned queue; the active-subject claim on
    the Job row keeps it single-flight instead.
    """

    key: str


@dataclass(frozen=True)
class JobSubmission:
    """One attempt of one Job, fully resolved by the work layer."""

    execution_id: str
    job_id: str
    definition: JobKind
    subject_key: str
    lane: LaneName
    priority: WorkPriority
    attempt: int
    execution_epoch: str
    routing: Deduplicated | Partitioned
    delay_seconds: float | None = None


@dataclass(frozen=True)
class PassSubmission:
    """One reconcile pass over one definition's source, on the reconcile lane."""

    execution_id: str
    source: JobKind
    priority: WorkPriority
    delay_seconds: float | None = None

    @property
    def lane(self) -> LaneName:
        return LaneName.RECONCILE


Submission = JobSubmission | PassSubmission


class StepRunner(Protocol):
    """How an engine runs one checkpointed unit inside an execution.

    Everything the execution body decides from changing state (admission,
    cancellation) goes through ``run`` too, so a durable engine replays the
    recorded answer and the body takes the same path on recovery.
    """

    def run(self, name: str, fn: Callable[[], Any], retry: RetryPolicy) -> Any: ...

    def sleep(self, seconds: float) -> None:
        """A durable wait between steps; an engine resumes it after a restart."""
        ...

    def is_cancellation(self, error: BaseException) -> bool:
        """Whether ``error`` is the engine unwinding a cancelled execution."""
        ...


class JobEngine(abc.ABC):
    """An execution engine. Implementations: DBOS and the inline test engine.

    An engine runs two kinds of execution: a Job attempt, whose body is
    ``app.modules.work.runner.execute_job``, and a reconcile pass, whose body
    is ``app.modules.work.reconciler.execute_pass``. Both receive only strings
    and integers; neither carries domain input.
    """

    @abc.abstractmethod
    def launch(self, *, listen_lanes: Sequence[LaneName] | None) -> None:
        """Start executing. ``listen_lanes=None`` listens to every lane."""

    @abc.abstractmethod
    def shutdown(self) -> None: ...

    @abc.abstractmethod
    def submit(self, submission: Submission) -> SubmitOutcome: ...

    @abc.abstractmethod
    def cancel(self, execution_id: str) -> None: ...

    @abc.abstractmethod
    def evidence(self, execution_ids: Sequence[str]) -> dict[str, EngineEvidence]:
        """What the engine knows about each id; an id it has no record of is left out."""

    @abc.abstractmethod
    def active(self) -> list[ActiveExecution]: ...

    @abc.abstractmethod
    def lane_depth(self, lane: LaneName) -> LaneDepth: ...

    @abc.abstractmethod
    def set_lane_concurrency(self, lane: LaneName, concurrency: int) -> None: ...

    @abc.abstractmethod
    def foreign_version_executions(self) -> list[str]:
        """Active executions recorded under another application version."""

    @abc.abstractmethod
    def prune_history(self, *, older_than: datetime) -> int: ...

    @abc.abstractmethod
    def reset(self) -> None:
        """Discard all engine state. The application database stays authoritative."""

    @property
    @abc.abstractmethod
    def executor_id(self) -> str: ...
