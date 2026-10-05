"""Durable custody of disposable ingest workspaces, distinct from input leases."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String
from sqlmodel import Field

from app.core.time import utcnow
from app.db.enum_columns import EnumText, enum_check

from .base import SQLModel


class ScratchWindowKind(StrEnum):
    DOWNLOAD = "download"
    ARCHIVE_ENTRY = "archive_entry"
    LOCAL_COPY = "local_copy"


class ScratchWindowPhase(StrEnum):
    PREPARING = "preparing"
    OPEN = "open"
    SEALED = "sealed"
    RELEASE_PENDING = "release_pending"
    TRANSFERRED = "transferred"
    RETIRING = "retiring"


class IngestionScratchWindow(SQLModel, table=True):
    """A row survives until its exact workspace and capacity claim are retired."""

    __tablename__ = "ingestion_scratch_windows"  # type: ignore[assignment]
    __table_args__ = (
        enum_check("kind", ScratchWindowKind),
        enum_check("phase", ScratchWindowPhase),
        CheckConstraint("max_bytes > 0", name="scratch_positive_budget"),
        CheckConstraint(
            "(phase = 'preparing' AND ((lock_device IS NULL AND lock_inode IS NULL) OR (lock_device IS NOT NULL AND lock_inode IS NOT NULL))) OR "
            "(phase <> 'preparing' AND lock_device IS NOT NULL AND lock_inode IS NOT NULL)",
            name="scratch_lock_identity",
        ),
        CheckConstraint(
            "(origin_job_id IS NULL AND execution_epoch IS NULL) OR "
            "(origin_job_id IS NOT NULL AND length(origin_job_id) > 0 AND execution_epoch IS NOT NULL AND length(execution_epoch) > 0)",
            name="scratch_owner_identity",
        ),
        CheckConstraint(
            "(phase = 'preparing' AND device IS NULL AND inode IS NULL AND transferred_path IS NULL) OR "
            "(phase IN ('open', 'sealed', 'release_pending') AND device IS NOT NULL AND inode IS NOT NULL AND transferred_path IS NULL) OR "
            "(phase = 'transferred' AND device IS NOT NULL AND inode IS NOT NULL AND transferred_path IS NOT NULL) OR "
            "(phase = 'retiring' AND ((device IS NULL AND inode IS NULL) OR (device IS NOT NULL AND inode IS NOT NULL)))",
            name="scratch_physical_identity",
        ),
        CheckConstraint(
            "(output_name IS NULL AND output_device IS NULL AND output_inode IS NULL) OR "
            "(output_name IS NOT NULL AND length(output_name) > 0 AND output_device IS NOT NULL AND output_inode IS NOT NULL)",
            name="scratch_output_identity",
        ),
        Index("ix_ingestion_scratch_due", "available_at", "id"),
    )

    id: str = Field(primary_key=True, max_length=64)
    path: str = Field(unique=True, max_length=2048)
    lock_path: str = Field(unique=True, max_length=2048)
    parent_device: int = Field(sa_column=Column(BigInteger, nullable=False))
    parent_inode: int = Field(sa_column=Column(BigInteger, nullable=False))
    output_name: str | None = Field(default=None, max_length=255)
    output_device: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    output_inode: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    lock_device: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    lock_inode: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    marker_token: str = Field(max_length=64)
    kind: ScratchWindowKind = Field(
        sa_column=Column(EnumText(ScratchWindowKind), nullable=False)
    )
    phase: ScratchWindowPhase = Field(
        sa_column=Column(EnumText(ScratchWindowPhase), nullable=False)
    )
    job_id: str | None = Field(
        default=None,
        sa_column=Column(
            String(64),
            ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    origin_job_id: str | None = Field(default=None, max_length=64, index=True)
    execution_epoch: str | None = Field(default=None, max_length=64)
    request_token: str = Field(max_length=64)
    capacity_operation_id: str = Field(unique=True, max_length=200)
    max_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    device: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    inode: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    transferred_path: str | None = Field(default=None, max_length=2048)
    available_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)
