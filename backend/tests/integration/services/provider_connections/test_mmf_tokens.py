"""Keeping a MyMiniFactory access token fresh without ever committing somebody else's work.

MyMiniFactory tokens expire and rotate, and a rotation must survive: if the new refresh
token is lost, the user has to re-authorize by hand. So a rotation runs in its **own**
session bound to the same engine, and only that session is committed — the caller's
transaction (which may hold unrelated staged work, and in the resolver path is a scoped
session that closes moments later) is never committed by this seam.

The retry rule is exactly one round: a `provider_auth_failed` refreshes once and retries;
a second failure raises and the rotation is rolled back, so a credential is never left
half-rotated. Every rotation also drops the owner's cached provider metadata, and the
transient signed URL a download returns is never written to the row.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import BrowserDevice, CaptureProvider, ProviderConnection, User
from app.services import import_resolvers
from app.services import provider_connections as service
from app.services.capture_provider_connections import (
    MyMiniFactoryTokens,
    ProviderConnectionError,
    ProviderModelMetadata,
)
from tests.factories import build_user

SIGNED_URL = "https://downloads.example.test/signed?token=transient"


class _Client:
    """A MyMiniFactory client that records what token each call was made with."""

    def __init__(
        self,
        *,
        refreshed: MyMiniFactoryTokens | None = None,
        refresh_error: ProviderConnectionError | None = None,
        metadata_failures: int = 0,
        download_failures: int = 0,
    ) -> None:
        self.refreshed = refreshed
        self.refresh_error = refresh_error
        self.metadata_failures = metadata_failures
        self.download_failures = download_failures
        self.refresh_calls: list[str] = []
        self.metadata_tokens: list[str] = []
        self.download_tokens: list[str] = []

    async def refresh_tokens(self, _credentials, tokens: MyMiniFactoryTokens):
        self.refresh_calls.append(tokens.refresh_token)
        if self.refresh_error is not None:
            raise self.refresh_error
        assert self.refreshed is not None
        return self.refreshed

    async def model_metadata(self, model_id: str, tokens: MyMiniFactoryTokens):
        self.metadata_tokens.append(tokens.access_token)
        if len(self.metadata_tokens) <= self.metadata_failures:
            raise ProviderConnectionError("provider_auth_failed")
        return ProviderModelMetadata(model_id, "Model", None, None, None)

    async def file_download_url(self, _file_id: str, tokens: MyMiniFactoryTokens):
        self.download_tokens.append(tokens.access_token)
        if len(self.download_tokens) <= self.download_failures:
            raise ProviderConnectionError("provider_auth_failed")
        return SIGNED_URL


@pytest.fixture
def connect(db_session: Session):
    """A user with a MyMiniFactory connection whose token state you choose."""
    made = {"n": 0}

    def build(
        *,
        access_token: str | None = "old-access",
        refresh_token: str | None = "old-refresh",
        expires_in: timedelta = timedelta(minutes=10),
    ) -> User:
        made["n"] += 1
        user = build_user(db_session, f"mmf-token-{made['n']}")
        assert user.id is not None
        db_session.add(
            ProviderConnection(
                user_id=user.id,
                provider=CaptureProvider.MYMINIFACTORY,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=utcnow() + expires_in,
            )
        )
        db_session.commit()
        return user

    return build


def _row(session: Session, user_id: int) -> ProviderConnection:
    return session.exec(
        select(ProviderConnection).where(
            ProviderConnection.user_id == user_id,
            ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
        )
    ).one()


class TestFetchMmfModelMetadata:
    def test_uses_a_token_that_is_still_fresh(
        self, db_session: Session, connect, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = connect(access_token="fresh-access")
        assert user.id is not None
        client = _Client()
        monkeypatch.setattr(service, "get_mmf_client", lambda: client)

        metadata = asyncio.run(
            service.fetch_mmf_model_metadata(db_session, user.id, "model-1")
        )

        assert metadata.title == "Model"
        assert client.refresh_calls == []
        assert client.metadata_tokens == ["fresh-access"]

    def test_refreshes_a_token_that_has_expired(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(access_token="expired-access", expires_in=timedelta(seconds=-1))
        assert user.id is not None
        client = _Client(
            refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
        )
        monkeypatch.setattr(service, "get_mmf_client", lambda: client)

        asyncio.run(service.fetch_mmf_model_metadata(db_session, user.id, "model-1"))

        assert client.refresh_calls == ["old-refresh"]
        assert client.metadata_tokens == ["new-access"]

    def test_keeps_the_old_refresh_token_when_the_provider_omits_a_new_one(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(expires_in=timedelta(seconds=-1))
        assert user.id is not None
        monkeypatch.setattr(
            service,
            "get_mmf_client",
            lambda: _Client(refreshed=MyMiniFactoryTokens("new-access", "", 3600)),
        )

        asyncio.run(service.fetch_mmf_model_metadata(db_session, user.id, "model-1"))

        # An empty rotation is "unchanged", not "revoked".
        assert _row(db_session, user.id).refresh_token == "old-refresh"

    def test_pushes_the_expiry_forward_after_a_refresh(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(expires_in=timedelta(seconds=-1))
        assert user.id is not None
        monkeypatch.setattr(
            service,
            "get_mmf_client",
            lambda: _Client(
                refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
            ),
        )

        asyncio.run(service.fetch_mmf_model_metadata(db_session, user.id, "model-1"))

        expires_at = _row(db_session, user.id).token_expires_at
        assert expires_at is not None
        assert expires_at.replace(tzinfo=timezone.utc) > utcnow()

    def test_refreshes_once_before_retrying_a_rejected_token(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect()
        assert user.id is not None
        client = _Client(
            refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600),
            metadata_failures=1,
        )
        monkeypatch.setattr(service, "get_mmf_client", lambda: client)

        asyncio.run(service.fetch_mmf_model_metadata(db_session, user.id, "model-1"))

        assert client.metadata_tokens == ["old-access", "new-access"]
        assert client.refresh_calls == ["old-refresh"]

    def test_drops_the_owners_cached_metadata_when_the_token_rotates(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(expires_in=timedelta(seconds=-1))
        assert user.id is not None
        key = (user.id, "myminifactory", "model-1")
        import_resolvers._provider_metadata_cache[key] = (
            ProviderModelMetadata("model-1", "stale", None, None, None),
            utcnow() + timedelta(minutes=5),
        )
        monkeypatch.setattr(
            service,
            "get_mmf_client",
            lambda: _Client(
                refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
            ),
        )

        asyncio.run(service.fetch_mmf_model_metadata(db_session, user.id, "model-1"))

        assert key not in import_resolvers._provider_metadata_cache

    def test_refuses_when_the_user_has_no_connection(self, db_session: Session) -> None:
        with pytest.raises(ProviderConnectionError, match="provider_not_connected"):
            asyncio.run(service.fetch_mmf_model_metadata(db_session, 4242, "model-1"))

    def test_reports_a_connection_that_lost_its_refresh_token_as_invalid(
        self, db_session: Session, connect
    ) -> None:
        user = connect(refresh_token=None)
        assert user.id is not None

        # Distinct from "not connected": the row is there but unusable, and the
        # UI tells the user to re-authorize rather than to connect.
        with pytest.raises(
            ProviderConnectionError, match="provider_connection_invalid"
        ):
            asyncio.run(
                service.fetch_mmf_model_metadata(db_session, user.id, "model-1")
            )

    def test_raises_the_providers_code_without_the_token_when_the_retry_also_fails(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(access_token="old-secret-token", refresh_token="refresh-secret")
        assert user.id is not None
        client = _Client(
            refreshed=MyMiniFactoryTokens("new-secret-token", "new-refresh", 3600),
            metadata_failures=2,
        )
        monkeypatch.setattr(service, "get_mmf_client", lambda: client)

        with pytest.raises(ProviderConnectionError, match="^provider_auth_failed$"):
            asyncio.run(
                service.fetch_mmf_model_metadata(db_session, user.id, "model-1")
            )

    def test_leaves_the_credential_untouched_when_the_retry_also_fails(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(access_token="old-secret-token", refresh_token="refresh-secret")
        assert user.id is not None
        monkeypatch.setattr(
            service,
            "get_mmf_client",
            lambda: _Client(
                refreshed=MyMiniFactoryTokens("new-secret-token", "new-refresh", 3600),
                metadata_failures=2,
            ),
        )

        with pytest.raises(ProviderConnectionError):
            asyncio.run(
                service.fetch_mmf_model_metadata(db_session, user.id, "model-1")
            )

        # A half-rotated credential would need a manual re-authorization to fix.
        with Session(db_session.get_bind()) as fresh:
            persisted = _row(fresh, user.id)
            assert persisted.access_token == "old-secret-token"
            assert persisted.refresh_token == "refresh-secret"

    def test_leaves_the_credential_untouched_when_the_refresh_itself_fails(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(expires_in=timedelta(seconds=-1))
        assert user.id is not None
        monkeypatch.setattr(
            service,
            "get_mmf_client",
            lambda: _Client(
                refresh_error=ProviderConnectionError("provider_auth_failed")
            ),
        )

        with pytest.raises(ProviderConnectionError, match="provider_auth_failed"):
            asyncio.run(
                service.fetch_mmf_model_metadata(db_session, user.id, "model-1")
            )

        with Session(db_session.get_bind()) as fresh:
            assert _row(fresh, user.id).access_token == "old-access"

    def test_raises_a_non_auth_provider_error_without_refreshing(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect()
        assert user.id is not None

        class Rejecting(_Client):
            async def model_metadata(self, _model_id, _tokens):
                raise ProviderConnectionError("provider_rate_limited")

        client = Rejecting()
        monkeypatch.setattr(service, "get_mmf_client", lambda: client)

        with pytest.raises(ProviderConnectionError, match="provider_rate_limited"):
            asyncio.run(
                service.fetch_mmf_model_metadata(db_session, user.id, "model-1")
            )
        assert client.refresh_calls == []


class TestFetchMmfFileDownloadUrl:
    def test_hands_back_the_signed_url(
        self, db_session: Session, connect, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = connect()
        assert user.id is not None
        monkeypatch.setattr(service, "get_mmf_client", lambda: _Client())

        url = asyncio.run(
            service.fetch_mmf_file_download_url(db_session, user.id, "file-1")
        )

        assert url == SIGNED_URL

    def test_never_stores_the_transient_url_on_the_connection(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(expires_in=timedelta(seconds=-1))
        assert user.id is not None
        monkeypatch.setattr(
            service,
            "get_mmf_client",
            lambda: _Client(
                refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
            ),
        )

        asyncio.run(service.fetch_mmf_file_download_url(db_session, user.id, "file-1"))

        assert "transient" not in repr(_row(db_session, user.id))

    def test_refreshes_once_before_retrying_a_rejected_token(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect()
        assert user.id is not None
        client = _Client(
            refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600),
            download_failures=1,
        )
        monkeypatch.setattr(service, "get_mmf_client", lambda: client)

        url = asyncio.run(
            service.fetch_mmf_file_download_url(db_session, user.id, "file-1")
        )

        assert url == SIGNED_URL
        assert client.download_tokens == ["old-access", "new-access"]

    def test_makes_the_rotation_durable_for_a_caller_that_closes_immediately(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(expires_in=timedelta(seconds=-1))
        assert user.id is not None
        monkeypatch.setattr(
            service,
            "get_mmf_client",
            lambda: _Client(
                refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
            ),
        )

        asyncio.run(service.fetch_mmf_file_download_url(db_session, user.id, "file-1"))

        # A resolver's scoped session closes right after it gets the URL.
        with Session(db_session.get_bind()) as fresh:
            persisted = _row(fresh, user.id)
            assert persisted.access_token == "new-access"
            assert persisted.refresh_token == "new-refresh"

    def test_does_not_commit_the_callers_unrelated_staged_work(
        self, file_engine, mmf_configured, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = file_engine("provider-refresh")
        monkeypatch.setattr(
            service,
            "get_mmf_client",
            lambda: _Client(
                refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
            ),
        )
        with Session(engine) as session:
            owner = build_user(session, "provider-refresh")
            assert owner.id is not None
            user_id = owner.id
            session.add(
                ProviderConnection(
                    user_id=user_id,
                    provider=CaptureProvider.MYMINIFACTORY,
                    access_token="expired-access",
                    refresh_token="old-refresh",
                    token_expires_at=utcnow() - timedelta(seconds=1),
                )
            )
            session.commit()
            session.add(
                BrowserDevice(
                    user_id=user_id,
                    name="unrelated-pending-device",
                    credential_hash="d" * 64,
                )
            )

            asyncio.run(service.fetch_mmf_file_download_url(session, user_id, "file-1"))

        with Session(engine) as fresh:
            assert _row(fresh, user_id).access_token == "new-access"
            assert fresh.exec(select(BrowserDevice)).all() == []

    def test_refuses_when_the_user_has_no_connection(self, db_session: Session) -> None:
        with pytest.raises(ProviderConnectionError, match="provider_not_connected"):
            asyncio.run(service.fetch_mmf_file_download_url(db_session, 4242, "file-1"))


class TestTokenExpiryArithmetic:
    def test_treats_a_connection_with_no_recorded_expiry_as_stale(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect()
        assert user.id is not None
        _row(db_session, user.id).token_expires_at = None
        db_session.commit()
        client = _Client(
            refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
        )
        monkeypatch.setattr(service, "get_mmf_client", lambda: client)

        asyncio.run(service.fetch_mmf_model_metadata(db_session, user.id, "model-1"))

        # An unknown expiry is a token that might already be dead.
        assert client.refresh_calls == ["old-refresh"]

    def test_refreshes_only_once_when_an_expired_token_is_also_rejected(
        self,
        db_session: Session,
        connect,
        mmf_configured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = connect(expires_in=timedelta(seconds=-1))
        assert user.id is not None
        client = _Client(
            refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600),
            metadata_failures=1,
        )
        monkeypatch.setattr(service, "get_mmf_client", lambda: client)

        asyncio.run(service.fetch_mmf_model_metadata(db_session, user.id, "model-1"))

        # The second rotation reuses the session the first one opened rather than
        # starting a competing transaction on the same row.
        assert client.refresh_calls == ["old-refresh", "new-refresh"]
        assert client.metadata_tokens == ["new-access", "new-access"]
