"""The one-time OAuth state that makes a MyMiniFactory callback safe to replay-proof.

A callback URL sits in a browser history and gets re-opened; a provider can also deliver
the same callback twice. So the state is spent by a **conditional UPDATE** — one
statement that matches only a state that is unused, unexpired, and bound to this exact
redirect URI — and the reservation is committed *before* the code is exchanged. Two
callbacks racing therefore trade the code at most once, on both SQLite and PostgreSQL,
which is why the statement's rendered SQL is asserted for both dialects.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import CaptureProvider, ProviderConnection, ProviderOAuthState, User
from app.services import provider_connections as service
from app.services.capture_provider_connections import (
    MyMiniFactoryTokens,
    ProviderConnectionError,
)
from tests.factories import build_user
from tests.integration.services.provider_connections.conftest import MMF_CLIENT_ID

REDIRECT_URI = "https://vault.example/callback"
TOKENS = MyMiniFactoryTokens("access-token", "refresh-token", 3600)


class _ExchangeClient:
    def __init__(self, tokens: MyMiniFactoryTokens | None = TOKENS) -> None:
        self.tokens = tokens
        self.codes: list[str] = []

    async def exchange_code(self, _credentials, *, code: str, redirect_uri: str):
        self.codes.append(code)
        if self.tokens is None:
            raise ProviderConnectionError("provider_auth_failed")
        return self.tokens


@pytest.fixture
def user(db_session: Session) -> User:
    row = build_user(db_session, "oauth-service")
    assert row.id is not None
    return row


class TestGetMmfCredentials:
    def test_returns_the_configured_deployment_credentials(
        self, mmf_configured
    ) -> None:
        assert service.get_mmf_credentials().client_id == MMF_CLIENT_ID

    def test_refuses_when_the_deployment_configured_none(self) -> None:
        with pytest.raises(ProviderConnectionError, match="provider_not_configured"):
            service.get_mmf_credentials()

    def test_unwraps_a_secret_wrapped_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import SecretStr

        from app.core.config import _overlay

        _overlay["mmf_client_id"] = SecretStr("wrapped-id")
        _overlay["mmf_client_secret"] = SecretStr("wrapped-secret")
        try:
            assert service.get_mmf_credentials().client_id == "wrapped-id"
        finally:
            _overlay.pop("mmf_client_id", None)
            _overlay.pop("mmf_client_secret", None)


class TestBeginOauth:
    def test_stores_only_a_hash_of_the_state(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)

        row = db_session.exec(select(ProviderOAuthState)).one()
        assert row.state_hash != raw
        assert raw not in repr(row)

    def test_binds_the_state_to_the_redirect_uri_it_was_issued_for(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        service.begin_oauth(db_session, user.id, REDIRECT_URI)

        assert db_session.exec(select(ProviderOAuthState)).one().redirect_uri == (
            REDIRECT_URI
        )

    def test_gives_the_state_ten_minutes_to_live(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        service.begin_oauth(db_session, user.id, REDIRECT_URI)

        row = db_session.exec(select(ProviderOAuthState)).one()
        assert abs(
            row.expires_at - utcnow().replace(tzinfo=None) - timedelta(minutes=10)
        ) < timedelta(seconds=5)


class TestAuthorizationUrl:
    def test_carries_what_the_callback_will_check(self, mmf_configured) -> None:
        query = parse_qs(
            urlparse(service.authorization_url("state-value", REDIRECT_URI)).query
        )

        assert query["state"] == ["state-value"]
        assert query["redirect_uri"] == [REDIRECT_URI]
        assert query["client_id"] == [MMF_CLIENT_ID]


class TestOauthReservationStatement:
    def test_reserves_only_an_unused_unexpired_state_for_its_redirect_uri(self) -> None:
        statement = service._oauth_reservation_statement(
            "state-hash", REDIRECT_URI, utcnow()
        )

        rendered = [
            str(statement.compile(dialect=sqlite.dialect())),
            str(statement.compile(dialect=postgresql.dialect())),
        ]

        # The guard must live in the SQL, not in Python: a read-then-write would
        # let two callbacks both pass it.
        assert all("used_at IS NULL" in sql for sql in rendered)
        assert all("expires_at" in sql for sql in rendered)
        assert all("redirect_uri" in sql for sql in rendered)


class TestConsumeOauth:
    def test_returns_the_state_it_reserved(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)

        row = service.consume_oauth(db_session, raw, REDIRECT_URI)

        assert row is not None
        assert row.user_id == user.id

    def test_refuses_a_state_that_was_already_consumed(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)
        service.consume_oauth(db_session, raw, REDIRECT_URI)

        assert service.consume_oauth(db_session, raw, REDIRECT_URI) is None

    def test_refuses_a_state_presented_for_a_different_redirect_uri(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)

        assert service.consume_oauth(db_session, raw, "https://evil.example/cb") is None

    def test_refuses_a_state_that_expired(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)
        row = db_session.exec(select(ProviderOAuthState)).one()
        row.expires_at = utcnow().replace(tzinfo=None) - timedelta(seconds=1)
        db_session.flush()

        assert service.consume_oauth(db_session, raw, REDIRECT_URI) is None

    def test_refuses_a_state_this_deployment_never_issued(
        self, db_session: Session
    ) -> None:
        assert service.consume_oauth(db_session, "forged", REDIRECT_URI) is None


class TestFinishOauth:
    def test_stores_the_tokens_it_exchanged_the_code_for(
        self,
        db_session: Session,
        user: User,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert user.id is not None
        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)
        db_session.commit()
        monkeypatch.setattr(service, "get_mmf_client", lambda: _ExchangeClient())

        connected = asyncio.run(
            service.finish_oauth(
                db_session, state=raw, code="auth-code", redirect_uri=REDIRECT_URI
            )
        )

        assert connected is True
        row = db_session.exec(select(ProviderConnection)).one()
        assert row.access_token == TOKENS.access_token
        assert row.refresh_token == TOKENS.refresh_token

    def test_replaces_the_tokens_on_a_connection_that_already_exists(
        self,
        db_session: Session,
        user: User,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert user.id is not None
        db_session.add(
            ProviderConnection(
                user_id=user.id,
                provider=CaptureProvider.MYMINIFACTORY,
                access_token="stale-access",
                refresh_token="stale-refresh",
            )
        )
        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)
        db_session.commit()
        monkeypatch.setattr(service, "get_mmf_client", lambda: _ExchangeClient())

        asyncio.run(
            service.finish_oauth(
                db_session, state=raw, code="auth-code", redirect_uri=REDIRECT_URI
            )
        )

        assert db_session.exec(select(ProviderConnection)).one().access_token == (
            TOKENS.access_token
        )

    def test_refuses_a_state_it_cannot_reserve(
        self, db_session: Session, mmf_configured, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "get_mmf_client", lambda: _ExchangeClient())

        connected = asyncio.run(
            service.finish_oauth(
                db_session, state="forged", code="x", redirect_uri=REDIRECT_URI
            )
        )

        assert connected is False

    def test_reports_a_provider_that_rejects_the_code(
        self,
        db_session: Session,
        user: User,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert user.id is not None
        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)
        db_session.commit()
        monkeypatch.setattr(service, "get_mmf_client", lambda: _ExchangeClient(None))

        connected = asyncio.run(
            service.finish_oauth(
                db_session, state=raw, code="x", redirect_uri=REDIRECT_URI
            )
        )

        assert connected is False

    def test_spends_the_state_before_asking_the_provider(
        self,
        db_session: Session,
        user: User,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert user.id is not None
        raw = service.begin_oauth(db_session, user.id, REDIRECT_URI)
        db_session.commit()
        monkeypatch.setattr(service, "get_mmf_client", lambda: _ExchangeClient(None))

        asyncio.run(
            service.finish_oauth(
                db_session, state=raw, code="x", redirect_uri=REDIRECT_URI
            )
        )

        # Committed before the exchange, so a failure cannot hand the state back.
        assert db_session.exec(select(ProviderOAuthState)).one().used_at is not None

    def test_exchanges_a_code_once_when_two_callbacks_race(
        self, file_engine, mmf_configured, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = file_engine("oauth-race")
        with Session(engine) as setup:
            owner = build_user(setup, "oauth-race")
            assert owner.id is not None
            raw = service.begin_oauth(setup, owner.id, REDIRECT_URI)
            setup.commit()

        class GatedExchange:
            def __init__(self) -> None:
                self.calls = 0
                self._lock = threading.Lock()
                self.entered = threading.Event()
                self.release = threading.Event()

            async def exchange_code(self, *_args, **_kwargs) -> MyMiniFactoryTokens:
                with self._lock:
                    self.calls += 1
                self.entered.set()
                if not self.release.wait(2):
                    raise AssertionError("test exchange gate was not released")
                return TOKENS

        gate = GatedExchange()
        monkeypatch.setattr(service, "get_mmf_client", lambda: gate)

        def callback(code: str) -> bool:
            with Session(engine) as session:
                result = asyncio.run(
                    service.finish_oauth(
                        session, state=raw, code=code, redirect_uri=REDIRECT_URI
                    )
                )
                session.commit()
                return result

        executor = ThreadPoolExecutor(max_workers=2)
        first = executor.submit(callback, "code-a")
        second = executor.submit(callback, "code-b")
        try:
            assert gate.entered.wait(2)
            deadline = time.monotonic() + 2
            while not (first.done() or second.done()) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert first.done() or second.done(), (
                "the losing callback did not observe the state as spent"
            )
            assert gate.calls == 1
            gate.release.set()
            results = [first.result(timeout=2), second.result(timeout=2)]
        finally:
            gate.release.set()
            executor.shutdown(wait=True, cancel_futures=True)

        assert sorted(results) == [False, True]
