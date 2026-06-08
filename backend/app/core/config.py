"""Process-wide configuration, sourced from ``VAULT_*`` environment variables.

Frozen env-only settings are wrapped by ``ConfigResolver`` which layers
runtime overrides (DB-backed) on top. See ADR-0002.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import make_url

# ---------------------------------------------------------------------------
# Shared overlay dict — written by runtime_config, read by ConfigResolver.
# Protected by _overlay_lock for writes; reads are GIL-safe dict lookups.
# ---------------------------------------------------------------------------

_overlay: dict[str, Any] = {}
_overlay_lock = asyncio.Lock()


class Settings(BaseSettings):
    """Frozen env-only settings. Never mutated after import.

    Runtime overrides live in the ``_overlay`` dict; the ``ConfigResolver``
    exposes the effective value (overlay wins, frozen falls back).
    """

    model_config = SettingsConfigDict(
        env_prefix="VAULT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    storage_backend: str = "local"
    data_dir: Path = Path("/data/files")
    thumb_dir: Path = Path("/data/thumbs")
    staging_dir: Path = Path("/data/staging")

    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_presigned_url_expire_seconds: int = 900
    s3_multipart_threshold_mb: int = 50
    s3_lifecycle_expiration_days: int = 0
    s3_lifecycle_transition_days: int = 0
    s3_transition_storage_class: str = "STANDARD_IA"

    db_url: str = "sqlite:////data/db/printstash.sqlite"

    jwt_secret: str = "changeme_jwt_secret_please_change"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    cors_origins: str = ""

    max_upload_mb: int = 512
    log_level: str = "INFO"

    backup_dir: Path = Path("/data/backups")
    backup_retention_days: int = 30
    trash_retention_days: int = 30

    backup_s3_bucket: str = ""
    backup_s3_endpoint_url: str = ""
    backup_s3_region: str = "auto"
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""

    app_name: str = "PrintStash"
    app_version: str = "0.1.0"

    @property
    def incoming_dir(self) -> Path:
        return self.staging_dir / "_incoming"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


class ConfigResolver:
    """Single read-path for effective configuration: overlay wins, frozen falls back.

    Wraps the frozen ``Settings`` and the shared ``_overlay`` dict so callers
    keep writing ``settings.data_dir`` — no migration churn at 16+ call sites.
    """

    __slots__ = ("_frozen",)

    def __init__(self, frozen: Settings) -> None:
        object.__setattr__(self, "_frozen", frozen)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        overlay_val = _overlay.get(name)
        if overlay_val is not None:
            return overlay_val
        return getattr(self._frozen, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("ConfigResolver is read-only — use overlay dict for mutations")

    @property
    def incoming_dir(self) -> Path:
        staging = _overlay.get("staging_dir", self._frozen.staging_dir)
        return staging / "_incoming"

    @property
    def max_upload_bytes(self) -> int:
        max_mb = _overlay.get("max_upload_mb", self._frozen.max_upload_mb)
        return max_mb * 1024 * 1024


# Public: same name, new type — transparent to all existing call sites.
settings = ConfigResolver(Settings())

# Expose frozen Settings class for introspection (defaults, model_fields).
FrozenSettings = Settings


def get_config() -> ConfigResolver:
    """Explicit accessor for the effective config resolver (alias for ``settings``)."""
    return settings


def _sqlite_db_path(db_url: str) -> Path | None:
    """Return the on-disk path for a sqlite URL, or ``None`` for in-memory/other."""
    if not db_url.startswith("sqlite"):
        return None
    database = make_url(db_url).database
    if not database or database == ":memory:":
        return None
    return Path(database)


def ensure_dirs() -> None:
    """Create required storage directories at startup. Idempotent."""
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)

    if settings.storage_backend == "local":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.thumb_dir.mkdir(parents=True, exist_ok=True)

    db_path = _sqlite_db_path(settings.db_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
