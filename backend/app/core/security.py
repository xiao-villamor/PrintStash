from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.db.models import User
from app.db.session import get_session
from app.services.auth import get_user_by_id, verify_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_token_payload(
    token: str | None = Depends(oauth2_scheme),
) -> Optional[dict]:
    """FastAPI dependency: decoded JWT payload, or None.

    FastAPI caches dependency results per request, so stacking
    `get_current_user` + `require_auth` decodes the token exactly once.
    """
    if not token:
        return None
    return verify_access_token(token)


def get_current_user(
    payload: Optional[dict] = Depends(get_token_payload),
    session: Session = Depends(get_session),
) -> Optional[User]:
    """FastAPI dependency: extract user from JWT Bearer token.

    Returns None when no valid token is present (read endpoints are open).
    Callers that require a user should check the result or use `require_user`.
    """
    if not payload:
        return None
    user_id_str = payload.get("sub")
    if not user_id_str:
        return None
    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        return None
    user = get_user_by_id(session, user_id)
    if user and user.is_active:
        return user
    return None


async def require_auth(
    current_user: Optional[User] = Depends(get_current_user),
    payload: Optional[dict] = Depends(get_token_payload),
) -> None:
    """FastAPI dependency: require a valid JWT user with write access."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not_authenticated",
        )
    scope = payload.get("scope") if payload else None
    if scope in {"write", "admin"}:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="insufficient_scope",
    )


async def require_user(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """FastAPI dependency: require a valid JWT user.

    Use this on user-profile or password-change endpoints where only a logged-in
    human should be accepted.
    """
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not_authenticated",
        )
    return current_user


async def require_superuser(
    current_user: User = Depends(require_user),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin_required",
        )
    return current_user
