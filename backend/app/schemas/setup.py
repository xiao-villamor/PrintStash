"""Pydantic DTOs for the first-run setup wizard."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SetupStatus(BaseModel):
    """Reported by ``GET /api/v1/setup/status``.

    ``configured`` is the bool the frontend gates on. The rest are read-only
    hints for the wizard UI so it can pre-fill sensible defaults.
    """

    configured: bool
    user_count: int
    default_data_dir: str
    default_thumb_dir: str
    current_data_dir: str
    current_thumb_dir: str
    current_storage_backend: str = "local"
    current_s3_bucket: str = ""
    current_s3_endpoint_url: str = ""
    current_s3_region: str = "auto"
    current_backup_retention_days: int = 30
    current_backup_s3_bucket: str = ""
    current_backup_s3_endpoint_url: str = ""
    current_backup_s3_region: str = "auto"
    configured_at: Optional[datetime] = None


class SetupRequest(BaseModel):
    """Payload for ``POST /api/v1/setup`` — only accepted while unconfigured."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8, max_length=256)
    email: Optional[str] = Field(default=None, max_length=255)
    storage_backend: Optional[str] = Field(default=None, max_length=64)
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


class SetupResponse(BaseModel):
    """Returned on successful first-run completion."""

    configured: bool
    user_id: int
    username: str
    storage_backend: str = "local"
    data_dir: str
    thumb_dir: str
    access_token: str
    token_type: str = "bearer"
