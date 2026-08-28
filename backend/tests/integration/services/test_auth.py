"""Credentials, and every way one stops working.

The three things here that are easy to get subtly wrong all have the same shape: a
credential that *looks* revoked but still authenticates.

**Legacy bcrypt.** Old hashes truncate the password at 72 bytes; the new ones do not. So a
successful login against a bcrypt hash re-hashes the **original** password rather than the
truncated byte string it just verified — otherwise a user's long password would be
permanently shortened by the very upgrade meant to strengthen it. A failed login re-hashes
nothing.

**Session invalidation.** Bumping `auth_version` and revoking refresh tokens happens in
the *caller's* transaction, so a password change and its session invalidation can never be
committed separately. Committed apart, there is a window where the old password is gone
and the old sessions still work.

**Rotation is once.** A refresh token can be exchanged exactly once, even under
concurrency, because a token that rotates twice hands two clients a live session from one
credential.
"""

from __future__ import annotations

import threading
import uuid

import bcrypt
import pytest
from sqlalchemy.sql.dml import Update
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.time import utcnow
from app.db.models import RefreshToken, User
from app.services.auth import (
    authenticate_api_key,
    authenticate_user,
    create_access_token,
    create_api_key,
    create_file_download_token,
    create_refresh_token,
    hash_password,
    invalidate_user_sessions,
    prune_expired_refresh_tokens,
    revoke_access_token,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_access_token,
    verify_file_download_token,
    verify_password,
)
from tests.factories import (
    build_user,
    user_config,
)


def _legacy_bcrypt_hash(password: str) -> str:
    password_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


class TestPasswordsAndSessions:
    def test_new_password_hashes_use_argon2(self) -> None:
        hashed = hash_password("Password123")

        assert hashed.startswith("$argon2")
        assert verify_password("Password123", hashed) is True
        assert verify_password("NotThePassword", hashed) is False

    def test_password_hashing_supports_long_unicode_passwords(self) -> None:
        password = "contraseña-🔐-" * 32
        hashed = hash_password(password)

        assert len(password.encode("utf-8")) > 72
        assert verify_password(password, hashed) is True
        assert verify_password(f"{password}!", hashed) is False

    def test_malformed_password_hash_is_a_controlled_failure(self) -> None:
        assert verify_password("Password123", "not-a-password-hash") is False
        assert verify_password("Password123", "$2b$invalid") is False

    def test_a_legacy_bcrypt_login_persists_the_rehashed_password(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(
            db_session,
            "legacy-bcrypt",
            password_hash=_legacy_bcrypt_hash("Password123"),
        )
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
        self,
        db_session: Session,
    ) -> None:
        shared_prefix = "🔐" * 18
        original = f"{shared_prefix}-original-suffix"
        same_legacy_prefix = f"{shared_prefix}-different-suffix"
        user = build_user(
            db_session,
            "legacy-long-password",
            password_hash=_legacy_bcrypt_hash(original),
        )

        authenticated = authenticate_user(db_session, user.username, original)

        assert authenticated is not None
        assert authenticated.hashed_password.startswith("$argon2")
        assert authenticate_user(db_session, user.username, same_legacy_prefix) is None

    def test_failed_legacy_bcrypt_login_does_not_rehash(
        self, db_session: Session
    ) -> None:
        legacy_hash = _legacy_bcrypt_hash("Password123")
        user = build_user(db_session, "legacy-bad-login", password_hash=legacy_hash)

        assert authenticate_user(db_session, user.username, "WrongPassword") is None
        db_session.refresh(user)
        assert user.hashed_password == legacy_hash

    def test_inactive_user_cannot_login_with_password_or_api_key(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "inactive-user", active=False)
        _, raw_key = create_api_key(db_session, user.id, "CI key")

        assert authenticate_user(db_session, user.username, "Password123") is None
        assert authenticate_api_key(db_session, user.username, raw_key) is None

    def test_successful_api_key_login_updates_last_used_at(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "api-key-user")
        record, raw_key = create_api_key(db_session, user.id, "Orca uploader")
        assert record.last_used_at is None

        authenticated = authenticate_api_key(db_session, user.username, raw_key)
        db_session.refresh(record)

        assert authenticated is not None
        assert authenticated.id == user.id
        assert record.last_used_at is not None

    def test_expired_refresh_token_does_not_rotate(self, db_session: Session) -> None:
        user = build_user(db_session, "refresh-user")
        raw_token = create_refresh_token(db_session, user.id, minutes=-1)

        assert rotate_refresh_token(db_session, raw_token) is None

    def test_refresh_token_can_only_be_rotated_once_under_concurrency(
        self, tmp_path, monkeypatch
    ) -> None:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'refresh-race.db'}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            user = build_user(session, "refresh-race")
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
                    outcomes.append(
                        rotate_refresh_token(session, raw_token) is not None
                    )
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

    def test_pruning_expired_refresh_tokens_spares_the_live_ones(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "refresh-prune")
        expired = [
            create_refresh_token(db_session, user.id, minutes=-1) for _ in range(3)
        ]
        live = create_refresh_token(db_session, user.id, minutes=60)

        assert prune_expired_refresh_tokens(batch_size=2) == 2
        assert prune_expired_refresh_tokens(batch_size=2) == 1
        assert prune_expired_refresh_tokens(batch_size=2) == 0

        remaining = db_session.exec(select(RefreshToken)).all()
        assert len(remaining) == 1
        assert remaining[0].expires_at > utcnow().replace(tzinfo=None)
        assert live not in expired


class TestTokenRevocation:
    def test_revokes_a_refresh_token(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        raw = create_refresh_token(db_session, user.id)

        assert revoke_refresh_token(db_session, raw) is True

    def test_stays_revoked_when_asked_twice(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        raw = create_refresh_token(db_session, user.id)
        revoke_refresh_token(db_session, raw)

        # Idempotent: a client retrying a logout must not see a failure.
        assert revoke_refresh_token(db_session, raw) is True

    def test_reports_a_token_it_never_issued(self, db_session: Session) -> None:
        assert revoke_refresh_token(db_session, "never-issued") is False

    def test_revokes_every_token_a_user_holds(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        first = create_refresh_token(db_session, user.id)
        second = create_refresh_token(db_session, user.id)

        revoke_all_refresh_tokens(db_session, user.id)

        # "Sign out everywhere" has to reach the session on the other device.
        assert rotate_refresh_token(db_session, first) is None
        assert rotate_refresh_token(db_session, second) is None

    def test_blocks_an_access_token_it_was_given(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        token = create_access_token(user.id, user.username, scope="write")

        revoke_access_token(token)

        assert verify_access_token(token) is None

    def test_ignores_an_access_token_it_cannot_read(self) -> None:
        # A malformed token is already refused by verification; adding it to the
        # blocklist would grow it without bound from unauthenticated input.
        revoke_access_token("not.a.jwt")


class TestInvalidateUserSessions:
    def test_bumps_the_auth_version(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        before = user.auth_version

        invalidate_user_sessions(db_session, user)

        assert user.auth_version == before + 1

    def test_stops_an_existing_access_token_working(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        token = create_access_token(
            user.id, user.username, scope="write", auth_version=user.auth_version
        )

        invalidate_user_sessions(db_session, user)
        db_session.commit()

        payload = verify_access_token(token)
        assert payload is not None
        assert payload["auth_version"] != user.auth_version

    def test_revokes_every_refresh_token(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        raw = create_refresh_token(db_session, user.id)

        invalidate_user_sessions(db_session, user)
        db_session.commit()

        assert rotate_refresh_token(db_session, raw) is None

    def test_leaves_the_commit_to_its_caller(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        raw = create_refresh_token(db_session, user.id)

        invalidate_user_sessions(db_session, user)
        db_session.rollback()

        # The caller owns the transaction so a password change and its session
        # invalidation can never be committed separately — committed apart,
        # there is a window where the old password is gone and the old sessions
        # still work.
        assert rotate_refresh_token(db_session, raw) is not None

    def test_refuses_a_user_that_was_never_saved(self, db_session: Session) -> None:
        with pytest.raises(ValueError, match="unpersisted user"):
            invalidate_user_sessions(db_session, user_config("ghost"))


class TestPruneExpiredRefreshTokens:
    def test_refuses_a_batch_size_below_one(self) -> None:
        with pytest.raises(ValueError, match="batch_size must be positive"):
            prune_expired_refresh_tokens(batch_size=0)


class TestAuthenticateUser:
    def test_signs_a_user_in(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")

        assert authenticate_user(db_session, user.username, "Password123") is not None

    def test_refuses_a_username_it_does_not_know(self, db_session: Session) -> None:
        assert authenticate_user(db_session, "nobody", "Password123") is None

    def test_refuses_a_wrong_password(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")

        assert authenticate_user(db_session, user.username, "Wrong") is None

    def test_refuses_a_user_managed_by_the_identity_provider(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        user.oidc_managed = True
        db_session.add(user)
        db_session.commit()

        # A local password on an SSO-managed account would be a way around the
        # provider's own policy, so it is never accepted.
        assert authenticate_user(db_session, user.username, "Password123") is None


class TestFileDownloadToken:
    def test_authorises_the_file_it_was_issued_for(self, db_session: Session) -> None:
        token = create_file_download_token(7)

        assert verify_file_download_token(token, 7) is True

    def test_refuses_another_file(self, db_session: Session) -> None:
        token = create_file_download_token(7)

        # These land in a URL a slicer can fetch without a login, so one must
        # never work for a different file.
        assert verify_file_download_token(token, 8) is False

    def test_refuses_an_ordinary_access_token(self, db_session: Session) -> None:
        user = build_user(db_session, f"auth-{uuid.uuid4().hex[:8]}")
        token = create_access_token(user.id, user.username, scope="write")

        # A login token in a URL would be a login token in a proxy log.
        assert verify_file_download_token(token, 7) is False

    def test_refuses_a_token_it_cannot_read(self) -> None:
        assert verify_file_download_token("not.a.jwt", 7) is False
