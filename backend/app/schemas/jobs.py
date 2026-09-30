"""Public shapes of background work: Jobs, Artifact derivatives and lanes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import ProcessRole
from app.db.models.types import (
    DerivativeKind,
    JobKind,
    JobState,
    LaneName,
    WorkPriority,
)

JobStage = Literal[
    "resolving",
    "downloading",
    "inspecting",
    "extracting",
    "hashing",
    "ingesting",
    "snapshotting",
    "archiving",
    "verifying",
    "publishing",
    "finalizing",
    "completed",
]
JobCompletion = Literal["complete", "partial"]


class DerivativeStatus(StrEnum):
    """A derivative as a reader sees it: ``pending`` (no attempt at the current
    recipe yet, or regenerated since) and every stored ``DerivativeState``."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobFailedItem(BaseModel):
    name: str
    reason: str
    retryable: bool = False


class JobStagingSummary(BaseModel):
    retained_bytes: int = Field(ge=0)
    lease_count: int = Field(ge=1)
    earliest_expiry: datetime
    discard_available: bool


class JobStatus(BaseModel):
    """One background Job: what it is doing, and what it did.

    ``kind`` is the Job Definition, one of a closed set a client can switch
    on. Counts and ``failed_items`` are filled by definitions that process
    several items (an archive, a collection); a single-item job reports
    ``processed``/``total`` of 1. Nothing here is ever read back as the job's
    input.
    """

    job_id: str
    staging: JobStagingSummary | None
    kind: JobKind
    owner_user_id: Optional[int] = Field(default=None, exclude=True)
    state: JobState
    priority: WorkPriority
    attempts: int
    resubmits: int
    model_id: Optional[int] = None
    file_id: Optional[int] = None
    error: Optional[str] = None
    retryable: bool = False
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    committed_at: Optional[datetime] = None
    step: Optional[int] = None
    total_steps: Optional[int] = None
    label: Optional[str] = None
    progress: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    stage: Optional[JobStage] = None
    current_item: Optional[str] = None
    processed: int = 0
    total: Optional[int] = None
    succeeded: int = 0
    deduplicated: int = 0
    skipped: int = 0
    failed: int = 0
    completion: Optional[JobCompletion] = None
    failed_items: list[JobFailedItem] = Field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}


class JobAccepted(BaseModel):
    """Returned by every endpoint that accepts background work."""

    job_id: str
    state: JobState = JobState.QUEUED
    message: str = "queued"


class DerivativeRead(BaseModel):
    """One derivative kind of one Artifact at the current recipe.

    ``pending`` means no attempt exists yet at the current recipe; every value
    the derivative would supply is unknown until it is ``ready``.
    """

    kind: DerivativeKind
    recipe_version: int
    state: DerivativeStatus
    attempts: int = 0
    failure_reason: Optional[str] = None
    updated_at: Optional[datetime] = None
    retryable: bool = False


class LaneRead(BaseModel):
    name: LaneName
    concurrency: int
    default_concurrency: int
    overridden: bool
    scope: Literal["worker", "global"]
    partitioned: bool
    queued: int
    running: int


class DefinitionRead(BaseModel):
    name: JobKind
    label: str
    lane: LaneName
    queued: int
    running: int
    interrupted: int
    failed: int
    completed: int
    derivative_kinds: list[DerivativeKind]
    next_due_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None


class ExecutorRead(BaseModel):
    executor_id: str
    role: ProcessRole
    hostname: str
    app_version: str
    lanes: list[LaneName]
    started_at: datetime
    heartbeat_at: datetime
    stale: bool


class WorkOverview(BaseModel):
    lanes: list[LaneRead]
    definitions: list[DefinitionRead]
    executors: list[ExecutorRead]
    failed_jobs: list[JobStatus]
    failed_derivatives: int


class LaneUpdate(BaseModel):
    concurrency: Optional[int] = Field(default=None, ge=1, le=64)


class RegenerateMode(StrEnum):
    """``missing`` fills gaps; ``all`` re-derives every Artifact of the kind."""

    MISSING = "missing"
    ALL = "all"


class DerivativeRegenerate(BaseModel):
    mode: RegenerateMode = RegenerateMode.MISSING


class CancelQueued(BaseModel):
    definition: JobKind
