"""Detached credential authority for commands with thread-owned SQL sessions.

Only immutable authentication data survives network awaits. Each synchronous
command revalidates its authority and owns its complete Session lifetime.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, cast

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from app.api.session_cookie import extract_access_token
from app.core.browser_device_auth import (
    require_browser_import_user,
    require_user_or_browser_import_user,
)
from app.core.security import get_current_user, get_token_payload, oauth2_scheme
from app.core.time import utcnow
from app.db.models import User
from app.db.session import get_session_factory
from app.modules.identity import auth


@dataclass(frozen=True)
class InternalIdentity:
    """Trusted identity supplied by an internal caller, never parsed from HTTP."""


@dataclass(frozen=True)
class JwtAuthority:
    scope: str | None
    expires_at: int | None
    token_id: str | None

    @classmethod
    def from_payload(cls, payload: dict) -> JwtAuthority:
        # Canonical JWT verification validates exp as integer seconds. Its omission is
        # accepted by that verifier and by explicit dependency overrides.
        expiry = payload.get("exp")
        return cls(
            scope=cast(str | None, payload.get("scope")),
            expires_at=int(expiry) if expiry is not None else None,
            token_id=cast(str | None, payload.get("jti")),
        )


@dataclass(frozen=True)
class BrowserAuthority:
    credential: str = field(repr=False)


@dataclass(frozen=True)
class CommandActor:
    user_id: int
    auth_version: int
    authority: InternalIdentity | JwtAuthority | BrowserAuthority

    @classmethod
    def from_user(cls, user: User) -> CommandActor:
        if user.id is None:
            raise ValueError("persisted command actor required")
        return cls(
            user_id=user.id,
            auth_version=user.auth_version,
            authority=InternalIdentity(),
        )


def require_command_user(
    payload: dict | None = Depends(get_token_payload),
) -> CommandActor:
    with get_session_factory().scoped_session() as session:
        user = get_current_user(payload=payload, session=session)
        if user is None or payload is None:
            raise HTTPException(status_code=401, detail="not_authenticated")
        assert user.id is not None
        return CommandActor(
            user.id, user.auth_version, JwtAuthority.from_payload(payload)
        )


def require_command_writer(
    payload: dict | None = Depends(get_token_payload),
) -> CommandActor:
    actor = require_command_user(payload)
    assert isinstance(actor.authority, JwtAuthority)
    if actor.authority.scope not in {"write", "admin"}:
        raise HTTPException(status_code=401, detail="insufficient_scope")
    return actor


def require_capture_actor(
    request: Request, credential: str | None = Depends(oauth2_scheme)
) -> CommandActor:
    with get_session_factory().scoped_session() as session:
        user = require_user_or_browser_import_user(credential, session, request=request)
        assert user.id is not None
        token = extract_access_token(request, credential)
        if token is not None and token.count(".") == 2:
            payload = auth.verify_access_token(token)
            if payload is None:
                raise HTTPException(status_code=401, detail="not_authenticated")
            authority: JwtAuthority | BrowserAuthority = JwtAuthority.from_payload(
                payload
            )
        else:
            assert credential is not None
            authority = BrowserAuthority(credential)
        actor = CommandActor(user.id, user.auth_version, authority)
        # Canonical paired-device authentication records last_used_at.
        session.commit()
        return actor


@contextmanager
def command_session(actor: CommandActor) -> Iterator[tuple[Session, User]]:
    with get_session_factory().scoped_session() as session:
        if isinstance(actor.authority, BrowserAuthority):
            user = require_browser_import_user(actor.authority.credential, session)
            if user.id != actor.user_id:
                raise HTTPException(
                    status_code=401, detail="invalid_browser_credential"
                )
        else:
            if isinstance(actor.authority, JwtAuthority):
                proof = actor.authority
                if (
                    proof.expires_at is not None
                    and utcnow().timestamp() >= proof.expires_at
                ) or (
                    proof.token_id is not None
                    and proof.token_id in auth.ACCESS_BLOCKLIST
                ):
                    raise HTTPException(status_code=401, detail="not_authenticated")
            user = session.get(User, actor.user_id)
            if (
                user is None
                or not user.is_active
                or user.auth_version != actor.auth_version
            ):
                raise HTTPException(status_code=401, detail="not_authenticated")
        yield session, user
