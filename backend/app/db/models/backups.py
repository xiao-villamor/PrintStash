"""Backup runs, destination results, retry attempts and garbage-collection runs."""

from typing import Optional

from pydantic import NaiveDatetime
from sqlalchemy import (
    BigInteger,
    Column,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel
from .types import GcRunState


class GcRun(SQLModel, table=True):
    """Immutable candidate plan plus its explicit destructive authorization."""

    __tablename__ = "gc_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    # A nullable unique lease enforced by the database. Every active state owns
    # slot 1; terminal states release it. This prevents two app processes from
    # authorizing overlapping destructive plans.
    active_slot: Optional[int] = Field(default=1, unique=True, index=True)
    state: GcRunState = Field(
        default=GcRunState.PREVIEW,
        sa_column=Column(
            SAEnum(
                GcRunState,
                values_callable=lambda members: [member.value for member in members],
                native_enum=False,
                length=16,
            ),
            nullable=False,
            index=True,
        ),
    )
    digest: str = Field(max_length=64, index=True)
    retention_days: int
    cutoff_at: NaiveDatetime = Field(index=True)
    resource_count: int = 0
    candidate_pool_count: int = 0
    key_count: int = 0
    size_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    scheduled: bool = False
    requested_by: Optional[int] = Field(default=None, foreign_key="users.id")
    approved_by: Optional[int] = Field(default=None, foreign_key="users.id")
    approved_at: Optional[NaiveDatetime] = Field(default=None, index=True)
    quarantine_until: Optional[NaiveDatetime] = Field(default=None, index=True)
    backup_id: Optional[str] = Field(default=None, max_length=255)
    backup_source_ref: Optional[str] = Field(default=None, max_length=64)
    backup_provider_ref: Optional[str] = Field(default=None, max_length=64)
    backup_archive_sha256: Optional[str] = Field(default=None, max_length=64)
    backup_verified_at: Optional[NaiveDatetime] = None
    active_provider_ref: Optional[str] = Field(default=None, max_length=64)
    active_identity_evidence: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    backup_identity_evidence: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    restore_generation: str = Field(default="", max_length=64)
    last_error: Optional[str] = Field(default=None, max_length=255)
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)
    completed_at: Optional[NaiveDatetime] = None


class GcItem(SQLModel, table=True):
    """One exact catalog resource selected by a GC plan."""

    __tablename__ = "gc_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "resource_kind", "resource_id", name="uq_gc_item_resource"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="gc_runs.id", index=True, ondelete="CASCADE")
    resource_kind: str = Field(max_length=32, index=True)
    resource_id: int = Field(index=True)
    deleted_at_snapshot: NaiveDatetime
    key_count: int = 0
    size_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class BackupRun(SQLModel, table=True):
    """One archive build and its snapshotted destination selection."""

    __tablename__ = "backup_runs"
    id: str = Field(primary_key=True, max_length=64)
    backup_id: str = Field(index=True, max_length=64)
    # The Job that built it. Whether a run is still in progress is its Job's
    # state; a run its Job left behind is settled by that Job's next attempt
    # or by its failure, never by guessing from a "running" outcome.
    job_id: Optional[str] = Field(default=None, max_length=64, index=True)
    trigger: str = Field(max_length=16)
    outcome: str = Field(default="running", max_length=16, index=True)
    archive_name: str = Field(max_length=255)
    archive_sha256: Optional[str] = Field(default=None, max_length=64)
    size_bytes: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    file_count: Optional[int] = None
    storage_backend: str = Field(max_length=64)
    app_version: str = Field(max_length=64)
    error_code: Optional[str] = Field(default=None, max_length=128)
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    finished_at: Optional[NaiveDatetime] = None


class BackupDestinationResult(SQLModel, table=True):
    """Immutable destination selection plus publication and verification evidence."""

    __tablename__ = "backup_destination_results"
    id: str = Field(primary_key=True, max_length=64)
    run_id: str = Field(foreign_key="backup_runs.id", ondelete="CASCADE", index=True)
    connection_id: Optional[int] = Field(default=None, index=True)
    kind: str = Field(max_length=32)
    name: str = Field(max_length=255)
    configuration_json: str = Field(
        default="{}", sa_column=Column(Text, nullable=False)
    )
    target_identity_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    provider_ref: Optional[str] = Field(default=None, max_length=64)
    namespace: Optional[str] = Field(default=None, sa_column=Column(Text))
    key: Optional[str] = Field(default=None, sa_column=Column(Text))
    source_ref: Optional[str] = Field(default=None, max_length=64)
    ownership_id: Optional[int] = Field(
        default=None, foreign_key="owned_storage_objects.id", ondelete="SET NULL"
    )
    outcome: str = Field(default="pending", max_length=16, index=True)
    error_code: Optional[str] = Field(default=None, max_length=128)
    published_at: Optional[NaiveDatetime] = None
    verified_at: Optional[NaiveDatetime] = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class BackupRetryAttempt(SQLModel, table=True):
    """Durable audit of one exact destination retry."""

    __tablename__ = "backup_retry_attempts"
    id: str = Field(primary_key=True, max_length=64)
    destination_result_id: str = Field(
        foreign_key="backup_destination_results.id", ondelete="CASCADE", index=True
    )
    source_result_id: Optional[str] = Field(default=None, max_length=64)
    archive_sha256: Optional[str] = Field(default=None, max_length=64)
    outcome: str = Field(default="running", max_length=16)
    error_code: Optional[str] = Field(default=None, max_length=128)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    finished_at: Optional[NaiveDatetime] = None
