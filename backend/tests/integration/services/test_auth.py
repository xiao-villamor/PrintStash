"""Authentication services keep credentials upgradeable and sessions revocable.

The tests defend durable token rotation and revocation without allowing malformed
legacy credentials or identity-provider accounts through local authentication.
"""

from __future__ import annotations

import threading

import bcrypt
import pytest
from sqlalchemy.sql.dml import Update
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.time import utcnow
from app.db.models import ApiKey, RefreshToken, User
from app.services.auth import (
    ACCESS_BLOCKLIST,
    _verify_password_and_update,
    authenticate_api_key,
    authenticate_user,
    create_api_key,
    create_refresh_token,
    hash_password,
    invalidate_user_sessions,
    prune_expired_refresh_tokens,
    revoke_access_token,
    revoke_all_refresh_tokens,
    revoke_api_key,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_password,
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


def _legacy_bcrypt_hash(password: str) -> str:
    password_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def test_new_password_hashes_use_argon2() -> None:
    hashed = hash_password("Password123")

    assert hashed.startswith("$argon2")
    assert verify_password("Password123", hashed) is True
    assert verify_password("NotThePassword", hashed) is False


def test_password_hashing_supports_long_unicode_passwords() -> None:
    password = "contraseña-🔐-" * 32
    hashed = hash_password(password)

    assert len(password.encode("utf-8")) > 72
    assert verify_password(password, hashed) is True
    assert verify_password(f"{password}!", hashed) is False


def test_malformed_password_hash_is_a_controlled_failure() -> None:
    assert verify_password("Password123", "not-a-password-hash") is False
    assert verify_password("Password123", "$2b$invalid") is False


def test_successful_legacy_bcrypt_login_rehashes_and_persists(
    db_session: Session,
) -> None:
    user = User(
        username="legacy-bcrypt",
        hashed_password=_legacy_bcrypt_hash("Password123"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    user_id = user.id

    authenticated = authenticate_user(db_session, user.username, "Password123")

    assert authenticated is not None
    assert authenticated.hashed_password.startswith("$argon2")
    with Session(db_session.get_bind()) as fresh_session:
        persisted = fresh_session.get(User, user_id)
        assert persisted is not None
        assert persisted.hashed_password.startswith("$argon2")


def test_legacy_bcrypt_preserves_72_byte_semantics_only_until_rehash(
    db_session: Session,
) -> None:
    shared_prefix = "🔐" * 18
    original = f"{shared_prefix}-original-suffix"
    same_legacy_prefix = f"{shared_prefix}-different-suffix"
    user = User(
        username="legacy-long-password",
        hashed_password=_legacy_bcrypt_hash(original),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    authenticated = authenticate_user(db_session, user.username, original)

    assert authenticated is not None
    assert authenticated.hashed_password.startswith("$argon2")
    assert authenticate_user(db_session, user.username, same_legacy_prefix) is None


def test_failed_legacy_bcrypt_login_does_not_rehash(db_session: Session) -> None:
    legacy_hash = _legacy_bcrypt_hash("Password123")
    user = User(
        username="legacy-bad-login",
        hashed_password=legacy_hash,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    assert authenticate_user(db_session, user.username, "WrongPassword") is None
    db_session.refresh(user)
    assert user.hashed_password == legacy_hash


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


def test_malformed_password_hash_is_failed_upgrade_check() -> None:
    assert _verify_password_and_update("Password123", "malformed") == (False, None)


def test_invalid_access_token_revocation_leaves_blocklist_unchanged() -> None:
    before = set(ACCESS_BLOCKLIST)

    revoke_access_token("not-a-token")

    assert ACCESS_BLOCKLIST == before


def test_missing_refresh_token_is_not_revoked(db_session: Session) -> None:
    assert revoke_refresh_token(db_session, "missing-token") is False


def test_revoke_all_refresh_tokens_revokes_every_live_row(
    db_session: Session,
) -> None:
    user = _user(db_session, "revoke-all")
    create_refresh_token(db_session, user.id)
    create_refresh_token(db_session, user.id)

    revoke_all_refresh_tokens(db_session, user.id)

    rows = db_session.exec(
        select(RefreshToken).where(RefreshToken.user_id == user.id)
    ).all()
    assert len(rows) == 2
    assert rows[0].revoked is True
    assert rows[0].revoked_at is not None
    assert rows[1].revoked is True
    assert rows[1].revoked_at is not None


def test_invalidate_sessions_rejects_unpersisted_user(db_session: Session) -> None:
    user = User(username="unpersisted", hashed_password=hash_password("Password123"))

    with pytest.raises(ValueError, match="unpersisted user"):
        invalidate_user_sessions(db_session, user)


def test_prune_expired_refresh_tokens_rejects_non_positive_batch() -> None:
    with pytest.raises(ValueError, match="batch_size must be positive"):
        prune_expired_refresh_tokens(batch_size=0)


def test_oidc_managed_user_cannot_use_local_password(db_session: Session) -> None:
    user = User(
        username="oidc-user",
        hashed_password=hash_password("Password123"),
        is_active=True,
        oidc_managed=True,
    )
    db_session.add(user)
    db_session.commit()

    authenticated = authenticate_user(db_session, user.username, "Password123")

    assert authenticated is None


@pytest.mark.parametrize(
    "key_selector",
    [
        pytest.param(lambda _foreign_id: 999_999, id="missing"),
        pytest.param(lambda foreign_id: foreign_id, id="foreign"),
    ],
)
def test_api_key_revocation_refuses_missing_or_foreign_key(
    db_session: Session, key_selector
) -> None:
    owner = _user(db_session, "key-owner")
    other = _user(db_session, "key-other")
    record, _ = create_api_key(db_session, other.id, "other key")
    requested_id = key_selector(record.id)

    revoked = revoke_api_key(db_session, owner.id, requested_id)

    assert revoked is False
    persisted = db_session.get(ApiKey, record.id)
    assert persisted is not None
    assert persisted.revoked_at is None
