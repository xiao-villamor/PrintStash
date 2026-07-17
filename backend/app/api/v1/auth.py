"""Authentication endpoints: password login and current-user lookup.

Stage 1 ships a thin local username/password flow that mints a JWT.
Stage 4 will graft OAuth2 / multi-tenant onto the same surface.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.core.security import oauth2_scheme, require_user
from app.db.models import User
from app.db.session import get_session
from app.schemas.auth import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyRead,
    AuthProvidersRead,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserRead,
)
from app.services import oidc
from app.services.auth import (
    authenticate_api_key,
    authenticate_user,
    clear_session_cookie,
    create_access_token,
    create_api_key,
    create_refresh_token,
    list_active_api_keys,
    revoke_access_token,
    revoke_all_refresh_tokens,
    revoke_api_key,
    revoke_refresh_token,
    rotate_refresh_token,
    set_session_cookie,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Separate limiters: a login flood must not lock out clients that only need
# to refresh an existing session.
_login_rate_limit = rate_limit(10, 60.0)
_refresh_rate_limit = rate_limit(10, 60.0)

_OIDC_COOKIE_PATH = "/api/v1/auth/oidc"
_OIDC_STATE_COOKIE = "printstash_oidc_state"
_OIDC_NONCE_COOKIE = "printstash_oidc_nonce"
_OIDC_VERIFIER_COOKIE = "printstash_oidc_verifier"


def _oidc_cookie(
    response: Response, name: str, value: str, *, secure: bool = False
) -> None:
    response.set_cookie(
        name,
        value,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=_OIDC_COOKIE_PATH,
        max_age=600,
    )


def _clear_oidc_cookies(response: Response) -> None:
    for name in (_OIDC_STATE_COOKIE, _OIDC_NONCE_COOKIE, _OIDC_VERIFIER_COOKIE):
        response.delete_cookie(
            name,
            httponly=True,
            secure=bool(settings.session_cookie_secure),
            samesite="lax",
            path=_OIDC_COOKIE_PATH,
        )


@router.get("/providers", response_model=AuthProvidersRead)
def auth_providers() -> AuthProvidersRead:
    provider = oidc.provider_status()
    return AuthProvidersRead(
        oidc_enabled=bool(provider["enabled"]),
        oidc_display_name=str(provider["display_name"]),
    )


@router.get("/oidc/login", dependencies=[Depends(_login_rate_limit)])
async def oidc_login(request: Request) -> RedirectResponse:
    redirect_uri = oidc.callback_uri(str(request.url_for("oidc_callback")))
    try:
        login = await oidc.begin_login(redirect_uri)
    except oidc.OIDCError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc
    response = RedirectResponse(login.authorization_url, status_code=302)
    secure = bool(
        settings.session_cookie_secure or urlparse(redirect_uri).scheme == "https"
    )
    _oidc_cookie(response, _OIDC_STATE_COOKIE, login.state, secure=secure)
    _oidc_cookie(response, _OIDC_NONCE_COOKIE, login.nonce, secure=secure)
    _oidc_cookie(response, _OIDC_VERIFIER_COOKIE, login.code_verifier, secure=secure)
    return response


@router.get("/oidc/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    response = RedirectResponse("/login?oidc=success", status_code=302)
    expected_state = request.cookies.get(_OIDC_STATE_COOKIE, "")
    nonce = request.cookies.get(_OIDC_NONCE_COOKIE, "")
    verifier = request.cookies.get(_OIDC_VERIFIER_COOKIE, "")
    if error:
        response = RedirectResponse(
            "/login?oidc_error=provider_rejected", status_code=302
        )
    elif (
        not code
        or not state
        or not expected_state
        or not secrets.compare_digest(state, expected_state)
    ):
        response = RedirectResponse("/login?oidc_error=invalid_state", status_code=302)
    elif not nonce or not verifier:
        response = RedirectResponse("/login?oidc_error=expired", status_code=302)
    else:
        redirect_uri = oidc.callback_uri(str(request.url_for("oidc_callback")))
        try:
            claims = await oidc.exchange_code(code, redirect_uri, verifier, nonce)
            user = oidc.provision_user(session, claims)
            scope = "admin" if user.is_superuser else "write"
            access_token = create_access_token(
                user.id,
                user.username,
                scope=scope,
                expires_delta=timedelta(days=settings.remember_me_days),
                auth_version=user.auth_version,
            )
            set_session_cookie(
                response,
                access_token,
                max_age=int(timedelta(days=settings.remember_me_days).total_seconds()),
                secure=bool(
                    settings.session_cookie_secure
                    or urlparse(redirect_uri).scheme == "https"
                ),
            )
        except oidc.OIDCError as exc:
            response = RedirectResponse(
                f"/login?oidc_error={exc.code}", status_code=302
            )
    _clear_oidc_cookies(response)
    return response


@router.post(
    "/login", response_model=TokenResponse, dependencies=[Depends(_login_rate_limit)]
)
def login(
    body: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> TokenResponse:
    """Authenticate and receive a JWT access token.

    Programmatic clients can exchange username + API key for the same Bearer
    JWT used by the UI, so protected endpoints keep one Authorization header.
    """
    if bool(body.password) == bool(body.api_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provide_password_or_api_key",
        )
    user = (
        authenticate_user(session, body.username, body.password)
        if body.password
        else authenticate_api_key(session, body.username, body.api_key or "")
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_credentials",
        )
    scope = "admin" if user.is_superuser else "write"
    expires_delta = (
        timedelta(days=settings.remember_me_days) if body.remember_me else None
    )
    access_token = create_access_token(
        user.id,
        user.username,
        scope=scope,
        expires_delta=expires_delta,
        auth_version=user.auth_version,
    )
    refresh_token = create_refresh_token(session, user_id=user.id)
    max_age = (
        int(timedelta(days=settings.remember_me_days).total_seconds())
        if body.remember_me
        else None
    )
    set_session_cookie(response, access_token, max_age=max_age)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        scope=scope,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(_refresh_rate_limit)],
)
def refresh(
    body: RefreshRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> TokenResponse:
    """Exchange a valid refresh token for a new access+refresh token pair."""
    old_token = rotate_refresh_token(session, body.refresh_token)
    if old_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_refresh_token",
        )
    user = session.get(User, old_token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_refresh_token",
        )
    scope = "admin" if user.is_superuser else "write"
    access_token = create_access_token(
        user.id, user.username, scope=scope, auth_version=user.auth_version
    )
    refresh_token = create_refresh_token(session, user_id=user.id)
    set_session_cookie(response, access_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        scope=scope,
    )


@router.post("/logout")
def logout(
    response: Response,
    body: LogoutRequest | None = None,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    token: str | None = Depends(oauth2_scheme),
) -> dict:
    """Invalidate access token and optionally revoke refresh token."""
    if token:
        revoke_access_token(token)
    if body and body.refresh_token:
        revoke_refresh_token(session, body.refresh_token)
    current_user.auth_version += 1
    session.add(current_user)
    session.commit()
    revoke_all_refresh_tokens(session, current_user.id)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserRead)
def get_me(current_user=Depends(require_user)) -> UserRead:
    """Return the currently logged-in user's profile."""
    return UserRead.model_validate(current_user)


@router.get("/api-keys", response_model=list[ApiKeyRead])
def get_api_keys(
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ApiKeyRead]:
    return [
        ApiKeyRead.model_validate(key)
        for key in list_active_api_keys(session, current_user.id)
    ]


@router.post("/api-keys", response_model=ApiKeyCreateResponse)
def post_api_key(
    body: ApiKeyCreateRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ApiKeyCreateResponse:
    record, raw_key = create_api_key(session, current_user.id, body.name)
    return ApiKeyCreateResponse(
        id=record.id,
        name=record.name,
        prefix=record.prefix,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        api_key=raw_key,
    )


@router.delete("/api-keys/{key_id}", status_code=204)
def delete_api_key(
    key_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> None:
    if not revoke_api_key(session, current_user.id, key_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="api_key_not_found",
        )
