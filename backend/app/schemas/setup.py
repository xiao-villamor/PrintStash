"""Pydantic DTOs for the first-run setup wizard."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Optional

from printstash_core.mesh.preview_profile import PreviewLook
from pydantic import BaseModel, ConfigDict, Field, StrictBool

UnavailableReason = Literal[
    # Registration is off (VAULT_SETUP_MODE=disabled).
    "disabled",
    # The host is not recognised as part of a private network.
    "untrusted_host",
    # VAULT_SETUP_MODE=environment: the owner comes from the deployment.
    "environment",
    # VAULT_SETUP_MODE and VAULT_SETUP_ADMIN_* contradict each other.
    "admin_credentials_missing",
    "admin_credentials_invalid",
    "admin_credentials_without_environment_mode",
]


class SetupPreviewAppearance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_look: PreviewLook
    preview_floor_grid: StrictBool


class SetupPreviewMode(StrEnum):
    SINGLE = "single"
    OBLIQUE = "oblique"
    OBLIQUE_POLES = "oblique-poles"


class SetupStatus(BaseModel):
    """Reported by ``GET /api/v1/setup/status``.

    ``configured`` is the bool the frontend gates on. The rest are read-only
    hints for the wizard UI so it can pre-fill sensible defaults.
    """

    configured: bool
    setup_available: bool = False
    recovery_required: bool = False
    # An owner exists (provisioned from VAULT_SETUP_ADMIN_*) but nobody has
    # chosen storage yet; the signed-in owner finishes it in the browser.
    storage_choice_required: bool = False
    # Why browser registration is unavailable on an unconfigured install, the
    # variables at fault when the first-run settings contradict each other (names
    # only), and the host the caller used, so the page can say which way out
    # applies.
    unavailable_reason: Optional[UnavailableReason] = None
    unavailable_variables: Optional[list[str]] = None
    observed_host: Optional[str] = None
    user_count: int = 0
    default_data_dir: Optional[str] = None
    default_thumb_dir: Optional[str] = None
    current_data_dir: Optional[str] = None
    current_thumb_dir: Optional[str] = None
    current_storage_backend: Optional[str] = None
    current_storage_provider: Optional[str] = None
    current_storage_provider_config: Optional[dict[str, object]] = None
    current_s3_bucket: Optional[str] = None
    current_s3_endpoint_url: Optional[str] = None
    current_s3_region: Optional[str] = None
    current_backup_retention_days: Optional[int] = None
    current_backup_s3_bucket: Optional[str] = None
    current_backup_s3_endpoint_url: Optional[str] = None
    current_backup_s3_region: Optional[str] = None
    preview_appearance: SetupPreviewAppearance | None = None
    preview_mode: SetupPreviewMode | None = None
    configured_at: Optional[datetime] = None


class SetupStorageRequest(BaseModel):
    """Payload for ``POST /api/v1/setup`` — only accepted while unconfigured."""

    model_config = ConfigDict(extra="forbid")

    storage_backend: Optional[str] = Field(default=None, max_length=64)
    storage_provider: Optional[str] = Field(default=None, max_length=64)
    storage_provider_config: Optional[dict[str, Any]] = None
    data_dir: Optional[str] = Field(default=None, max_length=1024)
    thumb_dir: Optional[str] = Field(default=None, max_length=1024)
    s3_bucket: Optional[str] = Field(default=None, max_length=256)
    s3_endpoint_url: Optional[str] = Field(default=None, max_length=512)
    s3_region: Optional[str] = Field(default=None, max_length=128)
    s3_access_key: Optional[str] = Field(default=None, max_length=256)
    s3_secret_key: Optional[str] = Field(default=None, max_length=512)
    backup_retention_days: Optional[int] = Field(default=None, ge=0)
    backup_s3_bucket: Optional[str] = Field(default=None, max_length=256)
    backup_s3_endpoint_url: Optional[str] = Field(default=None, max_length=512)
    backup_s3_region: Optional[str] = Field(default=None, max_length=128)
    backup_s3_access_key: Optional[str] = Field(default=None, max_length=256)
    backup_s3_secret_key: Optional[str] = Field(default=None, max_length=512)


class SetupRequest(SetupStorageRequest):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8, max_length=256)
    email: Optional[str] = Field(default=None, max_length=255)
    preview_appearance: SetupPreviewAppearance | None = None
    preview_mode: SetupPreviewMode | None = None


class SetupSessionResponse(BaseModel):
    csrf: str
    expires_in: int = 3600


class SetupStorageCheck(BaseModel):
    code: str
    free_bytes: Optional[int] = None


class SetupCheckResponse(BaseModel):
    ready: bool
    storage_provider: str
    checks: list[SetupStorageCheck]


class SetupResponse(BaseModel):
    """Returned on successful first-run completion."""

    configured: bool
    user_id: int
    username: str
    storage_backend: str = "local"
    storage_provider: str = "local"
    data_dir: str
    thumb_dir: str
    access_token: str
    token_type: str = "bearer"
    storage_ready: bool = True
