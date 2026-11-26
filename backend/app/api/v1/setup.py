"""First-run setup wizard.

While the install is unconfigured (no ``system_config.configured_at`` and no
users), this router is the *only* write surface that accepts traffic without
auth. Once ``POST /setup`` succeeds, the endpoint becomes read-only and
returns 409 on further attempts — re-running the wizard would let an attacker
seize an established vault.

The routes own transport only: the browser session, origin checks, cookies and
response shapes. First ownership and storage preparation are operations of
``modules.administration``, which startup also uses for ``VAULT_SETUP_ADMIN_*``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, Response, status
from printstash_core.mesh.preview_profile import PreviewAppearance, PreviewLook
from sqlmodel import Session, select

from app.api import setup_session
from app.api.session_cookie import set_session_cookie
from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.core.security import require_auth, require_superuser
from app.db.models import SystemConfig, User
from app.db.session import get_session
from app.modules.administration import (
    runtime_config,
    setup_bootstrap,
    setup_policy,
    setup_storage,
)
from app.modules.derivatives.policy import (
    SettingName,
    effective_setting,
    render_appearance,
)
from app.modules.identity.auth import create_access_token
from app.schemas.setup import (
    SetupCheckResponse,
    SetupPreviewAppearance,
    SetupPreviewMode,
    SetupRequest,
    SetupResponse,
    SetupSessionResponse,
    SetupStatus,
    SetupStorageCheck,
    SetupStorageRequest,
    UnavailableReason,
)

router = APIRouter(prefix="/setup", tags=["setup"])


def _unavailable(hostname: str) -> tuple[UnavailableReason | None, list[str] | None]:
    """Why this browser cannot claim the installation, and which variables to fix."""
    policy = setup_policy.current()
    if isinstance(policy, setup_policy.Misconfigured):
        return policy.code, list(policy.variables)
    if isinstance(policy, setup_policy.Environment):
        # The owner is created at startup; unconfigured here means that start
        # has not happened with these settings yet.
        return "environment", None
    if isinstance(policy, setup_policy.Disabled):
        return "disabled", None
    if not setup_session.host_allowed(hostname):
        return "untrusted_host", None
    return None, None


@router.get("/status", response_model=SetupStatus, response_model_exclude_none=True)
def get_status(
    request: Request, session: Session = Depends(get_session)
) -> SetupStatus:
    """Lightweight probe — safe to call on every page load."""
    config = session.get(SystemConfig, 1)
    user_count = len(session.exec(select(User.id)).all())
    closed = bool(
        user_count or (config is not None and config.configured_at is not None)
    )
    if closed:
        return SetupStatus(
            configured=True,
            recovery_required=not bool(user_count)
            or config is None
            or config.configured_at is None
            or config.setup_storage_pending,
            storage_choice_required=setup_storage.choice_required(config),
        )
    hostname = request.url.hostname or ""
    reason, variables = _unavailable(hostname)
    if reason is not None:
        # The host is the one the caller sent, echoed so the explainer can name
        # the address PrintStash refused; it discloses nothing new. Variables
        # are names only, never values, and only while nobody owns the install.
        return SetupStatus(
            configured=False,
            unavailable_reason=reason,
            unavailable_variables=variables,
            observed_host=hostname,
        )
    provider_config = runtime_config.get_sanitized_storage_provider(session)
    appearance = render_appearance(config)
    preview_mode = SetupPreviewMode.SINGLE
    if effective_setting(config, SettingName.PREVIEWS):
        preview_mode = (
            SetupPreviewMode.OBLIQUE_POLES
            if effective_setting(config, SettingName.PREVIEW_POLES)
            else SetupPreviewMode.OBLIQUE
        )
    return SetupStatus(
        configured=False,
        setup_available=True,
        user_count=user_count,
        preview_appearance=SetupPreviewAppearance(
            preview_look=appearance.look, preview_floor_grid=appearance.floor_grid
        ),
        preview_mode=preview_mode,
        # The deployment's own paths (VAULT_DATA_ROOT and any override), not a
        # later runtime edit: what a blank field means on a fresh install.
        default_data_dir=str(settings.frozen.data_dir),
        default_thumb_dir=str(settings.frozen.thumb_dir),
        current_data_dir=str(settings.data_dir),
        current_thumb_dir=str(settings.thumb_dir),
        current_storage_backend=str(settings.storage_backend),
        current_storage_provider=(provider_config[0] if provider_config else None),
        current_storage_provider_config=(
            provider_config[1] if provider_config else None
        ),
        current_s3_bucket=str(settings.s3_bucket),
        current_s3_endpoint_url=str(settings.s3_endpoint_url),
        current_s3_region=str(settings.s3_region),
        current_backup_retention_days=int(settings.backup_retention_days),
        current_backup_s3_bucket=str(settings.backup_s3_bucket),
        current_backup_s3_endpoint_url=str(settings.backup_s3_endpoint_url),
        current_backup_s3_region=str(settings.backup_s3_region),
        configured_at=config.configured_at if config is not None else None,
    )


@router.post(
    "/session",
    response_model=SetupSessionResponse,
    dependencies=[Depends(rate_limit(30, 60))],
)
def begin_setup(
    request: Request, response: Response, session: Session = Depends(get_session)
) -> SetupSessionResponse:
    return SetupSessionResponse(csrf=setup_session.begin(request, response, session))


@router.get(
    "/preview-example",
    response_class=Response,
    responses={200: {"content": {"image/webp": {}}}},
    dependencies=[Depends(rate_limit(40, 60))],
)
def preview_example(
    look: PreviewLook,
    floor_grid: bool,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    setup_session.verify_preview(request)
    setup_bootstrap.require_open(session)
    from app.modules.media.preview_example import render_example

    return Response(
        render_example(PreviewAppearance(look, floor_grid)),
        media_type="image/webp",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post(
    "/check-storage",
    response_model=SetupCheckResponse,
    dependencies=[Depends(rate_limit(20, 60))],
)
def check_storage(
    body: SetupStorageRequest, request: Request, session: Session = Depends(get_session)
) -> SetupCheckResponse:
    setup_session.verify(request)
    setup_bootstrap.require_open(session)
    prepared = setup_storage.prepare(body, session, provision=True)
    return SetupCheckResponse(
        ready=True,
        storage_provider=body.storage_provider or prepared.storage_backend,
        checks=setup_storage.check(body, prepared),
    )


@router.post(
    "/prepare-storage",
    response_model=SetupCheckResponse,
    dependencies=[Depends(require_auth)],
)
def prepare_storage(
    body: SetupStorageRequest | None = Body(default=None),
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> SetupCheckResponse:
    """Finish pending storage, or choose it when nobody has yet.

    Without a body this retries an activation that failed after the choice was
    persisted. An owner provisioned from ``VAULT_SETUP_ADMIN_*`` has no choice to
    retry, so its first call carries one. An empty body counts as no body.
    Deliberately not origin-gated: a signed-in owner is already trusted, and this
    is the path that works behind a proxy that rewrites ``Host``.
    """
    setup_storage.prepare_pending(session, body)
    return SetupCheckResponse(
        ready=True,
        storage_provider=str(settings.storage_backend),
        checks=[SetupStorageCheck(code="storage_prepared")],
    )


@router.post(
    "",
    response_model=SetupResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(20, 60))],
)
def complete_setup(
    body: SetupRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> SetupResponse:
    setup_session.require_origin(request)
    setup_bootstrap.require_open(session)
    setup_session.verify(request)
    try:
        setup_bootstrap.lock_installation(session)
        ownership = setup_bootstrap.claim(session, body)
    except Exception:
        session.rollback()
        raise
    user = ownership.user
    token = create_access_token(
        user.id, user.username, scope="admin", auth_version=user.auth_version
    )
    setup_session.clear(response, request)
    set_session_cookie(response, token)
    response.headers["Cache-Control"] = "no-store"
    return SetupResponse(
        configured=True,
        user_id=user.id,
        username=user.username,
        storage_backend=str(settings.storage_backend),
        storage_provider=(body.storage_provider or str(settings.storage_backend)),
        data_dir=str(settings.data_dir),
        thumb_dir=str(settings.thumb_dir),
        access_token=token,
        storage_ready=ownership.storage_ready,
    )
