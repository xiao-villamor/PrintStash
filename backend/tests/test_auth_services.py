from __future__ import annotations

import threading

from sqlalchemy.sql.dml import Update
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.time import utcnow
from app.db.models import RefreshToken, User
from app.services.auth import (
    authenticate_api_key,
    authenticate_user,
    create_api_key,
    create_refresh_token,
    hash_password,
    prune_expired_refresh_tokens,
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


def test_refresh_token_can_only_be_rotated_once_under_concurrency(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'refresh-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = _user(session, "refresh-race")
        raw_token = create_refresh_token(session, user.id)

    start = threading.Barrier(3)
    both_selects_complete = threading.Barrier(2)
    original_exec = Session.exec

    def synchronize_legacy_read(session, statement, *args, **kwargs):
        result = original_exec(session, statement, *args, **kwargs)
        # This hook makes the old read-then-write implementation lose the
        # race deterministically. The fixed implementation issues one atomic
        # conditional UPDATE and never enters this branch.
        if not isinstance(statement, Update) and "refresh_tokens" in str(statement):
            both_selects_complete.wait(timeout=5)
        return result

    monkeypatch.setattr(Session, "exec", synchronize_legacy_read)
    outcomes: list[bool] = []
    errors: list[BaseException] = []

    def rotate() -> None:
        try:
            with Session(engine) as session:
                start.wait(timeout=5)
                outcomes.append(rotate_refresh_token(session, raw_token) is not None)
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=rotate) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1


def test_prune_expired_refresh_tokens_is_bounded_and_preserves_live_tokens(
    db_session: Session,
) -> None:
    user = _user(db_session, "refresh-prune")
    expired = [create_refresh_token(db_session, user.id, minutes=-1) for _ in range(3)]
    live = create_refresh_token(db_session, user.id, minutes=60)

    assert prune_expired_refresh_tokens(batch_size=2) == 2
    assert prune_expired_refresh_tokens(batch_size=2) == 1
    assert prune_expired_refresh_tokens(batch_size=2) == 0

    remaining = db_session.exec(select(RefreshToken)).all()
    assert len(remaining) == 1
    assert remaining[0].expires_at > utcnow().replace(tzinfo=None)
    assert live not in expired
