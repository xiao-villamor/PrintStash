"""SQLModel tables for the vault.

Conventions:
- Every table has ``id``, ``created_at``, and (where mutable) ``updated_at``.
- Hashes are lowercase sha256 hex (64 chars), indexed when used for dedup.
- File paths are container-absolute; host mapping is a deployment concern.

The ``# type: ignore`` comments scattered through this module exist because
SQLModel/SQLAlchemy's column descriptors confuse static type checkers. They
are correct at runtime.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, Relationship, SQLModel

from app.core.time import utcnow
from app.db.encrypted import EncryptedText


class FileType(str, Enum):
    STL = "stl"
    THREE_MF = "3mf"
    GCODE = "gcode"
    OBJ = "obj"
    STEP = "step"


class FileRevisionStatus(str, Enum):
    KNOWN_GOOD = "known_good"
    NEEDS_TEST = "needs_test"
    FAILED = "failed"
    ARCHIVED = "archived"


# Mapping from filesystem suffix to ``FileType``. Used by ingest routers.
SUFFIX_TO_FILE_TYPE: dict[str, FileType] = {
    ".stl": FileType.STL,
    ".3mf": FileType.THREE_MF,
    ".obj": FileType.OBJ,
    ".step": FileType.STEP,
    ".stp": FileType.STEP,
    ".gcode": FileType.GCODE,
    ".g": FileType.GCODE,
    ".gco": FileType.GCODE,
    # PrusaSlicer binary G-code: metadata + thumbnail parse like a text G-code.
    ".bgcode": FileType.GCODE,
}


class PrinterStatus(str, Enum):
    UNKNOWN = "unknown"
    OFFLINE = "offline"
    READY = "ready"
    PRINTING = "printing"
    PAUSED = "paused"
    ERROR = "error"


class PrinterProvider(str, Enum):
    MOONRAKER = "moonraker"
    BAMBU_LAN = "bambu_lan"
    PRUSALINK = "prusalink"
    ELEGOO_CENTAURI = "elegoo_centauri"
    OCTOPRINT = "octoprint"


class PrintJobState(str, Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    STARTED = "started"
    PRINTING = "printing"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class VaultAuditMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


class VaultAuditRunState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class VaultAuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class VaultAuditFindingState(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class InboxSourceKind(str, Enum):
    URL = "url"
    BROWSER = "browser"
    UPLOAD = "upload"
    EXTERNAL = "external"


class InboxItemState(str, Enum):
    CAPTURED = "captured"
    RESOLVING = "resolving"
    REVIEW = "review"
    IMPORTING = "importing"
    COMPLETED = "completed"
    FAILED = "failed"
    DISMISSED = "dismissed"


class RoutingStrategy(str, Enum):
    MANUAL = "manual"
    DEFAULT = "default"
    LEAST_BUSY = "least_busy"


class MaterialSlotState(str, Enum):
    LOADED = "loaded"
    EMPTY = "empty"
    UNKNOWN = "unknown"


class MaterialSource(str, Enum):
    MANUAL = "manual"
    BAMBU_AMS = "bambu_ams"
    MOONRAKER_SPOOLMAN = "moonraker_spoolman"


class JobPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    RUSH = "rush"


class CompatibilityPolicy(str, Enum):
    SAFE = "safe"
    ALLOW_MISMATCH = "allow_mismatch"


class OperatorGateState(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    RELEASED = "released"
    HELD = "held"


class CollectionRole(str, Enum):
    VIEW = "view"
    EDIT = "edit"
    ADMIN = "admin"


class PrinterRole(str, Enum):
    """Ordered access levels for one physical printer."""

    VIEW = "view"
    PRINT = "print"
    CONTROL = "control"
    ADMIN = "admin"


class NotificationEventType(str, Enum):
    """Lifecycle events users can subscribe to.

    ``print_cancelled`` is split from ``print_failed`` so a user-initiated
    cancellation can be muted without silencing genuine print failures.
    """

    PRINT_COMPLETED = "print_completed"
    PRINT_FAILED = "print_failed"
    PRINT_CANCELLED = "print_cancelled"
    PRINTER_OFFLINE = "printer_offline"


class NotificationTarget(str, Enum):
    """Where a notification is delivered."""

    WEBHOOK = "webhook"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    NTFY = "ntfy"


class NotificationDeliveryStatus(str, Enum):
    PENDING = "pending"  # queued, awaiting first/next attempt
    SENDING = "sending"  # claimed by a dispatcher, in flight
    SENT = "sent"  # delivered successfully
    FAILED = "failed"  # gave up after exhausting retries


class Metadata(SQLModel, table=True):
    """Slicer-derived facts. 1:1 with File."""

    __tablename__ = "metadata"

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", unique=True, index=True)

    # Slicer
    slicer_name: Optional[str] = Field(default=None, max_length=64)
    slicer_version: Optional[str] = Field(default=None, max_length=32)
    printer_model: Optional[str] = Field(default=None, max_length=128)
    nozzle_diameter_mm: Optional[float] = None
    layer_height_mm: Optional[float] = None
    first_layer_height_mm: Optional[float] = None
    infill_percent: Optional[float] = None
    wall_loops: Optional[int] = None
    top_shell_layers: Optional[int] = None
    bottom_shell_layers: Optional[int] = None
    support_material: Optional[bool] = None
    nozzle_temperature_c: Optional[float] = None
    bed_temperature_c: Optional[float] = None

    # Print stats
    estimated_time_s: Optional[int] = None
    filament_weight_g: Optional[float] = None
    filament_length_mm: Optional[float] = None
    filament_cost: Optional[float] = None
    material_type: Optional[str] = Field(default=None, max_length=64)
    material_brand: Optional[str] = Field(default=None, max_length=128)

    # Geometry (filled later by Trimesh; left None in Stage 1 for STL/3MF)
    bbox_x_mm: Optional[float] = None
    bbox_y_mm: Optional[float] = None
    bbox_z_mm: Optional[float] = None
    volume_mm3: Optional[float] = None
    triangle_count: Optional[int] = None

    created_at: datetime = Field(default_factory=utcnow)

    file: Optional["File"] = Relationship(back_populates="file_metadata")


class ArtifactMaterialRequirement(SQLModel, table=True):
    """Per-tool material facts parsed from one G-code Artifact."""

    __tablename__ = "artifact_material_requirements"
    __table_args__ = (
        UniqueConstraint(
            "file_id", "tool_index", name="uq_artifact_material_requirement_tool"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", index=True)
    tool_index: int = Field(default=0)
    material_type: Optional[str] = Field(default=None, max_length=64)
    color_hex: Optional[str] = Field(default=None, max_length=16)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class FilamentProfile(SQLModel, table=True):
    """Local slicer filament preset with cost data for per-part estimates."""

    __tablename__ = "filament_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128, unique=True, index=True)
    material_type: Optional[str] = Field(default=None, max_length=64, index=True)
    material_brand: Optional[str] = Field(default=None, max_length=128, index=True)
    cost_per_kg: Optional[float] = None
    notes: Optional[str] = None

    # When set, this preset is a read-only mirror of a Spoolman filament (the
    # source of truth). Sync keeps cost/material/density/diameter aligned; the
    # API rejects local edits/deletes of linked presets. Cleared (reverting the
    # preset to a local-only, editable one) when its Spoolman filament is gone.
    spoolman_filament_id: Optional[int] = Field(default=None, index=True)
    # Physical filament properties Spoolman knows but local presets historically
    # didn't — used for accurate mm→grams when a synced spool is selected.
    density_g_cm3: Optional[float] = None
    diameter_mm: Optional[float] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrinterProfile(SQLModel, table=True):
    """Local slicer printer preset detected from uploaded jobs."""

    __tablename__ = "printer_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128, unique=True, index=True)
    printer_model: Optional[str] = Field(default=None, max_length=128, index=True)
    slicer_name: Optional[str] = Field(default=None, max_length=64, index=True)
    nozzle_diameter_mm: Optional[float] = None
    notes: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class File(SQLModel, table=True):
    """Physical artifact stored on disk; many-to-one with Model."""

    __tablename__ = "files"
    __table_args__ = (
        Index("uq_files_model_version", "model_id", "version", unique=True),
        Index("ix_files_model_deleted_type", "model_id", "deleted_at", "file_type"),
        Index(
            "uq_files_live_recommended_gcode",
            "model_id",
            unique=True,
            sqlite_where=text(
                "file_type = 'GCODE' AND is_recommended = 1 AND deleted_at IS NULL"
            ),
            postgresql_where=text(
                "file_type = 'GCODE' AND is_recommended IS TRUE AND deleted_at IS NULL"
            ),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: int = Field(foreign_key="models.id", index=True)

    path: str = Field(max_length=1024)
    original_filename: str = Field(max_length=512)
    file_type: FileType = Field(index=True)
    version: int = Field(default=1)
    size_bytes: int
    sha256: str = Field(index=True, max_length=64)
    revision_label: Optional[str] = Field(default=None, max_length=128)
    revision_status: Optional[FileRevisionStatus] = Field(default=None, index=True)
    revision_notes: Optional[str] = None
    is_recommended: bool = Field(default=False, index=True)

    # External libraries (NAS folder mirroring). When ``is_external`` is true the
    # blob lives on a user-managed external root (``path`` is its absolute path on
    # that root) — PrintStash indexes/serves it but never owns or deletes the bytes.
    # ``source_mtime`` is the on-disk mtime captured at scan time, used alongside
    # ``size_bytes`` for cheap change detection on subsequent scans.
    is_external: bool = Field(default=False, index=True)
    external_library_id: Optional[int] = Field(
        default=None, foreign_key="external_libraries.id", index=True
    )
    source_mtime: Optional[float] = None
    ingestion_key: Optional[str] = Field(
        default=None, max_length=64, unique=True, index=True
    )
    thumbnail_path: Optional[str] = Field(default=None, max_length=2048)

    uploaded_at: datetime = Field(default_factory=utcnow, index=True)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    purge_token: Optional[str] = Field(default=None, max_length=64, index=True)

    model: Optional["Model"] = Relationship(
        back_populates="files",
        sa_relationship_kwargs={"foreign_keys": "File.model_id"},
    )
    file_metadata: Optional[Metadata] = Relationship(
        back_populates="file",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"},
    )


# ---------------------------------------------------------------------------
# Collections (hierarchical) & Tags (flat, many-to-many)
# ---------------------------------------------------------------------------


class Collection(SQLModel, table=True):
    """Hierarchical collection. Self-referential via parent_id.

    `path` is the materialised slash-joined slug chain ("functional/brackets"),
    used for fast filtering and stable URLs.
    """

    __tablename__ = "collections"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    slug: str = Field(max_length=128, index=True)
    parent_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )
    path: str = Field(max_length=512, unique=True, index=True)

    # Short markdown description shown on top of the collection view. Image refs
    # point at /collections/{id}/images/{name} (self-hosted).
    readme: Optional[str] = Field(default=None, sa_column=Column(Text))

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    purge_token: Optional[str] = Field(default=None, max_length=64, index=True)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class CollectionPermission(SQLModel, table=True):
    __tablename__ = "collection_permissions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "collection_id",
            name="uq_collection_permissions_user_collection",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    collection_id: int = Field(foreign_key="collections.id", index=True)
    role: CollectionRole = Field(index=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=64, unique=True, index=True)
    slug: str = Field(max_length=64, unique=True, index=True)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class ModelTagLink(SQLModel, table=True):
    """Association table for Model <-> Tag."""

    __tablename__ = "model_tags"

    model_id: Optional[int] = Field(
        default=None, foreign_key="models.id", primary_key=True
    )
    tag_id: Optional[int] = Field(default=None, foreign_key="tags.id", primary_key=True)


class ModelStar(SQLModel, table=True):
    """Per-user favorite marker for a live Model."""

    __tablename__ = "model_stars"
    __table_args__ = (
        UniqueConstraint("user_id", "model_id", name="uq_model_stars_user_model"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    model_id: int = Field(foreign_key="models.id", index=True, ondelete="CASCADE")
    created_at: datetime = Field(default_factory=utcnow)


class SavedView(SQLModel, table=True):
    """Named model-list filters owned by one user."""

    __tablename__ = "saved_views"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_saved_views_user_name"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    name: str = Field(max_length=128)
    filters_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Model(SQLModel, table=True):
    """Logical asset, deduplicated by `hash` (source mesh sha256, gcode fallback)."""

    __tablename__ = "models"
    __table_args__ = (
        Index("ix_models_deleted_updated_id", "deleted_at", "updated_at", "id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=255)
    slug: str = Field(index=True, unique=True, max_length=255)
    hash: str = Field(index=True, unique=True, max_length=64)
    # Allocated atomically before an Artifact is persisted. Keeping the counter
    # on the owning Model turns version selection into a row-level write instead
    # of the racy ``MAX(files.version) + 1`` read.
    next_file_version: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default="1"),
    )

    collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )
    description: Optional[str] = None
    source_url: Optional[str] = Field(default=None, max_length=2048)
    thumbnail_path: Optional[str] = Field(default=None, max_length=512)
    thumbnail_file_id: Optional[int] = Field(default=None, foreign_key="files.id")

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    purge_token: Optional[str] = Field(default=None, max_length=64, index=True)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)

    files: List[File] = Relationship(
        back_populates="model",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "foreign_keys": "File.model_id",
        },
    )
    tags: List[Tag] = Relationship(
        link_model=ModelTagLink,
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    collection_rel: Optional["Collection"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "Model.collection_id == Collection.id",
            "lazy": "selectin",
        },
    )


class DocumentKind(str, Enum):
    MARKDOWN = "markdown"  # editable in-app, content in ``body``
    PDF = "pdf"  # binary blob under document_file_key
    OTHER = "other"  # any other uploaded binary


class Document(SQLModel, table=True):
    """A standalone document item (manual / notes) living in a collection,
    shown in the library alongside models. Markdown docs keep their content in
    ``body`` (editable); binary docs (PDF/other) store ``filename`` + a blob."""

    __tablename__ = "documents"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=255)
    kind: DocumentKind = Field(index=True)
    collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )

    body: Optional[str] = Field(default=None, sa_column=Column(Text))  # markdown
    filename: Optional[str] = Field(default=None, max_length=255)  # binary
    size_bytes: Optional[int] = None
    sha256: Optional[str] = Field(default=None, max_length=64)

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    purge_token: Optional[str] = Field(default=None, max_length=64, index=True)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


# ---------------------------------------------------------------------------
# Printers & Print Jobs (Stage 3 — Klipper / Moonraker integration)
# ---------------------------------------------------------------------------


class Printer(SQLModel, table=True):
    __tablename__ = "printers"
    __table_args__ = (
        Index(
            "uq_printers_live_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1 AND deleted_at IS NULL"),
            postgresql_where=text("is_default IS TRUE AND deleted_at IS NULL"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    provider: PrinterProvider = Field(
        default=PrinterProvider.MOONRAKER,
        index=True,
    )
    # Base URL of Moonraker, e.g. "http://mainsailos.local" or "http://10.0.0.42:7125".
    moonraker_url: str = Field(default="", max_length=512)
    api_key: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    provider_variant: Optional[str] = Field(default=None, max_length=64)
    bambu_host: Optional[str] = Field(default=None, max_length=255)
    bambu_serial: Optional[str] = Field(default=None, max_length=128)
    bambu_access_code: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    prusalink_url: Optional[str] = Field(default=None, max_length=512)
    prusalink_auth_mode: Optional[str] = Field(default=None, max_length=32)
    prusalink_username: Optional[str] = Field(default=None, max_length=128)
    prusalink_password: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    prusalink_api_key: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    elegoo_centauri_host: Optional[str] = Field(default=None, max_length=255)
    elegoo_centauri_access_code: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    elegoo_centauri_mainboard_id: Optional[str] = Field(default=None, max_length=128)
    octoprint_url: Optional[str] = Field(default=None, max_length=512)
    octoprint_api_key: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    # Hardware model label shown on the printer card. ``model_name`` is a
    # user-set override; ``detected_model`` is a best-effort guess from
    # provider_variant/bambu_serial, recomputed on create/update. Display
    # precedence is model_name, falling back to detected_model.
    model_name: Optional[str] = Field(default=None, max_length=128)
    detected_model: Optional[str] = Field(default=None, max_length=128)
    notes: Optional[str] = None
    group: Optional[str] = Field(default=None, max_length=128, index=True)
    is_default: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0", index=True),
    )
    drain_mode: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0", index=True),
    )
    drain_reason: Optional[str] = Field(default=None, max_length=512)
    drain_updated_at: Optional[datetime] = None
    provider_material_sync_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    operator_release_required: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0", index=True),
    )

    # Cached liveness info (refreshed by the live-state worker).
    status: PrinterStatus = Field(default=PrinterStatus.UNKNOWN, index=True)
    last_seen_at: Optional[datetime] = None
    last_error: Optional[str] = Field(default=None, max_length=512)

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrinterTool(SQLModel, table=True):
    __tablename__ = "printer_tools"
    __table_args__ = (
        UniqueConstraint(
            "printer_id", "source", "tool_key", name="uq_printer_tools_source_key"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    printer_id: int = Field(foreign_key="printers.id", index=True)
    tool_key: str = Field(default="tool0", max_length=64)
    label: str = Field(default="Tool 0", max_length=128)
    nozzle_diameter_mm: Optional[float] = None
    source: MaterialSource = Field(
        default=MaterialSource.MANUAL,
        sa_column=Column(
            SAEnum(MaterialSource), nullable=False, server_default="MANUAL"
        ),
    )
    observed_at: Optional[datetime] = None
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrinterMaterialSlot(SQLModel, table=True):
    __tablename__ = "printer_material_slots"
    __table_args__ = (
        UniqueConstraint(
            "printer_id",
            "source",
            "slot_key",
            name="uq_printer_material_slot_source_key",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    printer_id: int = Field(foreign_key="printers.id", index=True)
    slot_key: str = Field(max_length=64)
    label: str = Field(max_length=128)
    tool_key: Optional[str] = Field(default=None, max_length=64)
    state: MaterialSlotState = Field(
        default=MaterialSlotState.UNKNOWN,
        sa_column=Column(
            SAEnum(MaterialSlotState),
            nullable=False,
            server_default="UNKNOWN",
            index=True,
        ),
    )
    source: MaterialSource = Field(
        default=MaterialSource.MANUAL,
        sa_column=Column(
            SAEnum(MaterialSource), nullable=False, server_default="MANUAL"
        ),
    )
    material_type: Optional[str] = Field(default=None, max_length=64)
    material_brand: Optional[str] = Field(default=None, max_length=128)
    color_hex: Optional[str] = Field(default=None, max_length=16)
    spool_id: Optional[int] = Field(default=None, index=True)
    spool_name: Optional[str] = Field(default=None, max_length=256)
    spool_filament_id: Optional[int] = Field(default=None, index=True)
    observed_at: Optional[datetime] = Field(default=None, index=True)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrinterPermission(SQLModel, table=True):
    __tablename__ = "printer_permissions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "printer_id",
            name="uq_printer_permissions_user_printer",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    printer_id: int = Field(foreign_key="printers.id", index=True)
    role: PrinterRole = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(max_length=128, unique=True, index=True)
    email: Optional[str] = Field(default=None, max_length=255)
    hashed_password: str = Field(max_length=255)
    is_superuser: bool = Field(default=False)
    is_active: bool = Field(default=True)
    # Incrementing this value invalidates every access token issued for user.
    # Persisted in DB so logout survives API restarts and multiple processes.
    auth_version: int = Field(default=0)
    # External identity is additive: local users keep these null and local login
    # remains available even when OIDC is configured.
    oidc_issuer: Optional[str] = Field(default=None, max_length=512, index=True)
    oidc_subject: Optional[str] = Field(default=None, max_length=255, index=True)
    oidc_managed: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0", index=True),
    )

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(max_length=64, unique=True, index=True)
    expires_at: datetime = Field(index=True)
    revoked: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    revoked_at: Optional[datetime] = None


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(default="Programmatic access", max_length=128)
    key_hash: str = Field(max_length=64, unique=True, index=True)
    prefix: str = Field(max_length=16, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = Field(default=None, index=True)


class SystemConfig(SQLModel, table=True):
    """Singleton row (id=1) holding runtime-configurable settings.

    Values stored here overlay the env-based ``Settings`` on each startup and
    after the first-run setup wizard completes. Anything ``None`` means
    "fall back to env / default".

    A ``configured_at`` non-null value is the source of truth for whether the
    install has completed first-run setup. If ``configured_at`` is ``None`` and
    no users exist, the API exposes the ``/setup`` flow and refuses all other
    write traffic.
    """

    __tablename__ = "system_config"

    id: Optional[int] = Field(default=1, primary_key=True)

    # Local storage paths (overridden at runtime)
    data_dir: Optional[str] = Field(default=None, max_length=1024)
    thumb_dir: Optional[str] = Field(default=None, max_length=1024)

    # Storage backend: "local" or "s3"
    storage_backend: Optional[str] = Field(default=None, max_length=64)

    # Generated on first boot when no VAULT_JWT_SECRET is supplied, so an install
    # never signs tokens with the public default. Stays None when the operator
    # sets the env var — theirs wins and we don't copy it into the DB.
    jwt_secret: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )

    # Optional OpenID Connect provider. Null values fall back to VAULT_OIDC_*
    # environment settings; client secret is encrypted at rest.
    oidc_enabled: Optional[bool] = None
    oidc_issuer_url: Optional[str] = Field(default=None, max_length=512)
    oidc_client_id: Optional[str] = Field(default=None, max_length=255)
    oidc_client_secret: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    oidc_scopes: Optional[str] = Field(default=None, max_length=512)
    oidc_username_claim: Optional[str] = Field(default=None, max_length=128)
    oidc_groups_claim: Optional[str] = Field(default=None, max_length=128)
    oidc_admin_groups: Optional[str] = Field(default=None, max_length=1024)
    oidc_display_name: Optional[str] = Field(default=None, max_length=128)
    oidc_redirect_uri: Optional[str] = Field(default=None, max_length=1024)
    oidc_allow_insecure_http: Optional[bool] = None

    # S3 / R2 settings
    s3_bucket: Optional[str] = Field(default=None, max_length=256)
    s3_endpoint_url: Optional[str] = Field(default=None, max_length=512)
    s3_region: Optional[str] = Field(default=None, max_length=128)
    s3_access_key: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    s3_secret_key: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )

    # Backup
    backup_retention_days: Optional[int] = Field(default=None)
    trash_retention_days: Optional[int] = Field(default=None)

    # Backup S3 destination (separate from vault S3 — allows local vault + cloud backups)
    backup_s3_bucket: Optional[str] = Field(default=None, max_length=256)
    backup_s3_endpoint_url: Optional[str] = Field(default=None, max_length=512)
    backup_s3_region: Optional[str] = Field(default=None, max_length=128)
    backup_s3_access_key: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    backup_s3_secret_key: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )

    # Behaviour toggles
    # When true, a file's revision is auto-marked known_good after its first
    # successful print (never overriding a human's failed/archived verdict).
    auto_mark_known_good: bool = Field(default=True)

    # Opt-in master switch for NAS folder mirroring (External Libraries). Off by
    # default: while disabled, the scan loop is idle and the /libraries API and UI
    # are unavailable. Disabling later never deletes libraries or indexed models.
    external_libraries_enabled: bool = Field(default=False)

    # Opt-in master switch for outbound notifications (webhooks, Discord,
    # Telegram, ntfy). Off by default: while disabled, no events are enqueued
    # and the dispatcher loop stays idle. Disabling never deletes channels.
    notifications_enabled: bool = Field(default=False)

    # ISO 4217 currency code used to render cost figures (statistics, filament
    # cost). ``None`` falls back to the default "USD".
    currency: Optional[str] = Field(default=None, max_length=3)

    # Generated Model preview width. Null falls back to
    # VAULT_MODEL_THUMBNAIL_WIDTH (640 by default).
    model_thumbnail_width: Optional[int] = Field(default=None)

    # Opt-in master switch for the Spoolman filament-inventory integration. Off
    # by default: while disabled the Spoolman API/UI are idle and no consumption
    # is written. Spoolman stays the source of truth for spools and remaining
    # weight; PrintStash reads it for display and writes measured usage back.
    spoolman_enabled: bool = Field(default=False)
    spoolman_base_url: Optional[str] = Field(default=None, max_length=512)
    # Optional API key / bearer token (e.g. for a reverse proxy in front of
    # Spoolman). Stored plaintext like the S3 secrets / makerworld_token above,
    # superuser-only API, masked on read.
    spoolman_api_key: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    # Whether PrintStash writes consumption back to Spoolman on measured-print
    # completion. Off by default: enabling Spoolman only turns on write-back for
    # providers that report measured consumption (currently Moonraker); leaving
    # it off by default avoids implying write-back for providers that can never
    # report it. The write path also skips at runtime when Moonraker's native
    # Spoolman hook is decrementing the active spool (see spoolman_write_force)
    # so a print is never counted twice.
    spoolman_write_enabled: bool = Field(default=False)
    # Override the native-hook double-count guard: when True, PrintStash writes
    # consumption back even if Spoolman reports an active spool (use only after
    # disabling Moonraker's own Spoolman decrement). Off by default so the guard
    # protects users who never open the settings card.
    spoolman_write_force: bool = Field(default=False)

    # MakerWorld session token (a Bambu account JWT) obtained via the in-app
    # login flow. MakerWorld auth-gates file downloads; this token is injected as
    # the ``token=<jwt>`` cookie so imports authenticate. Stored like the S3
    # secrets above (plaintext, superuser-only API). ``None`` = not connected.
    makerworld_token: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    makerworld_token_updated_at: Optional[datetime] = Field(default=None)

    configured_at: Optional[datetime] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class OwnedStorageObject(SQLModel, table=True):
    """Object-level proof recorded only after a create-only storage write.

    Legacy rows intentionally have no entry. Their bytes are therefore never
    eligible for automated deletion: database presence, naming, and directory
    placement are claims, not proof that this installation created an object.
    """

    __tablename__ = "owned_storage_objects"
    __table_args__ = (
        UniqueConstraint(
            "backend", "namespace", "key", name="uq_owned_storage_locator"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    backend: str = Field(max_length=32, index=True)
    namespace: str = Field(max_length=1024, index=True)
    key: str = Field(max_length=2048)
    object_kind: str = Field(max_length=64, index=True)
    token: str = Field(max_length=64)
    size_bytes: int
    etag: Optional[str] = Field(default=None, max_length=255)
    version_id: Optional[str] = Field(default=None, max_length=1024)
    device: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    inode: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    ctime_ns: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    created_at: datetime = Field(default_factory=utcnow)


class StorageDeleteIntent(SQLModel, table=True):
    """Durable, immutable authorization to delete one exact owned object."""

    __tablename__ = "storage_delete_intents"
    __table_args__ = (
        UniqueConstraint(
            "backend",
            "namespace",
            "key",
            "token",
            name="uq_storage_delete_intent_receipt",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    backend: str = Field(max_length=32, index=True)
    namespace: str = Field(max_length=1024)
    key: str = Field(max_length=2048)
    object_kind: str = Field(max_length=64, index=True)
    token: str = Field(max_length=64)
    size_bytes: int
    etag: Optional[str] = Field(default=None, max_length=255)
    version_id: Optional[str] = Field(default=None, max_length=1024)
    device: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    inode: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    ctime_ns: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    resource_kind: Optional[str] = Field(default=None, max_length=64, index=True)
    resource_id: Optional[str] = Field(default=None, max_length=64, index=True)
    status: str = Field(default="pending", max_length=16, index=True)
    attempts: int = Field(default=0)
    next_attempt_at: Optional[datetime] = Field(default=None, index=True)
    last_error: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None


class ExternalLibraryCollectionMode(str, Enum):
    """How a scanned file's NAS subfolder maps to a vault Collection."""

    # Mirror the folder tree: ``{root}/functional/brackets/x.stl`` -> "functional/brackets".
    MIRROR = "mirror"
    # Drop everything into one fixed target collection (``target_collection_id``).
    SINGLE = "single"


class ExternalLibraryScanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    RUNNING = "running"
    # Scan completed but one or more files failed to index — terminal, like OK,
    # but surfaces the partial failure instead of a misleading green status.
    PARTIAL = "partial"


class ExternalLibraryWatchMode(str, Enum):
    """Whether a library is watched for real-time changes (watchfiles).

    Real-time watching only works on local filesystems; on network mounts
    (NFS/SMB/CIFS) the kernel does not deliver inotify events, so the library
    falls back to its scheduled scan. ``AUTO`` decides from the detected
    filesystem; ``EVENTS``/``OFF`` are explicit user overrides.
    """

    # Watch only when the root is on a local filesystem (auto-detected).
    AUTO = "auto"
    # Force watching regardless of detected filesystem.
    EVENTS = "events"
    # Never watch; rely solely on the schedule / manual scans.
    OFF = "off"


class ExternalLibrary(SQLModel, table=True):
    """A user-managed external folder (typically on a NAS) mirrored into the vault.

    The folder is the source of truth: PrintStash indexes files where they sit
    (``File.is_external=true``, ``File.path`` = absolute on-disk path), stores only
    the generated thumbnail + metadata, and streams originals on demand. A scan
    reconciles the index with the folder; web uploads/revisions write back into the
    folder so it stays complete. PrintStash never overwrites or deletes existing bytes.
    """

    __tablename__ = "external_libraries"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    root_path: str = Field(max_length=1024)
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

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrintBatch(SQLModel, table=True):
    __tablename__ = "print_batches"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_print_batches_quantity_positive"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", index=True)
    model_id: int = Field(foreign_key="models.id", index=True)
    quantity: int
    routing_strategy: RoutingStrategy = Field(
        default=RoutingStrategy.LEAST_BUSY,
        sa_column=Column(
            SAEnum(RoutingStrategy), nullable=False, server_default="LEAST_BUSY"
        ),
    )
    priority: JobPriority = Field(
        default=JobPriority.NORMAL,
        sa_column=Column(SAEnum(JobPriority), nullable=False, server_default="NORMAL"),
    )
    target_group: Optional[str] = Field(default=None, max_length=128, index=True)
    compatibility_policy: CompatibilityPolicy = Field(
        default=CompatibilityPolicy.SAFE,
        sa_column=Column(
            SAEnum(CompatibilityPolicy), nullable=False, server_default="SAFE"
        ),
    )
    requested_by: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrintJob(SQLModel, table=True):
    __tablename__ = "print_jobs"
    __table_args__ = (
        UniqueConstraint("batch_id", "copy_index", name="uq_print_jobs_batch_copy"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    # Null when the job was logged against an ad-hoc printer that isn't
    # registered in the vault; `printer_name` then carries the free-text label.
    printer_id: Optional[int] = Field(
        default=None, foreign_key="printers.id", index=True
    )
    printer_name: Optional[str] = Field(default=None, max_length=128)
    file_id: int = Field(foreign_key="files.id", index=True)
    model_id: int = Field(foreign_key="models.id", index=True)
    batch_id: Optional[int] = Field(
        default=None, foreign_key="print_batches.id", index=True
    )
    copy_index: Optional[int] = None

    remote_filename: str = Field(max_length=512)  # filename as uploaded to Moonraker
    state: PrintJobState = Field(default=PrintJobState.QUEUED, index=True)
    progress: float = Field(default=0.0)  # 0.0–1.0
    error: Optional[str] = Field(default=None, max_length=1024)
    routing_strategy: RoutingStrategy = Field(
        default=RoutingStrategy.MANUAL,
        sa_column=Column(
            SAEnum(RoutingStrategy), nullable=False, server_default="MANUAL", index=True
        ),
    )
    queue_position: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0", index=True),
    )
    priority: JobPriority = Field(
        default=JobPriority.NORMAL,
        sa_column=Column(
            SAEnum(JobPriority), nullable=False, server_default="NORMAL", index=True
        ),
    )
    target_group: Optional[str] = Field(default=None, max_length=128, index=True)
    compatibility_policy: CompatibilityPolicy = Field(
        default=CompatibilityPolicy.SAFE,
        sa_column=Column(
            SAEnum(CompatibilityPolicy),
            nullable=False,
            server_default="SAFE",
            index=True,
        ),
    )
    material_override_by: Optional[int] = Field(default=None, foreign_key="users.id")
    material_override_at: Optional[datetime] = None
    operator_gate_state: OperatorGateState = Field(
        default=OperatorGateState.NOT_REQUIRED,
        sa_column=Column(
            SAEnum(OperatorGateState),
            nullable=False,
            server_default="NOT_REQUIRED",
            index=True,
        ),
    )
    operator_decided_by: Optional[int] = Field(default=None, foreign_key="users.id")
    operator_decided_at: Optional[datetime] = None
    provider_job_id: Optional[str] = Field(default=None, max_length=255, index=True)
    blocked_reason: Optional[str] = Field(default=None, max_length=255)
    dispatch_claimed_at: Optional[datetime] = Field(default=None, index=True)
    dispatch_attempts: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    retryable: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0", index=True),
    )
    requested_by: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )

    # Distinguishes vault-initiated jobs from those detected on the printer.
    source: str = Field(default="vault", max_length=16)  # "vault" or "external"

    # Provider-side identity and evidence for prints started outside PrintStash.
    # These fields intentionally describe what the printer reported; they do
    # not pretend that slicer settings absent from MQTT can be reconstructed.
    external_display_name: Optional[str] = Field(default=None, max_length=512)
    external_task_id: Optional[str] = Field(default=None, max_length=255, index=True)
    external_subtask_id: Optional[str] = Field(default=None, max_length=255)
    external_project_id: Optional[str] = Field(default=None, max_length=255)
    external_profile_id: Optional[str] = Field(default=None, max_length=255)
    external_gcode_file: Optional[str] = Field(default=None, max_length=1024)
    external_plate_index: Optional[int] = None
    external_current_layer: Optional[int] = None
    external_total_layers: Optional[int] = None
    external_nozzle_diameter: Optional[float] = None
    artifact_evidence: str = Field(default="vault", max_length=32, index=True)
    artifact_capture_error: Optional[str] = Field(default=None, max_length=1024)

    # Measured outcome, captured from Moonraker when the print finishes.
    # filament in mm (raw from print_stats) and grams (derived when a matching
    # filament profile is known); duration in seconds. Null when unknown
    # (e.g. Bambu, which does not report live filament consumption).
    filament_used_mm: Optional[float] = None
    filament_used_g: Optional[float] = None
    actual_duration_s: Optional[int] = None

    # Resolved once at completion (`filament_cost_for_job`) and frozen from
    # then on — editing a filament profile's price afterwards does not
    # change historical cost. Populated by every write path that marks a job
    # COMPLETED; backfilled for pre-existing rows by migration 175be54ef975.
    cost: Optional[float] = None
    filament_g_effective: Optional[float] = None

    # Spoolman spool this print consumed, selected when starting/logging the
    # job. A soft reference (Spoolman owns the spool table) — not an FK. The
    # cached label keeps history readable if the spool is later renamed/archived
    # in Spoolman. On measured completion, this spool is decremented by
    # filament_used_g (when Spoolman write-back is enabled).
    spool_id: Optional[int] = Field(default=None, index=True)
    spool_name: Optional[str] = Field(default=None, max_length=256)
    # The Spoolman filament (type) the selected spool belongs to. Lets a print
    # resolve its synced FilamentProfile for exact cost and density/diameter,
    # without a live Spoolman call at the finishing tick.
    spool_filament_id: Optional[int] = Field(default=None, index=True)

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrinterMaintenanceWindow(SQLModel, table=True):
    __tablename__ = "printer_maintenance_windows"

    id: Optional[int] = Field(default=None, primary_key=True)
    printer_id: int = Field(foreign_key="printers.id", index=True)
    starts_at: datetime = Field(index=True)
    ends_at: datetime = Field(index=True)
    reason: Optional[str] = Field(default=None, max_length=512)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrinterMaintenanceLog(SQLModel, table=True):
    __tablename__ = "printer_maintenance_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    printer_id: int = Field(foreign_key="printers.id", index=True)
    performed_at: datetime = Field(default_factory=utcnow, index=True)
    category: str = Field(max_length=64, index=True)
    note: str = Field(max_length=4096)
    counter_value: Optional[float] = None
    counter_unit: Optional[str] = Field(default=None, max_length=32)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
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

    id: str = Field(primary_key=True, max_length=64)
    path: str = Field(unique=True, max_length=2048)
    owner_user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    background_job_id: str = Field(
        foreign_key="background_jobs.id", unique=True, index=True
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


class VaultAuditRun(SQLModel, table=True):
    __tablename__ = "vault_audit_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    requested_by: int = Field(foreign_key="users.id", index=True)
    mode: VaultAuditMode = Field(index=True)
    state: VaultAuditRunState = Field(default=VaultAuditRunState.PENDING, index=True)
    info_count: int = Field(default=0)
    warning_count: int = Field(default=0)
    critical_count: int = Field(default=0)
    progress: float = Field(default=0.0)
    current_phase: Optional[str] = Field(default=None, max_length=64)
    cancel_requested: bool = Field(default=False)
    error_code: Optional[str] = Field(default=None, max_length=128)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class VaultAuditFinding(SQLModel, table=True):
    __tablename__ = "vault_audit_findings"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="vault_audit_runs.id", index=True)
    code: str = Field(max_length=64, index=True)
    severity: VaultAuditSeverity = Field(index=True)
    resource_type: str = Field(max_length=64, index=True)
    resource_identifier: str = Field(max_length=255)
    repair_action: Optional[str] = Field(default=None, max_length=64)
    state: VaultAuditFindingState = Field(
        default=VaultAuditFindingState.OPEN, index=True
    )
    details_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[int] = Field(default=None, foreign_key="users.id")


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
        default=None, foreign_key="background_jobs.id", index=True
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


class PrinterFile(SQLModel, table=True):
    __tablename__ = "printer_files"

    id: Optional[int] = Field(default=None, primary_key=True)
    printer_id: int = Field(foreign_key="printers.id", index=True)
    file_id: Optional[int] = Field(default=None, foreign_key="files.id", index=True)

    remote_filename: str = Field(max_length=512)
    size_bytes: Optional[int] = None
    sha256: Optional[str] = Field(default=None, max_length=64, index=True)
    matched_by: str = Field(default="external", max_length=32, index=True)
    modified_at: Optional[datetime] = None
    last_seen_at: datetime = Field(default_factory=utcnow, index=True)
    missing_since: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ShareLink(SQLModel, table=True):
    """A public, expiring, read-only capability to view a single Model.

    Possession of the (unguessable) token grants access to exactly one model —
    never the rest of the vault, never any mutation. Only the SHA-256 of the
    token is stored; the raw token is shown once at creation.
    """

    __tablename__ = "share_links"

    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: int = Field(foreign_key="models.id", index=True)
    token_hash: str = Field(max_length=64, unique=True, index=True)

    expires_at: datetime = Field(index=True)
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    # When false, the public viewer can render the model but not download the
    # original source files (a tessellated mesh is still served for viewing).
    allow_download: bool = Field(default=False)
    selected_file_ids_json: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    access_count: int = Field(default=0)

    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    actor_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    action: str = Field(max_length=32, index=True)
    resource_type: str = Field(max_length=64, index=True)
    resource_id: Optional[int] = Field(default=None, index=True)
    diff_json: str = Field(default="{}")
    ip: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class NotificationChannel(SQLModel, table=True):
    """A configured outbound notification target (superuser-managed).

    Secret-bearing fields live in ``config_json`` (e.g. Telegram bot token,
    webhook URLs); the API masks them on read, mirroring the S3/MakerWorld
    secret handling. ``events_json`` and ``printer_ids_json`` hold the
    per-event and per-printer subscription filters as JSON arrays of strings;
    a null/empty ``printer_ids_json`` means "all printers".
    """

    __tablename__ = "notification_channels"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    target: NotificationTarget = Field(index=True)
    enabled: bool = Field(default=True, index=True)

    # Target-specific connection config (url, bot_token, chat_id, topic, ...).
    config_json: str = Field(
        default="{}", sa_column=Column(EncryptedText(), nullable=False)
    )
    # JSON array of NotificationEventType values this channel subscribes to.
    events_json: str = Field(default="[]", sa_column=Column(Text))
    # JSON array of printer ids; null = all printers.
    printer_ids_json: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # Visible "last notification" status surfaced in the UI.
    last_status: Optional[str] = Field(default=None, max_length=16)
    last_error: Optional[str] = Field(default=None, max_length=1024)
    last_delivered_at: Optional[datetime] = Field(default=None)

    # Circuit breaker: consecutive permanently-failed deliveries. Reset to 0 on
    # any success; once it crosses the threshold the channel is auto-disabled so
    # a dead endpoint stops generating failures for every event.
    consecutive_failures: int = Field(default=0)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class NotificationDelivery(SQLModel, table=True):
    """Outbox / work-queue row: one per (event x matching channel).

    Rows are inserted in the *same transaction* as the print-job/printer-status
    change that produced the event (transactional outbox), so events are never
    lost and — because emission is edge-triggered — never duplicated. A
    background dispatcher polls ``status in (pending)`` with
    ``next_retry_at <= now`` and delivers them with exponential backoff.

    ``context_json`` snapshots the rendered event context at enqueue time so a
    later channel-config edit can't retroactively change a queued payload.
    """

    __tablename__ = "notification_deliveries"

    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="notification_channels.id", index=True)
    event_type: NotificationEventType = Field(index=True)
    printer_id: Optional[int] = Field(
        default=None, foreign_key="printers.id", index=True
    )
    print_job_id: Optional[int] = Field(
        default=None, foreign_key="print_jobs.id", index=True
    )

    context_json: str = Field(default="{}", sa_column=Column(Text))

    status: NotificationDeliveryStatus = Field(
        default=NotificationDeliveryStatus.PENDING, index=True
    )
    attempts: int = Field(default=0)
    last_error: Optional[str] = Field(default=None, max_length=1024)
    next_retry_at: datetime = Field(default_factory=utcnow, index=True)
    delivered_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


# Sentinel hashes for external (non-vault) print jobs.
SENTINEL_MODEL_HASH = "ext-model-sentinel-000000000000000000000000000000000000000000"
SENTINEL_FILE_HASH = "ext-file-sentinel-0000000000000000000000000000000000000000000"
