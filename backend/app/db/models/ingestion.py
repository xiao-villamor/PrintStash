"""Pending imports, accepted ingest requests and staged-upload ownership."""

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
from app.db.enum_columns import EnumText, enum_check

from .base import SQLModel
from .types import (
    CaptureUploadSlotState,
    InboxItemCompletion,
    InboxItemResultState,
    InboxItemState,
    InboxSourceKind,
    IngestionEntryState,
    IngestRequestKind,
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


class IngestRequest(SQLModel, table=True):
    """The typed intent behind one accepted ingest Job.

    Everything an ``ingest.*`` job needs besides the staged bytes (which its
    staging leases own) lives here as columns, so an attempt rebuilds its work
    from the current schema rather than from a serialized payload that an
    upgrade could make unreadable.
    """

    __tablename__ = "ingest_requests"
    __table_args__ = (enum_check("kind", IngestRequestKind),)

    job_id: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("jobs.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    kind: IngestRequestKind = Field(
        sa_column=Column(EnumText(IngestRequestKind), nullable=False)
    )
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    original_filename: Optional[str] = Field(default=None, max_length=512)
    model_name: Optional[str] = Field(default=None, max_length=255)
    collection: Optional[str] = Field(default=None, max_length=1024)
    tags: Optional[str] = Field(default=None, sa_column=Column(Text))
    source_hash: Optional[str] = Field(default=None, max_length=64)
    file_type: Optional[str] = Field(default=None, max_length=16)
    target_library_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("external_libraries.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    source_url: Optional[str] = Field(default=None, max_length=2048)
    # A user-supplied source credential (a Thingiverse cookie), encrypted with
    # the installation's secrets key and cleared when the Job settles.
    source_credential: Optional[str] = Field(default=None, sa_column=Column(Text))
    # Kind-specific typed selection (archive entry names, model-page files,
    # collection members), validated by its reader on every attempt.
    selection_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    # A review manifest this request produced (an archive's entries, a model
    # page's files, a collection's members), read back by the selection that
    # follows it. Durable, so any process can serve the selection.
    manifest_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class StagingLease(SQLModel, table=True):
    """Exact durable ownership record for one staged ingestion object."""

    __tablename__ = "staging_leases"
    __table_args__ = (
        CheckConstraint(
            "(job_id IS NOT NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NULL) OR "
            "(job_id IS NULL AND inbox_item_id IS NOT NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NULL) OR "
            "(job_id IS NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NOT NULL AND capture_upload_slot_id IS NULL) OR "
            "(job_id IS NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NOT NULL)",
            name="ck_staging_leases_exactly_one_owner",
        ),
    )

    id: str = Field(primary_key=True, max_length=64)
    path: str = Field(unique=True, max_length=2048)
    owner_user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    job_id: Optional[str] = Field(default=None, foreign_key="jobs.id", index=True)
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
    job_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String(64),
            ForeignKey("jobs.id", ondelete="SET NULL"),
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


class IngestionEntry(SQLModel, table=True):
    """Frozen batch units and incremental outcomes, independent of engine input."""

    __tablename__ = "ingestion_entries"
    __table_args__ = (
        CheckConstraint(
            "(job_id IS NULL) <> (inbox_item_id IS NULL)", name="entry_owner"
        ),
        CheckConstraint("length(entry_key) = 64", name="entry_key_present"),
        CheckConstraint("ordinal >= 0", name="entry_ordinal_nonnegative"),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0", name="entry_size_nonnegative"
        ),
        CheckConstraint(
            "(state IN ('failed', 'skipped') AND error_code IS NOT NULL AND length(error_code) > 0) "
            "OR (state NOT IN ('failed', 'skipped') AND error_code IS NULL)",
            name="entry_diagnostic",
        ),
        CheckConstraint("NOT retryable OR state = 'failed'", name="entry_retryable"),
        enum_check("state", IngestionEntryState),
        UniqueConstraint("job_id", "entry_key", name="uq_ingestion_entry_job_key"),
        UniqueConstraint(
            "inbox_item_id", "entry_key", name="uq_ingestion_entry_inbox_key"
        ),
        Index("ix_ingestion_entries_job_page", "job_id", "id"),
        Index("ix_ingestion_entries_inbox_page", "inbox_item_id", "id"),
        Index(
            "ix_ingestion_entries_job_state_order", "job_id", "state", "ordinal", "id"
        ),
        Index(
            "ix_ingestion_entries_inbox_state_order",
            "inbox_item_id",
            "state",
            "ordinal",
            "id",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String(64), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True
        ),
    )
    inbox_item_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("inbox_items.id", ondelete="CASCADE"), nullable=True
        ),
    )
    entry_key: str = Field(max_length=64)
    identity: str = Field(sa_column=Column(Text, nullable=False))
    display_name: str = Field(max_length=512)
    descriptor_json: str = Field(sa_column=Column(Text, nullable=False))
    ordinal: int = Field(default=0)
    size_bytes: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    state: IngestionEntryState = Field(
        default=IngestionEntryState.PENDING,
        sa_column=Column(EnumText(IngestionEntryState), nullable=False),
    )
    model_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("models.id", ondelete="SET NULL"), nullable=True
        ),
    )
    file_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("files.id", ondelete="SET NULL"), nullable=True
        ),
    )
    error_code: Optional[str] = Field(default=None, max_length=128)
    retryable: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
