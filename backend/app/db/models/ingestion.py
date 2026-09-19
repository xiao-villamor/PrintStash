"""Pending imports, accepted background jobs and staged-upload ownership."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel
from .types import (
    CaptureUploadSlotState,
    InboxItemCompletion,
    InboxItemResultState,
    InboxItemState,
    InboxSourceKind,
)


class InboxItemResult(SQLModel, table=True):
    __tablename__ = "inbox_item_results"
    __table_args__ = (
        UniqueConstraint(
            "inbox_item_id",
            "source_selection_id",
            "result_key",
            name="uq_inbox_result_key",
        ),
        CheckConstraint(
            "state IN ('imported', 'deduplicated', 'failed')",
            name="ck_inbox_result_state",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    inbox_item_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("inbox_items.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    source_selection_id: str = Field(max_length=512)
    result_key: str = Field(max_length=64)
    original_filename: str = Field(max_length=512)
    state: InboxItemResultState = Field(
        sa_column=Column(String(16), nullable=False, index=True)
    )
    model_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("models.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    file_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    provenance_source_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("model_provenance_sources.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    error_code: Optional[str] = Field(default=None, max_length=128)
    retryable: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class BackgroundJob(SQLModel, table=True):
    __tablename__ = "background_jobs"
    __table_args__ = (
        Index(
            "ix_background_jobs_visible_state_owner_updated",
            "visible",
            "state",
            "owner_user_id",
            "updated_at",
        ),
    )

    id: str = Field(primary_key=True, max_length=64)
    owner_user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    visible: bool = Field(default=True, index=True)
    kind: str = Field(default="generic", max_length=64, index=True)
    state: str = Field(default="pending", max_length=16, index=True)
    status_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    payload_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    replay_safe: bool = Field(default=False, index=True)
    claim_token: Optional[str] = Field(default=None, max_length=64, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    attempts: int = Field(default=0)
    next_attempt_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    finished_at: Optional[datetime] = Field(default=None, index=True)


class StagingLease(SQLModel, table=True):
    """Exact durable ownership record for one staged ingestion object."""

    __tablename__ = "staging_leases"
    __table_args__ = (
        CheckConstraint(
            "(background_job_id IS NOT NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NOT NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NOT NULL AND capture_upload_slot_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NOT NULL)",
            name="ck_staging_leases_exactly_one_owner",
        ),
    )

    id: str = Field(primary_key=True, max_length=64)
    path: str = Field(unique=True, max_length=2048)
    owner_user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    background_job_id: Optional[str] = Field(
        default=None, foreign_key="background_jobs.id", index=True
    )
    inbox_item_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("inbox_items.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
            index=True,
        ),
    )
    model_source_cover_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("model_source_covers.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
            index=True,
        ),
    )
    capture_upload_slot_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String(64),
            ForeignKey("capture_upload_slots.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
            index=True,
        ),
    )
    capture_upload_slot_origin_id: Optional[str] = Field(
        default=None, max_length=64, index=True
    )
    size_bytes: int
    sha256: str = Field(max_length=64, index=True)
    device: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    inode: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    ctime_ns: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    destination_key: Optional[str] = Field(default=None, max_length=2048)
    receipt_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class InboxItem(SQLModel, table=True):
    """Durable capture request; API/UI calls these Pending Imports."""

    __tablename__ = "inbox_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    source_kind: InboxSourceKind = Field(default=InboxSourceKind.URL, index=True)
    source_url: Optional[str] = Field(default=None, max_length=2048)
    display_title: Optional[str] = Field(default=None, max_length=255)
    source_hostname: Optional[str] = Field(default=None, max_length=255)
    state: InboxItemState = Field(default=InboxItemState.CAPTURED, index=True)
    manifest_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    staging_key: Optional[str] = Field(default=None, max_length=1024)
    target_collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )
    requested_tags_json: str = Field(
        default="[]", sa_column=Column(Text, nullable=False)
    )
    background_job_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String(64),
            ForeignKey("background_jobs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    resulting_model_id: Optional[int] = Field(
        default=None, foreign_key="models.id", index=True
    )
    error_code: Optional[str] = Field(default=None, max_length=128)
    retryable: bool = Field(default=False, index=True)
    attempt_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    completed_at: Optional[datetime] = None
    completion: Optional[InboxItemCompletion] = Field(
        default=None, sa_column=Column(String(16), nullable=True)
    )


class CaptureUploadSlot(SQLModel, table=True):
    """Declared browser capture object and its durable staging receipt."""

    __tablename__ = "capture_upload_slots"

    id: str = Field(primary_key=True, max_length=64)
    inbox_item_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("inbox_items.id", ondelete="CASCADE"), nullable=False
        ),
    )
    role: str = Field(max_length=16, index=True)
    source_file_id: Optional[str] = Field(default=None, max_length=255)
    filename: str = Field(max_length=512)
    media_type: str = Field(max_length=128)
    size_bytes: int
    sha256: str = Field(max_length=64, index=True)
    state: CaptureUploadSlotState = Field(
        default=CaptureUploadSlotState.PENDING, index=True
    )
    storage_key: Optional[str] = Field(default=None, max_length=2048, unique=True)
    receipt_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    uploaded_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class IngestionReview(SQLModel, table=True):
    """Owner-scoped review manifest that survives process restarts."""

    __tablename__ = "ingestion_reviews"
    __table_args__ = ({"info": {"audit_exclude": True}},)
    id: str = Field(primary_key=True, max_length=64)
    kind: str = Field(max_length=32, index=True)
    owner_user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    source_job_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String(64),
            ForeignKey("background_jobs.id", ondelete="SET NULL"),
            index=True,
        ),
    )
    expires_at: datetime = Field(index=True)
    claim_expires_at: Optional[datetime] = None
