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

from sqlmodel import Field, Relationship, SQLModel

from app.core.time import utcnow


class FileType(str, Enum):
    STL = "stl"
    THREE_MF = "3mf"
    GCODE = "gcode"
    OBJ = "obj"


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
    ".gcode": FileType.GCODE,
    ".g": FileType.GCODE,
    ".gco": FileType.GCODE,
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


class PrintJobState(str, Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    STARTED = "started"
    PRINTING = "printing"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


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


class FilamentProfile(SQLModel, table=True):
    """Local slicer filament preset with cost data for per-part estimates."""

    __tablename__ = "filament_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128, unique=True, index=True)
    material_type: Optional[str] = Field(default=None, max_length=64, index=True)
    material_brand: Optional[str] = Field(default=None, max_length=128, index=True)
    cost_per_kg: Optional[float] = None
    notes: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class File(SQLModel, table=True):
    """Physical artifact stored on disk; many-to-one with Model."""

    __tablename__ = "files"

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

    uploaded_at: datetime = Field(default_factory=utcnow)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")

    model: Optional["Model"] = Relationship(
        back_populates="files",
        sa_relationship_kwargs={"foreign_keys": "File.model_id"},
    )
    file_metadata: Optional[Metadata] = Relationship(
        back_populates="file",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"},
    )


# ---------------------------------------------------------------------------
# Categories (hierarchical) & Tags (flat, many-to-many)
# ---------------------------------------------------------------------------


class Category(SQLModel, table=True):
    """Hierarchical category. Self-referential via parent_id.

    `path` is the materialised slash-joined slug chain ("functional/brackets"),
    used for fast filtering and stable URLs.
    """

    __tablename__ = "categories"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    slug: str = Field(max_length=128, index=True)
    parent_id: Optional[int] = Field(
        default=None, foreign_key="categories.id", index=True
    )
    path: str = Field(max_length=512, unique=True, index=True)

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


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


class Model(SQLModel, table=True):
    """Logical asset, deduplicated by `hash` (source mesh sha256, gcode fallback)."""

    __tablename__ = "models"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=255)
    slug: str = Field(index=True, unique=True, max_length=255)
    hash: str = Field(index=True, unique=True, max_length=64)

    category_id: Optional[int] = Field(
        default=None, foreign_key="categories.id", index=True
    )
    description: Optional[str] = None
    thumbnail_path: Optional[str] = Field(default=None, max_length=512)
    thumbnail_file_id: Optional[int] = Field(default=None, foreign_key="files.id")

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

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
    category_rel: Optional["Category"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "Model.category_id == Category.id",
            "lazy": "selectin",
        },
    )


# ---------------------------------------------------------------------------
# Printers & Print Jobs (Stage 3 — Klipper / Moonraker integration)
# ---------------------------------------------------------------------------


class Printer(SQLModel, table=True):
    __tablename__ = "printers"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    provider: PrinterProvider = Field(
        default=PrinterProvider.MOONRAKER,
        index=True,
    )
    # Base URL of Moonraker, e.g. "http://mainsailos.local" or "http://10.0.0.42:7125".
    moonraker_url: str = Field(default="", max_length=512)
    api_key: Optional[str] = Field(default=None, max_length=128)
    bambu_host: Optional[str] = Field(default=None, max_length=255)
    bambu_serial: Optional[str] = Field(default=None, max_length=128)
    bambu_access_code: Optional[str] = Field(default=None, max_length=128)
    notes: Optional[str] = None
    group: Optional[str] = Field(default=None, max_length=128, index=True)

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


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(max_length=128, unique=True, index=True)
    email: Optional[str] = Field(default=None, max_length=255)
    hashed_password: str = Field(max_length=255)
    is_superuser: bool = Field(default=False)
    is_active: bool = Field(default=True)

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

    # S3 / R2 settings
    s3_bucket: Optional[str] = Field(default=None, max_length=256)
    s3_endpoint_url: Optional[str] = Field(default=None, max_length=512)
    s3_region: Optional[str] = Field(default=None, max_length=128)
    s3_access_key: Optional[str] = Field(default=None, max_length=256)
    s3_secret_key: Optional[str] = Field(default=None, max_length=512)

    # Backup
    backup_retention_days: Optional[int] = Field(default=None)

    # Backup S3 destination (separate from vault S3 — allows local vault + cloud backups)
    backup_s3_bucket: Optional[str] = Field(default=None, max_length=256)
    backup_s3_endpoint_url: Optional[str] = Field(default=None, max_length=512)
    backup_s3_region: Optional[str] = Field(default=None, max_length=128)
    backup_s3_access_key: Optional[str] = Field(default=None, max_length=256)
    backup_s3_secret_key: Optional[str] = Field(default=None, max_length=512)

    configured_at: Optional[datetime] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrintJob(SQLModel, table=True):
    __tablename__ = "print_jobs"

    id: Optional[int] = Field(default=None, primary_key=True)
    printer_id: int = Field(foreign_key="printers.id", index=True)
    file_id: int = Field(foreign_key="files.id", index=True)
    model_id: int = Field(foreign_key="models.id", index=True)

    remote_filename: str = Field(max_length=512)  # filename as uploaded to Moonraker
    state: PrintJobState = Field(default=PrintJobState.QUEUED, index=True)
    progress: float = Field(default=0.0)  # 0.0–1.0
    error: Optional[str] = Field(default=None, max_length=1024)

    # Distinguishes vault-initiated jobs from those detected on the printer.
    source: str = Field(default="vault", max_length=16)  # "vault" or "external"

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


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


# Sentinel hashes for external (non-vault) print jobs.
SENTINEL_MODEL_HASH = "ext-model-sentinel-000000000000000000000000000000000000000000"
SENTINEL_FILE_HASH = "ext-file-sentinel-0000000000000000000000000000000000000000000"
