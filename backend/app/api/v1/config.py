"""Runtime configuration endpoints — read & update storage/backup settings."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.core.config import settings
from app.core.security import require_superuser
from app.db.session import get_session
from app.services import runtime_config
from app.services.storage_backend import get_backend
from app.services.storage_operations import serialize_operations, vault_operations

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
    automatic_backups_enabled: bool = False
    automatic_backup_time_utc: str = "02:00"
    automatic_backup_last_attempt_at: datetime | None = None
    manual_local_backup_enabled: bool = True
    automatic_local_backup_enabled: bool = True
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
    model_thumbnail_width: int = 640
    oidc_enabled: bool = False
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    has_oidc_client_secret: bool = False
    oidc_scopes: str = "openid profile email groups"
    oidc_username_claim: str = "preferred_username"
    oidc_groups_claim: str = "groups"
    oidc_admin_groups: str = "printstash-admins"
    oidc_display_name: str = "Single sign-on"
    oidc_redirect_uri: str = ""
    oidc_allow_insecure_http: bool = False
    storage_tier: str = "unguarded"
    storage_capabilities: dict[str, object] = Field(default_factory=dict)
    storage_operations: dict[str, object] = Field(default_factory=dict)
    storage_warnings: list[str] = Field(default_factory=list)
    storage_probe_diagnostics: dict[str, object] = Field(default_factory=dict)
    storage_unverified_acknowledged: bool = False
    storage_provider: str = ""
    storage_provider_config: dict[str, object] = Field(default_factory=dict)


class VaultConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_mark_known_good: Optional[bool] = None
    external_libraries_enabled: Optional[bool] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    model_thumbnail_width: Optional[Literal[320, 640, 1280]] = None
    oidc_enabled: Optional[bool] = None
    oidc_issuer_url: Optional[str] = Field(default=None, max_length=512)
    oidc_client_id: Optional[str] = Field(default=None, max_length=255)
    oidc_client_secret: Optional[str] = Field(default=None, max_length=2048)
    oidc_scopes: Optional[str] = Field(default=None, max_length=512)
    oidc_username_claim: Optional[str] = Field(default=None, max_length=128)
    oidc_groups_claim: Optional[str] = Field(default=None, max_length=128)
    oidc_admin_groups: Optional[str] = Field(default=None, max_length=1024)
    oidc_display_name: Optional[str] = Field(default=None, max_length=128)
    oidc_redirect_uri: Optional[str] = Field(default=None, max_length=1024)
    oidc_allow_insecure_http: Optional[bool] = None

    storage_backend: Optional[str] = None
    storage_provider: Optional[str] = Field(default=None, max_length=64)
    storage_provider_config: Optional[dict[str, Any]] = None
    data_dir: Optional[str] = None
    thumb_dir: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_endpoint_url: Optional[str] = None
    s3_region: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    backup_retention_days: Optional[int] = Field(default=None, ge=-1)
    automatic_backups_enabled: Optional[bool] = None
    automatic_backup_time_utc: Optional[str] = Field(
        default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    manual_local_backup_enabled: Optional[bool] = None
    automatic_local_backup_enabled: Optional[bool] = None
    trash_retention_days: Optional[int] = Field(default=None, ge=-1)
    backup_s3_bucket: Optional[str] = None
    backup_s3_endpoint_url: Optional[str] = None
    backup_s3_region: Optional[str] = None
    backup_s3_access_key: Optional[str] = None
    backup_s3_secret_key: Optional[str] = None


class StorageRootEnrollment(BaseModel):
    """Explicit acknowledgement for enrolling an unprovable local root."""

    model_config = ConfigDict(extra="forbid")
    role: Literal["data", "thumb"]
    confirm: bool = False


@router.post(
    "/storage-roots/enroll",
    summary="Enroll a local storage root after an explicit confirmation",
    dependencies=[Depends(require_superuser)],
)
def enroll_storage_root(
    body: StorageRootEnrollment,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if not body.confirm:
        raise HTTPException(
            status_code=400, detail="storage_root_confirmation_required"
        )
    if settings.storage_backend != "local":
        raise HTTPException(status_code=409, detail="storage_backend_not_local")
    from app.services.storage_backend import enroll_legacy_local_root

    identity = runtime_config.ensure_storage_identity(session)
    root = settings.data_dir if body.role == "data" else settings.thumb_dir
    enrolled = enroll_legacy_local_root(
        root,
        role=body.role,
        installation=identity,
        proofs=[],
        allow_empty=True,
    )
    if not enrolled:
        raise HTTPException(status_code=409, detail="storage_root_enrollment_failed")
    return {"enrolled": True, "role": body.role, "restart_required": True}


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
    provider_config = runtime_config.get_sanitized_storage_provider(session)
    if provider_config is not None:
        cfg["storage_provider"], cfg["storage_provider_config"] = provider_config
    backend = get_backend()
    cfg.update(
        storage_tier=backend.capabilities.tier.value,
        storage_capabilities=backend.capabilities.as_dict(),
        storage_operations=serialize_operations(vault_operations(backend.capabilities)),
        storage_warnings=list(backend.capabilities.warnings),
        storage_probe_diagnostics=backend.probe_diagnostics,
        storage_unverified_acknowledged=bool(settings.storage_allow_unverified),
    )
    return VaultConfigRead(**cfg)


# --------------------------------------------------------------------------- #
# Legacy MakerWorld connection contract. Imports now use the browser extension;
# routes remain present so existing clients receive an actionable response.
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
    del session
    return MakerWorldStatus(connected=False)


def _makerworld_extension_only() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="makerworld_extension_required",
    )


@router.post(
    "/makerworld/login",
    dependencies=[Depends(require_superuser)],
    summary="Legacy MakerWorld login endpoint",
    description=(
        "Retained for client compatibility. MakerWorld imports now require the "
        "browser extension and this endpoint returns HTTP 410."
    ),
)
async def makerworld_login(
    body: MakerWorldLoginRequest,
    session: Session = Depends(get_session),
) -> MakerWorldLoginResponse:
    del body, session
    _makerworld_extension_only()


@router.post(
    "/makerworld/verify",
    dependencies=[Depends(require_superuser)],
    summary="Legacy MakerWorld verification endpoint",
)
async def makerworld_verify(
    body: MakerWorldVerifyRequest,
    session: Session = Depends(get_session),
) -> MakerWorldLoginResponse:
    del body, session
    _makerworld_extension_only()


@router.post(
    "/makerworld/token",
    dependencies=[Depends(require_superuser)],
    summary="Legacy MakerWorld token endpoint",
    description=(
        "Retained for client compatibility. MakerWorld imports now require the "
        "browser extension and this endpoint returns HTTP 410."
    ),
)
def makerworld_set_token(
    body: MakerWorldTokenRequest,
    session: Session = Depends(get_session),
) -> MakerWorldStatus:
    del body, session
    _makerworld_extension_only()


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
    legacy_storage_fields = {
        "storage_backend",
        "data_dir",
        "thumb_dir",
        "s3_bucket",
        "s3_endpoint_url",
        "s3_region",
        "s3_access_key",
        "s3_secret_key",
    }
    new_storage_supplied = (
        body.storage_provider is not None or body.storage_provider_config is not None
    )
    legacy_storage_supplied = bool(body.model_fields_set & legacy_storage_fields)
    if new_storage_supplied and legacy_storage_supplied:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="mixed_storage_provider_input",
        )
    if (body.storage_provider is None) != (body.storage_provider_config is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="storage_provider_and_config_required",
        )
    if body.storage_backend is not None and body.storage_backend not in (
        "",
        "local",
        "s3",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="storage_backend must be 'local' or 's3'",
        )

    try:
        namespace_change, requested_provider = (
            runtime_config.storage_namespace_change_requires_migration(
                session,
                storage_backend=body.storage_backend,
                data_dir=body.data_dir,
                thumb_dir=body.thumb_dir,
                s3_bucket=body.s3_bucket,
                s3_endpoint_url=body.s3_endpoint_url,
                s3_region=body.s3_region,
                storage_provider=body.storage_provider,
                storage_provider_config=body.storage_provider_config,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if namespace_change:
        # Runtime remapping would make row-derived keys point at a new root or
        # bucket. There is intentionally no in-place shortcut: a future storage
        # migration must copy, verify, and atomically switch every exact object.
        if runtime_config.is_configured(session) or runtime_config.has_storage_state(
            session
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="storage_migration_required",
            )

    if body.auto_mark_known_good is not None:
        runtime_config.set_auto_mark_known_good(session, body.auto_mark_known_good)

    if body.external_libraries_enabled is not None:
        runtime_config.set_external_libraries_enabled(
            session, body.external_libraries_enabled
        )

    if (
        body.automatic_backups_enabled is not None
        or body.automatic_backup_time_utc is not None
        or body.manual_local_backup_enabled is not None
        or body.automatic_local_backup_enabled is not None
    ):
        runtime_config.update_backup_schedule(
            session,
            enabled=body.automatic_backups_enabled,
            time_utc=body.automatic_backup_time_utc,
            manual_local_enabled=body.manual_local_backup_enabled,
            automatic_local_enabled=body.automatic_local_backup_enabled,
        )

    if body.currency is not None:
        runtime_config.set_currency(session, body.currency)

    if requested_provider is not None:
        runtime_config.update_storage_provider(
            session,
            provider=body.storage_provider or "",
            raw_config=body.storage_provider_config or {},
        )

    runtime_config.update_config(
        session,
        storage_backend=None if new_storage_supplied else body.storage_backend,
        data_dir=None if new_storage_supplied else body.data_dir,
        thumb_dir=None if new_storage_supplied else body.thumb_dir,
        s3_bucket=None if new_storage_supplied else body.s3_bucket,
        s3_endpoint_url=None if new_storage_supplied else body.s3_endpoint_url,
        s3_region=None if new_storage_supplied else body.s3_region,
        s3_access_key=None if new_storage_supplied else body.s3_access_key,
        s3_secret_key=None if new_storage_supplied else body.s3_secret_key,
        backup_retention_days=body.backup_retention_days,
        trash_retention_days=body.trash_retention_days,
        model_thumbnail_width=body.model_thumbnail_width,
        backup_s3_bucket=body.backup_s3_bucket,
        backup_s3_endpoint_url=body.backup_s3_endpoint_url,
        backup_s3_region=body.backup_s3_region,
        backup_s3_access_key=body.backup_s3_access_key,
        backup_s3_secret_key=body.backup_s3_secret_key,
        oidc_enabled=body.oidc_enabled,
        oidc_issuer_url=body.oidc_issuer_url,
        oidc_client_id=body.oidc_client_id,
        oidc_client_secret=body.oidc_client_secret,
        oidc_scopes=body.oidc_scopes,
        oidc_username_claim=body.oidc_username_claim,
        oidc_groups_claim=body.oidc_groups_claim,
        oidc_admin_groups=body.oidc_admin_groups,
        oidc_display_name=body.oidc_display_name,
        oidc_redirect_uri=body.oidc_redirect_uri,
        oidc_allow_insecure_http=body.oidc_allow_insecure_http,
    )

    cfg = runtime_config.get_effective_config(session)
    provider_config = runtime_config.get_sanitized_storage_provider(session)
    if provider_config is not None:
        cfg["storage_provider"], cfg["storage_provider_config"] = provider_config
    backend = get_backend()
    cfg.update(
        storage_tier=backend.capabilities.tier.value,
        storage_capabilities=backend.capabilities.as_dict(),
        storage_operations=serialize_operations(vault_operations(backend.capabilities)),
        storage_warnings=list(backend.capabilities.warnings),
        storage_probe_diagnostics=backend.probe_diagnostics,
        storage_unverified_acknowledged=bool(settings.storage_allow_unverified),
    )
    return VaultConfigRead(**cfg)
