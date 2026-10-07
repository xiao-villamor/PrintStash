"""External libraries, scan checkpoints, discovery inventories and tombstones."""

from datetime import datetime
from typing import ClassVar, Optional
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, column_property
from sqlmodel import Field, select

from app.core.time import utcnow

from .base import SQLModel
from .library import LibraryRevision
from .types import (
    ExternalLibraryCollectionMode,
    ExternalLibraryScanStatus,
    ExternalLibraryWatchMode,
    LibrarySourceKind,
)


class ExternalLibrary(SQLModel, table=True):
    """A user-managed mounted or remote source indexed into the catalog.

    The source is authoritative. PrintStash stores generated thumbnails and
    metadata, then reads originals through ArtifactContent. Mounted sources may
    allow create-only write-back. S3, WebDAV, SFTP, and Google Drive sources are
    read-only.
    Trash and garbage collection never delete source bytes.
    """

    __tablename__ = "external_libraries"

    id: Optional[int] = Field(default=None, primary_key=True)
    edit_version: int = Field(
        default=1, sa_column=Column(BigInteger, nullable=False, server_default="1")
    )
    edit_identity: str = Field(default_factory=lambda: uuid4().hex, max_length=32)
    database_epoch: ClassVar[Mapped[str]]
    name: str = Field(max_length=128)
    root_path: str = Field(max_length=1024)
    source_kind: LibrarySourceKind = Field(
        default=LibrarySourceKind.MOUNTED, index=True
    )
    connection_id: Optional[int] = Field(
        default=None, foreign_key="storage_connections.id", index=True
    )
    source_prefix: str = Field(default="", max_length=1024)
    # Remote sources are indexed read-only. An explicit future capability can
    # opt in only after provider-specific atomic publication is proven.
    writeback_enabled: bool = Field(default=False)
    # Random, durable identity for the exact external root.  Legacy rows are
    # intentionally NULL and remain read-only until an administrator explicitly
    # enrolls the mounted directory.
    root_identity: Optional[str] = Field(default=None, max_length=64, index=True)
    enabled: bool = Field(default=True, index=True)
    # Legacy fixed-interval scheduling. Retained for back-compat / migration
    # source; ``scan_schedule`` (cron) is now the source of truth.
    scan_interval_minutes: int = Field(default=60)
    # Cron expression driving scheduled scans. Empty string = manual only.
    scan_schedule: str = Field(default="0 * * * *", max_length=128)
    # Whether to watch the folder for real-time changes (see enum docstring).
    watch_mode: ExternalLibraryWatchMode = Field(default=ExternalLibraryWatchMode.AUTO)
    # Last-detected filesystem class ("local" / "network" / "unknown"). Display
    # only — explains why watching is or isn't active. Refreshed on each scan /
    # watcher (re)start.
    fs_kind: Optional[str] = Field(default=None, max_length=16)

    collection_mode: ExternalLibraryCollectionMode = Field(
        default=ExternalLibraryCollectionMode.MIRROR
    )
    target_collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id"
    )

    last_scanned_at: Optional[datetime] = Field(default=None)
    last_scan_status: Optional[ExternalLibraryScanStatus] = Field(default=None)
    # JSON blob: {"added": n, "updated": n, "removed": n, "skipped": n,
    #             "errors": [..], "error": "..."}
    last_scan_summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    scan_claim_token: Optional[str] = Field(default=None, max_length=64, index=True)
    scan_claim_expires_at: Optional[datetime] = Field(default=None, index=True)
    scan_job_id: Optional[str] = Field(default=None, max_length=64, index=True)
    # A scan someone asked for (the "scan now" button, the folder watcher).
    # Intent the scan source reads; the scan clears it when it starts.
    # ``scan_requested_path`` narrows the request to one folder; two requests
    # for different folders widen it to a full scan.
    scan_requested_at: Optional[datetime] = Field(default=None, index=True)
    scan_requested_path: Optional[str] = Field(default=None, max_length=2048)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ExternalLibraryTombstone(SQLModel, table=True):
    """Suppress automatic re-import after a source-backed Artifact is trashed."""

    __tablename__ = "external_library_tombstones"
    __table_args__ = (
        UniqueConstraint(
            "library_id", "source_key", name="uq_external_tombstone_source_key"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    library_id: int = Field(
        foreign_key="external_libraries.id", index=True, ondelete="CASCADE"
    )
    source_key: str = Field(max_length=2048)
    sha256: Optional[str] = Field(default=None, max_length=64)
    reason: str = Field(default="removed", max_length=32)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    cleared_at: Optional[datetime] = Field(default=None, index=True)


class ExternalLibraryCheckpoint(SQLModel, table=True):
    """Restart-safe cursor and complete-epoch evidence for bounded scans."""

    __tablename__ = "external_library_checkpoints"
    __table_args__ = (
        UniqueConstraint("library_id", name="uq_external_library_checkpoint"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    library_id: int = Field(
        foreign_key="external_libraries.id", index=True, ondelete="CASCADE"
    )
    epoch: str = Field(max_length=64, index=True)
    cursor: Optional[str] = Field(default=None, max_length=2048)
    complete: bool = Field(default=False, index=True)
    observed_keys_json: str = Field(
        default="[]", sa_column=Column(Text, nullable=False)
    )
    metadata_ops: int = 0
    bytes_read: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    backoff_until: Optional[datetime] = Field(default=None, index=True)
    started_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None


class RemoteDiscoveryInventory(SQLModel, table=True):
    """Target-bound snapshot; incomplete listings grant no absence evidence."""

    __tablename__ = "remote_discovery_inventories"
    id: str = Field(primary_key=True, max_length=64)
    target_ref: str = Field(max_length=64, index=True)
    prefix: str = Field(sa_column=Column(Text, nullable=False))
    complete: bool = False
    metadata_ops: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class RemoteDiscoveryDirectory(SQLModel, table=True):
    __tablename__ = "remote_discovery_directories"
    __table_args__ = (
        UniqueConstraint("inventory_id", "path_hash", name="uq_discovery_directory"),
        Index("ix_discovery_directory_pending", "inventory_id", "complete", "id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    inventory_id: str = Field(
        foreign_key="remote_discovery_inventories.id", ondelete="CASCADE", index=True
    )
    parent_id: Optional[int] = Field(
        default=None,
        foreign_key="remote_discovery_directories.id",
        ondelete="CASCADE",
        index=True,
    )
    path: str = Field(sa_column=Column(Text, nullable=False))
    path_hash: str = Field(max_length=64)
    complete: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class RemoteDiscoveryEntry(SQLModel, table=True):
    __tablename__ = "remote_discovery_entries"
    __table_args__ = (
        UniqueConstraint("inventory_id", "key_hash", name="uq_discovery_entry"),
        Index("ix_discovery_entry_page", "inventory_id", "id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    inventory_id: str = Field(
        foreign_key="remote_discovery_inventories.id", ondelete="CASCADE", index=True
    )
    directory_id: int = Field(
        foreign_key="remote_discovery_directories.id", ondelete="CASCADE", index=True
    )
    source_key: str = Field(sa_column=Column(Text, nullable=False))
    key_hash: str = Field(max_length=64)
    size: int = Field(sa_column=Column(BigInteger, nullable=False))
    modified_at: Optional[datetime] = None
    etag: Optional[str] = None
    version_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class ExternalLibraryObservation(SQLModel, table=True):
    """Indexed seen-key evidence bound to the epoch's linked File row."""

    __tablename__ = "external_library_observations"
    __table_args__ = (
        UniqueConstraint("checkpoint_id", "key_hash", name="uq_library_observation"),
        Index("ix_library_observation_file", "checkpoint_id", "file_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    checkpoint_id: int = Field(
        foreign_key="external_library_checkpoints.id", ondelete="CASCADE", index=True
    )
    key_hash: str = Field(max_length=64)
    file_id: Optional[int] = Field(
        default=None, foreign_key="files.id", ondelete="CASCADE"
    )
    created_at: datetime = Field(default_factory=utcnow)


ExternalLibrary.database_epoch = column_property(
    select(LibraryRevision.epoch).where(LibraryRevision.id == 1).scalar_subquery()
)
