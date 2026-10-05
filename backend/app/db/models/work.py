"""Background work: Jobs, reconciler cursors, fences, executors and lane overrides.

The application database records *intent*; the execution engine only executes.
A ``Job`` is the user-visible record of work on one subject, and it is the only
job state that survives a restore or an engine swap. Its input is never
serialized: every attempt rebuilds what it needs from the subject row.
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, Text, text
from sqlmodel import Field

from app.core.config import ProcessRole
from app.core.time import utcnow
from app.db.enum_columns import EnumText, enum_check

from .base import SQLModel
from .types import JobKind, JobState, LaneName, WorkPriority

# Non-terminal states. At most one Job per (definition, subject) may be in one of
# these at a time; the partial unique index below is the claim that enforces it,
# so two reconciler passes racing on the same subject cannot both create work.
ACTIVE_JOB_STATES = (JobState.QUEUED, JobState.RUNNING, JobState.INTERRUPTED)


class Job(SQLModel, table=True):
    """One unit of background work on one subject, across its attempts.

    Staging leases, Pending Imports and upload sessions point at it through
    ``job_id``: the Job, not an engine execution, owns their staged bytes.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_jobs_active_subject",
            "kind",
            "subject_key",
            unique=True,
            sqlite_where=text("state IN ('queued', 'running', 'interrupted')"),
            postgresql_where=text("state IN ('queued', 'running', 'interrupted')"),
        ),
        Index(
            "ix_jobs_owner_state_updated",
            "owner_user_id",
            "state",
            "updated_at",
        ),
        enum_check("kind", JobKind),
        enum_check("priority", WorkPriority),
        enum_check("state", JobState),
        CheckConstraint("length(execution_epoch) > 0", name="execution_epoch_present"),
        CheckConstraint(
            "submitted_epoch IS NULL OR length(submitted_epoch) > 0",
            name="submitted_epoch_present",
        ),
        CheckConstraint(
            "backfill_admission_epoch IS NULL OR length(backfill_admission_epoch) > 0",
            name="backfill_admission_epoch_present",
        ),
    )

    id: str = Field(primary_key=True, max_length=64)
    # The Job Definition this Job is an instance of.
    kind: JobKind = Field(
        sa_column=Column(EnumText(JobKind), nullable=False, index=True)
    )
    subject_key: str = Field(max_length=255, index=True)
    owner_user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    priority: WorkPriority = Field(
        default=WorkPriority.INTERACTIVE,
        sa_column=Column(EnumText(WorkPriority), nullable=False),
    )
    state: JobState = Field(
        default=JobState.QUEUED,
        sa_column=Column(EnumText(JobState), nullable=False, index=True),
    )
    # Display-safe progress, counts and result. Never replayed as input.
    status_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    # Retry invalidates callbacks without counting an execution that never ran.
    execution_epoch: str = Field(default_factory=lambda: uuid4().hex, max_length=64)
    # Last epoch accepted by the engine; None means never submitted.
    submitted_epoch: Optional[str] = Field(default=None, max_length=64)
    # Pre-engine backfill reservation; only equality with execution_epoch grants
    # authority. Retry may retain a stale marker without retaining its slot.
    backfill_admission_epoch: Optional[str] = Field(default=None, max_length=64)
    # Executions submitted so far, across explicit retries.
    attempts: int = Field(default=0)
    # Consecutive interrupted executions; bounded by ``jobs_max_resubmits``.
    resubmits: int = Field(default=0)
    app_version: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = Field(default=None, index=True)


class ReconcileCursor(SQLModel, table=True):
    """Per-definition reconciler bookkeeping: the pass claim and the dirty mark.

    Every nudge stamps ``nudged_at`` *before* deciding whether a pass needs
    enqueueing. A pass claims ``holder`` for its duration and releases it only
    while ``nudged_at`` is not newer than its own start; otherwise it runs again.
    A nudge can therefore never be lost between a pass's last read and its
    release, which engine-level deduplication alone cannot guarantee (it treats
    a finishing pass as still active and would drop the nudge).
    """

    __tablename__ = "reconcile_cursors"
    __table_args__ = (
        enum_check("source", JobKind),
        enum_check("pass_priority", WorkPriority),
        enum_check("discovery_next_priority", WorkPriority),
        CheckConstraint(
            "(scan_recent_at IS NULL) = (scan_recent_file_id IS NULL)",
            name="recent_keyset_complete",
        ),
        CheckConstraint(
            "scan_recent_file_id IS NULL OR scan_recent_file_id > 0",
            name="recent_keyset_positive_id",
        ),
        # A queued pass always records its priority; a held claim, its expiry;
        # a parked drain, both ends of its window.
        CheckConstraint(
            "(pass_queued_at IS NULL) = (pass_priority IS NULL)",
            name="pass_queued_with_priority",
        ),
        CheckConstraint(
            "(holder IS NULL) = (holder_expires_at IS NULL)",
            name="holder_with_expiry",
        ),
        CheckConstraint(
            "(idle_since IS NULL) = (idle_until IS NULL)",
            name="idle_window_complete",
        ),
    )

    # The definition whose source this cursor paces.
    source: JobKind = Field(
        sa_column=Column(EnumText(JobKind), primary_key=True, nullable=False)
    )
    nudged_at: Optional[datetime] = None
    # A pass waiting in the engine, and the priority it was queued at.
    pass_queued_at: Optional[datetime] = None
    pass_priority: Optional[WorkPriority] = Field(
        default=None, sa_column=Column(EnumText(WorkPriority), nullable=True)
    )
    holder: Optional[str] = Field(default=None, max_length=128)
    holder_expires_at: Optional[datetime] = None
    last_pass_started_at: Optional[datetime] = None
    last_pass_finished_at: Optional[datetime] = None
    last_pass_submitted: int = Field(default=0)
    last_pass_deferred: int = Field(default=0)
    # For schedule sources: the newest occurrence already turned into a Job.
    # Kept here rather than derived from Job rows, which retention prunes.
    last_occurrence_at: Optional[datetime] = None
    # A drain source parked after a pass that could make no progress.
    idle_since: Optional[datetime] = None
    idle_until: Optional[datetime] = None
    # A derivative source's scan: the newest Artifact id it has offered, and
    # where its rotating window over older Artifacts stands.
    scan_high_water: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    scan_position: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    # A one-item discovery pass alternates tiers across processes and restarts.
    discovery_next_priority: WorkPriority = Field(
        default=WorkPriority.BACKFILL,
        sa_column=Column(
            EnumText(WorkPriority), nullable=False, server_default="backfill"
        ),
    )
    # Indexed keyset for recent uploads or viewer demand. These are scan
    # positions, not foreign keys: deletion must not invalidate pagination.
    scan_recent_at: datetime | None = None
    scan_recent_file_id: int | None = None


class WorkFence(SQLModel, table=True):
    """A database lease that stops new steps from starting while it is held.

    Replaces the process-local maintenance counters, which cannot protect
    anything once a worker runs in another process. A holder heartbeats; a
    crashed holder's fence expires on its own after ``expires_at``.
    """

    __tablename__ = "work_fences"

    name: str = Field(primary_key=True, max_length=64)
    holder: str = Field(max_length=128)
    reason: str = Field(max_length=64)
    acquired_at: datetime = Field(default_factory=utcnow)
    heartbeat_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(index=True)


class WorkExecutor(SQLModel, table=True):
    """A process that runs jobs, with its role and last heartbeat."""

    __tablename__ = "work_executors"
    __table_args__ = (enum_check("role", ProcessRole),)

    executor_id: str = Field(primary_key=True, max_length=128)
    role: ProcessRole = Field(sa_column=Column(EnumText(ProcessRole), nullable=False))
    hostname: str = Field(max_length=255)
    pid: int
    app_version: str = Field(max_length=64)
    # The lanes it executes, comma-separated; empty when it runs none.
    lanes: str = Field(default="", sa_column=Column(Text, nullable=False))
    # Write-capable operations in flight at the last heartbeat. A restore
    # drains every live executor by waiting for each to report zero after the
    # restore fence was taken.
    active_mutations: int = Field(default=0)
    started_at: datetime = Field(default_factory=utcnow)
    heartbeat_at: datetime = Field(default_factory=utcnow, index=True)


class WorkLaneOverride(SQLModel, table=True):
    """An administrator's runtime concurrency for one lane.

    The application database owns it rather than the engine, whose state is
    disposable: it is reapplied to the engine on every launch.
    """

    __tablename__ = "work_lane_overrides"
    __table_args__ = (enum_check("lane", LaneName),)

    lane: LaneName = Field(
        sa_column=Column(EnumText(LaneName), primary_key=True, nullable=False)
    )
    concurrency: int = Field(sa_column=Column(Integer, nullable=False))
    updated_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
    updated_at: datetime = Field(default_factory=utcnow)
