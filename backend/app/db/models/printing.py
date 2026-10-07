"""Printers, materials, print history and multipart manufacturing records."""

from datetime import datetime
from typing import ClassVar, Optional
from uuid import uuid4

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
from sqlalchemy.orm import Mapped, column_property
from sqlmodel import Field, select

from app.core.time import utcnow
from app.db.encrypted import EncryptedText

from .base import SQLModel
from .library import LibraryRevision
from .types import (
    CompatibilityPolicy,
    JobPriority,
    MaterialSlotState,
    MaterialSource,
    OperatorGateState,
    PrinterProvider,
    PrinterStatus,
    PrintJobState,
    RoutingStrategy,
)


class FilamentProfile(SQLModel, table=True):
    """Local slicer filament preset with cost data for per-part estimates."""

    __tablename__ = "filament_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    edit_version: int = Field(
        default=1, sa_column=Column(BigInteger, nullable=False, server_default="1")
    )
    # A hard-deleted preset ID may be reused by SQLite. Its editing identity may not.
    edit_identity: str = Field(default_factory=lambda: uuid4().hex, max_length=32)
    database_epoch: ClassVar[Mapped[str]]

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
    edit_version: int = Field(
        default=1, sa_column=Column(BigInteger, nullable=False, server_default="1")
    )
    # A hard-deleted preset ID may be reused by SQLite. Its editing identity may not.
    edit_identity: str = Field(default_factory=lambda: uuid4().hex, max_length=32)
    database_epoch: ClassVar[Mapped[str]]

    name: str = Field(max_length=128, unique=True, index=True)
    printer_model: Optional[str] = Field(default=None, max_length=128, index=True)
    slicer_name: Optional[str] = Field(default=None, max_length=64, index=True)
    nozzle_diameter_mm: Optional[float] = None
    notes: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


for _profile in (FilamentProfile, PrinterProfile):
    _profile.database_epoch = column_property(
        select(LibraryRevision.epoch).where(LibraryRevision.id == 1).scalar_subquery()
    )


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
    edit_version: int = Field(
        default=1, sa_column=Column(BigInteger, nullable=False, server_default="1")
    )
    edit_epoch: ClassVar[Mapped[str]]
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


# Read the settings version and database history together, without per-row queries.
Printer.edit_epoch = column_property(
    select(LibraryRevision.epoch).where(LibraryRevision.id == 1).scalar_subquery()
)


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
    # Stable, actionable capture outcome. ``artifact_capture_error`` remains
    # as the legacy short detail for existing clients; these fields separate a
    # machine-readable code from an operator-facing message.
    artifact_capture_error_code: Optional[str] = Field(default=None, max_length=128)
    artifact_capture_error_message: Optional[str] = Field(default=None, max_length=1024)

    # Rows absorbed by the Bambu identity repair remain for audit/rollback and
    # are excluded by the live() scope. The survivor is intentionally the
    # earliest row in the identity group.
    dedupe_absorbed_at: Optional[datetime] = Field(default=None, index=True)
    dedupe_survivor_id: Optional[int] = Field(default=None, index=True)

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


class MultipartBuild(SQLModel, table=True):
    """A frozen set of manufacturing requirements, independent of its composition."""

    __tablename__ = "multipart_builds"
    id: int | None = Field(default=None, primary_key=True)
    multipart_model_id: int | None = Field(
        default=None, foreign_key="multipart_models.id", ondelete="SET NULL"
    )
    composition_name: str = Field(max_length=255)
    # Historical permission boundary: deleting the collection must never turn
    # private manufacturing history into an uncollected, globally readable row.
    collection_id: int | None = Field(default=None, index=True)
    name: str = Field(max_length=255)
    object_quantity: int = Field(default=1)
    version: int = Field(default=0)
    archived_at: datetime | None = Field(default=None)
    created_by: int | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MultipartBuildPart(SQLModel, table=True):
    __tablename__ = "multipart_build_parts"
    id: int | None = Field(default=None, primary_key=True)
    build_id: int = Field(
        foreign_key="multipart_builds.id", ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=128)
    sort_order: int = Field(default=0)
    quantity: int
    required_units: int
    # Choice IDs and Model IDs are historical values, not cascading ownership.
    choices_json: str = Field(sa_column=Column(Text, nullable=False))
    selected_model_id: int | None = Field(default=None)
    selected_choice_id: int | None = Field(default=None)
    revision_id: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MultipartBuildAttempt(SQLModel, table=True):
    __tablename__ = "multipart_build_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_multipart_build_attempt_job"),
    )
    id: int | None = Field(default=None, primary_key=True)
    part_id: int = Field(
        foreign_key="multipart_build_parts.id", ondelete="CASCADE", index=True
    )
    job_id: int | None = Field(
        default=None, foreign_key="print_jobs.id", ondelete="SET NULL"
    )
    historical_job_id: int
    model_id: int
    revision_id: int
    planned_units: int
    valid_units: int | None = Field(default=None)
    version: int = Field(default=0)
    confirmed_by: int | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL"
    )
    confirmed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MultipartBuildConfirmation(SQLModel, table=True):
    """Durable idempotency receipts survive subsequent result corrections."""

    __tablename__ = "multipart_build_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "idempotency_key", name="uq_build_confirmation_key"
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    attempt_id: int = Field(
        foreign_key="multipart_build_attempts.id", ondelete="CASCADE", index=True
    )
    idempotency_key: str = Field(max_length=128)
    requested_version: int
    valid_units: int
    created_at: datetime = Field(default_factory=utcnow)
