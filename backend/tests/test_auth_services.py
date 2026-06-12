from __future__ import annotations

from sqlmodel import Session

from app.db.models import User
from app.services.auth import (
    authenticate_api_key,
    authenticate_user,
    create_api_key,
    create_refresh_token,
    hash_password,
    rotate_refresh_token,
)


def _user(
    session: Session,
    username: str,
    *,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_inactive_user_cannot_login_with_password_or_api_key(
    db_session: Session,
) -> None:
    user = _user(db_session, "inactive-user", is_active=False)
    _, raw_key = create_api_key(db_session, user.id, "CI key")

    assert authenticate_user(db_session, user.username, "Password123") is None
    assert authenticate_api_key(db_session, user.username, raw_key) is None


def test_successful_api_key_login_updates_last_used_at(db_session: Session) -> None:
    user = _user(db_session, "api-key-user")
    record, raw_key = create_api_key(db_session, user.id, "Orca uploader")
    assert record.last_used_at is None

    authenticated = authenticate_api_key(db_session, user.username, raw_key)
    db_session.refresh(record)

    assert authenticated is not None
    assert authenticated.id == user.id
    assert record.last_used_at is not None


def test_expired_refresh_token_does_not_rotate(db_session: Session) -> None:
    user = _user(db_session, "refresh-user")
    raw_token = create_refresh_token(db_session, user.id, minutes=-1)

    assert rotate_refresh_token(db_session, raw_token) is None
