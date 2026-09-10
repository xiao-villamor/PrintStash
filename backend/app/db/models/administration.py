"""Dynamic system settings, operational audit runs and audit findings."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Integer,
    String,
    Text,
    text,
)
from sqlmodel import Field

from app.core.time import utcnow
from app.db.encrypted import EncryptedText

from .base import SQLModel
from .types import (
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRunState,
    VaultAuditSeverity,
)


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

    # Random installation identity used to bind managed filesystem roots to
    # this database. It is generated once and never derived from a path.
    storage_identity: Optional[str] = Field(default=None, max_length=64, index=True)

    artifact_cache_policy_json: Optional[str] = Field(default=None)
    similarity_settings_json: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # Local storage paths (overridden at runtime)
    data_dir: Optional[str] = Field(default=None, max_length=1024)
    thumb_dir: Optional[str] = Field(default=None, max_length=1024)

    # Storage backend: "local" or "s3"
    storage_backend: Optional[str] = Field(default=None, max_length=64)

    # Compatibility namespace for legacy S3 storage.  v0.12 installations did
    # not persist this value and always used the literal ``vault-data`` root;
    # the upgrade migration backfills that value so an env change cannot point
    # existing keys at a different prefix.  Typed providers keep their root in
    # ``storage_provider_config_json`` instead.
    s3_root: Optional[str] = Field(default=None, max_length=1024)

    # Typed provider configuration. Non-secret and secret JSON are split so
    # sanitized reads never need to deserialize plaintext credentials.
    storage_provider: Optional[str] = Field(default=None, max_length=64)
    storage_provider_config_json: Optional[str] = Field(default=None)
    storage_provider_secret_json: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )

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
    automatic_backups_enabled: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0"),
    )
    automatic_backup_time_utc: str = Field(
        default="02:00",
        sa_column=Column(String(5), nullable=False, server_default="02:00"),
    )
    automatic_backup_last_attempt_at: Optional[datetime] = None
    manual_local_backup_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    automatic_local_backup_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    storage_min_free_bytes: Optional[int] = Field(default=None)
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
    setup_storage_pending: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="0")
    )

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class VaultAuditPolicy(SQLModel, table=True):
    """One opt-in calendar policy per audit mode; UTC instants, IANA wall clock."""

    __tablename__ = "vault_audit_policies"

    mode: str = Field(primary_key=True, max_length=16)
    enabled: bool = Field(default=False)
    paused: bool = Field(default=False)
    cadence: str = Field(default="weekly", max_length=16)
    timezone: str = Field(default="UTC", max_length=64)
    weekday: int = Field(default=6)
    month_day: int = Field(default=1)
    start_time: str = Field(default="02:00", max_length=5)
    window_minutes: int = Field(default=120)
    jitter_seconds: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    max_lateness_minutes: int = Field(
        default=120, sa_column=Column(Integer, nullable=False, server_default="120")
    )
    notification_threshold: str = Field(
        default="warning",
        sa_column=Column(String(16), nullable=False, server_default="warning"),
    )
    notification_channels_json: str = Field(
        default="[]", sa_column=Column(Text, nullable=False, server_default="[]")
    )
    notification_cooldown_minutes: int = Field(
        default=60, sa_column=Column(Integer, nullable=False, server_default="60")
    )
    last_notified_at: Optional[datetime] = None
    retry_after: Optional[datetime] = None
    launch_failures: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    bytes_per_second: int = Field(default=10485760)
    read_concurrency: int = Field(default=1)
    auto_repair: bool = Field(default=False)
    repair_actions_json: str = Field(
        default="[]", sa_column=Column(Text, nullable=False)
    )
    full_cost_acknowledged: bool = Field(default=False)
    requested_by: Optional[int] = Field(default=None, foreign_key="users.id")
    revision: int = Field(default=1)
    next_due_at: Optional[datetime] = Field(default=None, index=True)
    last_attempt_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    deferred_reason: Optional[str] = Field(default=None, max_length=64)
    updated_at: datetime = Field(default_factory=utcnow)


class VaultAuditEvent(SQLModel, table=True):
    """Durable storage event; safe aggregate payload, independent of delivery."""

    __tablename__ = "vault_audit_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(
        default=None, foreign_key="vault_audit_runs.id", index=True
    )
    dedup_key: str = Field(max_length=128, unique=True)
    event_type: str = Field(max_length=32)
    summary_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class VaultAuditRun(SQLModel, table=True):
    __tablename__ = "vault_audit_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    requested_by: int = Field(foreign_key="users.id", index=True)
    mode: VaultAuditMode = Field(index=True)
    state: VaultAuditRunState = Field(default=VaultAuditRunState.PENDING, index=True)
    # A nullable unique claim serializes manual and scheduled admission on both DBs.
    active_slot: Optional[str] = Field(default=None, max_length=16, unique=True)
    trigger: str = Field(
        default="manual",
        sa_column=Column(String(16), nullable=False, server_default="manual"),
    )
    trigger_key: Optional[str] = Field(default=None, max_length=128, unique=True)
    policy_revision: Optional[int] = None
    scheduled_for: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    bytes_per_second: Optional[int] = None
    planned_bytes: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )
    bytes_read: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )
    storage_generation: str = Field(
        default="",
        sa_column=Column(String(64), nullable=False, server_default=text("''")),
    )
    scope: str = Field(
        default="vault",
        sa_column=Column(String(32), nullable=False, server_default="vault"),
    )
    baseline_run_id: Optional[int] = None
    regression_json: str = Field(
        default="{}", sa_column=Column(Text, nullable=False, server_default="{}")
    )
    result_recorded: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="0")
    )
    repair_actions_json: str = Field(
        default="[]", sa_column=Column(Text, nullable=False, server_default="[]")
    )
    info_count: int = Field(default=0)
    warning_count: int = Field(default=0)
    critical_count: int = Field(default=0)
    unclaimed_bytes: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default="0"),
    )
    unclaimed_unknown_size_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
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
    run_id: int = Field(
        foreign_key="vault_audit_runs.id", index=True, ondelete="CASCADE"
    )
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
