"""Runtime configuration endpoints — read & update storage/backup settings."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.core.security import require_superuser
from app.db.session import get_session
from app.services import runtime_config

router = APIRouter(prefix="/config", tags=["config"])


class VaultConfigRead(BaseModel):
    storage_backend: str = "local"
    data_dir: str = ""
    thumb_dir: str = ""
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    has_s3_access_key: bool = False
    has_s3_secret_key: bool = False
    backup_retention_days: int = 30
    backup_s3_bucket: str = ""
    backup_s3_endpoint_url: str = ""
    backup_s3_region: str = "auto"
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""
    has_backup_s3_access_key: bool = False
    has_backup_s3_secret_key: bool = False
    has_backup_s3: bool = False


class VaultConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_backend: Optional[str] = None
    data_dir: Optional[str] = None
    thumb_dir: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_endpoint_url: Optional[str] = None
    s3_region: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    backup_retention_days: Optional[int] = Field(default=None, ge=-1)
    backup_s3_bucket: Optional[str] = None
    backup_s3_endpoint_url: Optional[str] = None
    backup_s3_region: Optional[str] = None
    backup_s3_access_key: Optional[str] = None
    backup_s3_secret_key: Optional[str] = None


@router.get(
    "",
    summary="Get current vault configuration",
    description=(
        "Returns the effective configuration (env + DB overlay). "
        "Secret values are masked."
    ),
)
def get_config(
    _: object = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultConfigRead:
    cfg = runtime_config.get_effective_config(session)
    return VaultConfigRead(**cfg)


@router.put(
    "",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_superuser)],
    summary="Update vault configuration",
    description=(
        "Persists configuration overrides to the database and applies them "
        "immediately. Set a field to an empty string to clear the override "
        "(fall back to env/default). Changes to ``storage_backend`` or "
        "S3 credentials require a restart of the ``storage_backend`` "
        "singleton to take full effect for file operations."
    ),
)
def update_config(
    body: VaultConfigUpdate,
    session: Session = Depends(get_session),
) -> VaultConfigRead:
    if body.storage_backend is not None and body.storage_backend not in (
        "",
        "local",
        "s3",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="storage_backend must be 'local' or 's3'",
        )

    runtime_config.update_config(
        session,
        storage_backend=body.storage_backend,
        data_dir=body.data_dir,
        thumb_dir=body.thumb_dir,
        s3_bucket=body.s3_bucket,
        s3_endpoint_url=body.s3_endpoint_url,
        s3_region=body.s3_region,
        s3_access_key=body.s3_access_key,
        s3_secret_key=body.s3_secret_key,
        backup_retention_days=body.backup_retention_days,
        backup_s3_bucket=body.backup_s3_bucket,
        backup_s3_endpoint_url=body.backup_s3_endpoint_url,
        backup_s3_region=body.backup_s3_region,
        backup_s3_access_key=body.backup_s3_access_key,
        backup_s3_secret_key=body.backup_s3_secret_key,
    )

    cfg = runtime_config.get_effective_config(session)
    return VaultConfigRead(**cfg)
