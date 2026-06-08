"""Authentication endpoints: password login and current-user lookup.

Stage 1 ships a thin local username/password flow that mints a JWT.
Stage 4 will graft OAuth2 / multi-tenant onto the same surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.security import oauth2_scheme, require_user
from app.db.models import User
from app.db.session import get_session
from app.schemas.auth import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyRead,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserRead,
)
from app.services.auth import (
    authenticate_api_key,
    authenticate_user,
    create_api_key,
    create_access_token,
    create_refresh_token,
    list_active_api_keys,
    revoke_api_key,
    revoke_access_token,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
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
    access_token = create_access_token(user.id, user.username, scope=scope)
    refresh_token = create_refresh_token(session, user_id=user.id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        scope=scope,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest, session: Session = Depends(get_session)
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
    access_token = create_access_token(user.id, user.username, scope=scope)
    refresh_token = create_refresh_token(session, user_id=user.id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        scope=scope,
    )


@router.post("/logout")
def logout(
    body: LogoutRequest | None = None,
    session: Session = Depends(get_session),
    token: str | None = Depends(oauth2_scheme),
) -> dict:
    """Invalidate access token and optionally revoke refresh token."""
    if token:
        revoke_access_token(token)
    if body and body.refresh_token:
        revoke_refresh_token(session, body.refresh_token)
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
