"""Runtime configuration endpoints — read & update storage/backup settings."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.core.security import require_superuser
from app.db.session import get_session
from app.services import runtime_config
from app.services.makerworld_auth import (
    MakerWorldAuthError,
    begin_login,
    submit_code,
)

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
    trash_retention_days: int = 30
    backup_s3_bucket: str = ""
    backup_s3_endpoint_url: str = ""
    backup_s3_region: str = "auto"
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""
    has_backup_s3_access_key: bool = False
    has_backup_s3_secret_key: bool = False
    has_backup_s3: bool = False
    auto_mark_known_good: bool = True
    external_libraries_enabled: bool = False
    currency: str = "USD"


class VaultConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_mark_known_good: Optional[bool] = None
    external_libraries_enabled: Optional[bool] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)

    storage_backend: Optional[str] = None
    data_dir: Optional[str] = None
    thumb_dir: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_endpoint_url: Optional[str] = None
    s3_region: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    backup_retention_days: Optional[int] = Field(default=None, ge=-1)
    trash_retention_days: Optional[int] = Field(default=None, ge=-1)
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


# --------------------------------------------------------------------------- #
# MakerWorld login (Bambu account) — obtains the download session token.
# --------------------------------------------------------------------------- #
class MakerWorldStatus(BaseModel):
    connected: bool = False
    updated_at: Optional[str] = None


class MakerWorldLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str = Field(min_length=1, description="MakerWorld / Bambu account email")
    password: str = Field(min_length=1)


class MakerWorldVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_token: str = Field(min_length=1)
    code: str = Field(min_length=1, description="Emailed or authenticator code")


class MakerWorldLoginResponse(BaseModel):
    # "ok" (connected), "need_email_code", or "need_tfa_code".
    status: str
    login_token: Optional[str] = None
    connected: bool = False


class MakerWorldTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The raw MakerWorld ``token`` cookie value (a Bambu JWT), pasted from a
    # browser already logged in — the escape hatch for Google-SSO accounts and
    # any case where password login won't work.
    token: str = Field(min_length=1)


@router.get(
    "/makerworld",
    dependencies=[Depends(require_superuser)],
    summary="MakerWorld connection status",
)
def makerworld_status(session: Session = Depends(get_session)) -> MakerWorldStatus:
    return MakerWorldStatus(**runtime_config.makerworld_status(session))


@router.post(
    "/makerworld/login",
    dependencies=[Depends(require_superuser)],
    summary="Start MakerWorld login",
    description=(
        "Submit MakerWorld (Bambu) email + password. Bambu usually emails a "
        "verification code, so the response is typically ``need_email_code`` with "
        "a ``login_token`` to pass to /makerworld/verify. The password is never "
        "stored — only the resulting session token is, on success."
    ),
)
async def makerworld_login(
    body: MakerWorldLoginRequest,
    session: Session = Depends(get_session),
) -> MakerWorldLoginResponse:
    try:
        result = await begin_login(body.account, body.password)
    except MakerWorldAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.code)
    if result.status == "ok" and result.token:
        runtime_config.set_makerworld_token(session, result.token)
        return MakerWorldLoginResponse(status="ok", connected=True)
    return MakerWorldLoginResponse(status=result.status, login_token=result.login_token)


@router.post(
    "/makerworld/verify",
    dependencies=[Depends(require_superuser)],
    summary="Complete MakerWorld login with a verification code",
)
async def makerworld_verify(
    body: MakerWorldVerifyRequest,
    session: Session = Depends(get_session),
) -> MakerWorldLoginResponse:
    try:
        result = await submit_code(body.login_token, body.code)
    except MakerWorldAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.code)
    if not result.token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_code")
    runtime_config.set_makerworld_token(session, result.token)
    return MakerWorldLoginResponse(status="ok", connected=True)


@router.post(
    "/makerworld/token",
    dependencies=[Depends(require_superuser)],
    summary="Connect MakerWorld with a pasted session token",
    description=(
        "Store a MakerWorld session token directly (the ``token`` cookie value "
        "copied from a logged-in browser). Use this for Google-SSO accounts, "
        "which have no password to log in with."
    ),
)
def makerworld_set_token(
    body: MakerWorldTokenRequest,
    session: Session = Depends(get_session),
) -> MakerWorldStatus:
    token = body.token.strip()
    # Be forgiving: accept a full ``token=<jwt>`` (or ``token=<jwt>; other=…``)
    # cookie header pasted as-is, not just the bare value.
    if token.lower().startswith("token="):
        token = token.split("=", 1)[1].split(";", 1)[0].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing_token")
    runtime_config.set_makerworld_token(session, token)
    return MakerWorldStatus(**runtime_config.makerworld_status(session))


@router.delete(
    "/makerworld",
    dependencies=[Depends(require_superuser)],
    summary="Disconnect MakerWorld (clear the stored token)",
)
def makerworld_disconnect(session: Session = Depends(get_session)) -> MakerWorldStatus:
    runtime_config.clear_makerworld_token(session)
    return MakerWorldStatus(**runtime_config.makerworld_status(session))


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

    if body.auto_mark_known_good is not None:
        runtime_config.set_auto_mark_known_good(session, body.auto_mark_known_good)

    if body.external_libraries_enabled is not None:
        runtime_config.set_external_libraries_enabled(
            session, body.external_libraries_enabled
        )

    if body.currency is not None:
        runtime_config.set_currency(session, body.currency)

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
        trash_retention_days=body.trash_retention_days,
        backup_s3_bucket=body.backup_s3_bucket,
        backup_s3_endpoint_url=body.backup_s3_endpoint_url,
        backup_s3_region=body.backup_s3_region,
        backup_s3_access_key=body.backup_s3_access_key,
        backup_s3_secret_key=body.backup_s3_secret_key,
    )

    cfg = runtime_config.get_effective_config(session)
    return VaultConfigRead(**cfg)
