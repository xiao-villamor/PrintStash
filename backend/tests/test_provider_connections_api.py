from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    BrowserDevice,
    CaptureProvider,
    ProviderConnection,
    ProviderOAuthState,
    User,
)
from app.db.session import _set_sqlite_pragmas
from app.services import import_resolvers
from app.services import provider_connections as provider_service
from app.services.auth import create_access_token, hash_password
from app.services.capture_provider_connections import (
    MyMiniFactoryTokens,
    ProviderConnectionError,
    ProviderModelMetadata,
)


def _headers(session: Session, name: str) -> dict[str, str]:
    user = User(username=name, hashed_password=hash_password("Password123"))
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.username, scope='write')}"
    }


def test_cults_connection_is_encrypted_scoped_and_disconnects(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def accept_credentials(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        provider_service.CultsMetadataClient,
        "validate_credentials",
        accept_credentials,
    )
    owner = _headers(db_session, "provider-owner")
    other = _headers(db_session, "provider-other")
    response = client.post(
        "/api/v1/provider-connections/cults/connect",
        headers=owner,
        json={"username": "private-user", "password": "private-password"},
    )
    assert response.status_code == 200
    assert (
        client.get("/api/v1/provider-connections", headers=other).json()[1]["connected"]
        is False
    )
    row = db_session.exec(
        select(ProviderConnection)
        .where(ProviderConnection.provider == CaptureProvider.CULTS)
        .order_by(col(ProviderConnection.updated_at).desc())
    ).first()
    assert row is not None and row.id is not None
    encrypted = (
        db_session.connection()
        .exec_driver_sql(
            "SELECT credential_secret FROM provider_connections WHERE id = ?", (row.id,)
        )
        .scalar_one()
    )
    assert "private-password" not in encrypted
    assert row.credential_secret == "private-user\nprivate-password"
    import_resolvers._provider_metadata_cache[(1, "cults", "private-model")] = (
        ProviderModelMetadata("private-model", "stale", None, None, None),
        utcnow() + timedelta(minutes=5),
    )
    assert (
        client.delete(
            "/api/v1/provider-connections/cults/disconnect", headers=owner
        ).status_code
        == 204
    )
    assert (
        1,
        "cults",
        "private-model",
    ) not in import_resolvers._provider_metadata_cache
    assert (
        db_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.provider == CaptureProvider.CULTS
            )
        ).first()
        is None
    )


def test_mmf_authorize_is_disabled_without_deployment_credentials(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "mmf-owner")
    initial_states = db_session.exec(select(ProviderOAuthState)).all()
    auth = client.post(
        "/api/v1/provider-connections/myminifactory/authorize", headers=headers
    )
    assert auth.status_code == 503
    assert auth.json()["detail"] == "provider_not_configured"
    assert db_session.exec(select(ProviderOAuthState)).all() == initial_states


def test_mmf_callback_connects_one_owner_and_rejects_replay(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    monkeypatch.setattr(
        provider_service,
        "get_mmf_client",
        lambda: _ExchangeClient(MyMiniFactoryTokens("access", "refresh", 3600)),
    )
    headers = _headers(db_session, "mmf-owner")
    db_session.rollback()
    assert not db_session.in_transaction()
    db_session.close()
    auth = client.post(
        "/api/v1/provider-connections/myminifactory/authorize", headers=headers
    )
    assert auth.status_code == 200
    assert "client_id=client-id" in auth.json()["authorization_url"]
    state = auth.json()["authorization_url"].split("state=")[1].split("&")[0]
    assert (
        client.get(
            "/api/v1/provider-connections/myminifactory/callback?state=bad&code=x"
        ).json()["detail"]
        == "invalid_oauth_callback"
    )
    callback = client.get(
        f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=x"
    )
    assert callback.status_code == 200
    assert callback.json() == {"status": "connected"}
    connection = db_session.exec(
        select(ProviderConnection)
        .where(ProviderConnection.provider == CaptureProvider.MYMINIFACTORY)
        .order_by(col(ProviderConnection.updated_at).desc())
    ).first()
    assert connection is not None
    assert connection.user_id == 1
    assert connection.provider == CaptureProvider.MYMINIFACTORY
    assert connection.access_token == "access"
    db_session.rollback()
    assert not db_session.in_transaction()
    db_session.close()
    assert (
        client.get(
            f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=x"
        ).status_code
        == 400
    )
    row = db_session.exec(select(ProviderOAuthState)).one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    row.used_at = None
    db_session.commit()
    assert not db_session.in_transaction()
    db_session.close()
    assert (
        client.get(
            f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=x"
        ).status_code
        == 400
    )


def test_oauth_reservation_is_conditional_and_portable() -> None:
    statement = provider_service._oauth_reservation_statement(
        "state-hash", "https://vault.example/callback", utcnow()
    )
    sqlite_sql = str(statement.compile(dialect=sqlite.dialect()))
    postgres_sql = str(statement.compile(dialect=postgresql.dialect()))
    for rendered in (sqlite_sql, postgres_sql):
        assert "used_at IS NULL" in rendered
        assert "expires_at" in rendered
        assert "redirect_uri" in rendered


def test_repeated_oauth_reservation_exchanges_once(db_session: Session) -> None:
    user = User(username="oauth-race", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    assert user.id is not None
    raw = provider_service.begin_oauth(
        db_session, user.id, "https://vault.example/callback"
    )
    first = provider_service.consume_oauth(
        db_session, raw, "https://vault.example/callback"
    )
    second = provider_service.consume_oauth(
        db_session, raw, "https://vault.example/callback"
    )
    db_session.commit()

    assert first is not None
    assert second is None


@pytest.mark.anyio
async def test_concurrent_oauth_callbacks_exchange_code_once(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'oauth-race.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 0.5},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    redirect_uri = "https://vault.example/callback"
    with Session(engine) as setup:
        user = User(username="oauth-race-concurrent", hashed_password="x")
        setup.add(user)
        setup.flush()
        assert user.id is not None
        raw = provider_service.begin_oauth(setup, user.id, redirect_uri)
        setup.commit()

    class ExchangeClient:
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
            return MyMiniFactoryTokens("access", "refresh", 3600)

    client = ExchangeClient()
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)

    def callback(code: str) -> bool:
        with Session(engine) as session:
            result = asyncio.run(
                provider_service.finish_oauth(
                    session, state=raw, code=code, redirect_uri=redirect_uri
                )
            )
            # The API callback owns this final commit after the service has
            # reserved state and exchanged the code.
            session.commit()
            return result

    executor = ThreadPoolExecutor(max_workers=2)
    first = executor.submit(callback, "code-a")
    second = executor.submit(callback, "code-b")
    try:
        assert client.entered.wait(2)
        deadline = time.monotonic() + 2
        while not (first.done() or second.done()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert first.done() or second.done(), (
            "losing callback did not observe spent state"
        )
        assert client.calls == 1
        client.release.set()
        results = [first.result(timeout=2), second.result(timeout=2)]
    finally:
        client.release.set()
        executor.shutdown(wait=True, cancel_futures=True)
        engine.dispose()

    assert sorted(results) == [False, True]


class _ExchangeClient:
    def __init__(self, tokens: MyMiniFactoryTokens) -> None:
        self.tokens = tokens

    async def exchange_code(self, *_args, **_kwargs) -> MyMiniFactoryTokens:
        return self.tokens


@pytest.mark.anyio
async def test_mmf_metadata_uses_fresh_token_without_refresh(
    db_session: Session, monkeypatch
) -> None:
    connection = ProviderConnection(
        user_id=99,
        provider=CaptureProvider.MYMINIFACTORY,
        access_token="fresh-access",
        refresh_token="refresh",
        token_expires_at=utcnow() + timedelta(minutes=10),
    )
    db_session.add(connection)
    db_session.commit()
    client = _MetadataClient()
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)

    model = await provider_service.fetch_mmf_model_metadata(db_session, 99, "model-1")

    assert model.title == "Model"
    assert client.refresh_calls == []
    assert client.metadata_tokens == ["fresh-access"]


@pytest.mark.anyio
async def test_mmf_metadata_refreshes_expired_connection_and_preserves_omitted_refresh_token(
    db_session: Session, monkeypatch
) -> None:
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    connection = ProviderConnection(
        user_id=100,
        provider=CaptureProvider.MYMINIFACTORY,
        access_token="expired-access",
        refresh_token="old-refresh",
        token_expires_at=utcnow() - timedelta(seconds=1),
    )
    db_session.add(connection)
    db_session.commit()
    client = _MetadataClient(refreshed=MyMiniFactoryTokens("new-access", "", 3600))
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)

    await provider_service.fetch_mmf_model_metadata(db_session, 100, "model-1")

    assert client.refresh_calls == ["old-refresh"]
    assert client.metadata_tokens == ["new-access"]
    assert connection.access_token == "new-access"
    assert connection.refresh_token == "old-refresh"
    assert (
        connection.token_expires_at is not None
        and connection.token_expires_at.replace(tzinfo=timezone.utc) > utcnow()
    )


@pytest.mark.anyio
async def test_mmf_metadata_refresh_rotation_is_saved_and_failure_leaves_connection_intact(
    db_session: Session, monkeypatch
) -> None:
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    connection = ProviderConnection(
        user_id=101,
        provider=CaptureProvider.MYMINIFACTORY,
        access_token="expired-access",
        refresh_token="old-refresh",
        token_expires_at=utcnow() + timedelta(seconds=10),
    )
    db_session.add(connection)
    db_session.commit()
    client = _MetadataClient(
        refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
    )
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)

    await provider_service.fetch_mmf_model_metadata(db_session, 101, "model-1")
    assert connection.refresh_token == "new-refresh"

    connection.access_token = "expired-again"
    connection.token_expires_at = utcnow() - timedelta(seconds=1)
    failing_client = _MetadataClient(
        error=ProviderConnectionError("provider_auth_failed")
    )
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: failing_client)
    with pytest.raises(ProviderConnectionError, match="provider_auth_failed"):
        await provider_service.fetch_mmf_model_metadata(db_session, 101, "model-1")
    assert connection.access_token == "expired-again"
    assert connection.refresh_token == "new-refresh"


class _MetadataClient:
    def __init__(
        self,
        *,
        refreshed: MyMiniFactoryTokens | None = None,
        error: ProviderConnectionError | None = None,
    ) -> None:
        self.refreshed = refreshed
        self.error = error
        self.refresh_calls: list[str] = []
        self.metadata_tokens: list[str] = []

    async def refresh_tokens(
        self, _credentials, tokens: MyMiniFactoryTokens
    ) -> MyMiniFactoryTokens:
        self.refresh_calls.append(tokens.refresh_token)
        if self.error:
            raise self.error
        assert self.refreshed is not None
        return self.refreshed

    async def model_metadata(
        self, _model_id: str, tokens: MyMiniFactoryTokens
    ) -> ProviderModelMetadata:
        self.metadata_tokens.append(tokens.access_token)
        return ProviderModelMetadata("model-1", "Model", None, None, None)


@pytest.mark.anyio
async def test_mmf_token_rotation_invalidates_metadata_cache(
    db_session: Session, monkeypatch
) -> None:
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    connection = ProviderConnection(
        user_id=103,
        provider=CaptureProvider.MYMINIFACTORY,
        access_token="expired-access",
        refresh_token="old-refresh",
        token_expires_at=utcnow() - timedelta(seconds=1),
    )
    db_session.add(connection)
    db_session.commit()
    import_resolvers._provider_metadata_cache[(103, "myminifactory", "model-1")] = (
        ProviderModelMetadata("model-1", "stale", None, None, None),
        utcnow() + timedelta(minutes=5),
    )
    monkeypatch.setattr(
        provider_service,
        "get_mmf_client",
        lambda: _MetadataClient(
            refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
        ),
    )

    await provider_service.fetch_mmf_model_metadata(db_session, 103, "model-1")

    assert (
        103,
        "myminifactory",
        "model-1",
    ) not in import_resolvers._provider_metadata_cache


@pytest.mark.anyio
async def test_mmf_selected_download_refreshes_expired_connection(
    db_session: Session, monkeypatch
) -> None:
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    connection = ProviderConnection(
        user_id=104,
        provider=CaptureProvider.MYMINIFACTORY,
        access_token="expired-access",
        refresh_token="old-refresh",
        token_expires_at=utcnow() - timedelta(seconds=1),
    )
    db_session.add(connection)
    db_session.commit()

    class Client(_MetadataClient):
        def __init__(self) -> None:
            super().__init__(
                refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
            )
            self.download_tokens: list[str] = []

        async def file_download_url(
            self, _file_id: str, tokens: MyMiniFactoryTokens
        ) -> str:
            self.download_tokens.append(tokens.access_token)
            return "https://downloads.example.test/signed?token=transient"

    client = Client()
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)

    url = await provider_service.fetch_mmf_file_download_url(db_session, 104, "file-1")

    assert url == "https://downloads.example.test/signed?token=transient"
    assert client.refresh_calls == ["old-refresh"]
    assert client.download_tokens == ["new-access"]
    assert connection.access_token == "new-access"
    assert connection.refresh_token == "new-refresh"
    assert "transient" not in repr(connection)
    # The provider seam owns the successful rotation transaction.  A scoped
    # caller may close immediately after receiving the transient URL, so a
    # fresh session must observe the durable credentials.
    with Session(db_session.get_bind()) as fresh_session:
        persisted = fresh_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == 104,
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
            )
        ).one()
        assert persisted.access_token == "new-access"
        assert persisted.refresh_token == "new-refresh"


@pytest.mark.anyio
async def test_mmf_selected_download_rotation_commits_for_a_scoped_session(
    tmp_path, monkeypatch
) -> None:
    """A provider resolver's scoped-session close must not undo token rotation."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'provider-refresh.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    with Session(engine) as session:
        user = User(username="provider-refresh", hashed_password="x")
        session.add(user)
        session.flush()
        assert user.id is not None
        user_id = user.id
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
        # This unrelated write is intentionally left uncommitted.  A provider
        # rotation must not commit it while making the token durable.
        session.add(
            BrowserDevice(
                user_id=user_id,
                name="unrelated-pending-device",
                credential_hash="d" * 64,
            )
        )

        class Client(_MetadataClient):
            async def file_download_url(self, _file_id, _tokens):
                return "https://downloads.example.test/signed?token=transient"

        client = Client(
            refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
        )
        monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)
        await provider_service.fetch_mmf_file_download_url(session, user_id, "file-1")

    with Session(engine) as fresh_session:
        persisted = fresh_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == user_id,
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
            )
        ).one()
        assert persisted.access_token == "new-access"
        assert persisted.refresh_token == "new-refresh"
        assert not fresh_session.exec(select(BrowserDevice)).all()
    engine.dispose()


@pytest.mark.anyio
async def test_mmf_selected_download_rotation_rolls_back_after_second_401(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'provider-refresh-failure.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    with Session(engine) as session:
        user = User(username="provider-refresh-failure", hashed_password="x")
        session.add(user)
        session.flush()
        assert user.id is not None
        user_id = user.id
        session.add(
            ProviderConnection(
                user_id=user_id,
                provider=CaptureProvider.MYMINIFACTORY,
                access_token="old-access",
                refresh_token="old-refresh",
                token_expires_at=utcnow() + timedelta(minutes=10),
            )
        )
        session.commit()

        class Client(_MetadataClient):
            async def file_download_url(self, _file_id, _tokens):
                raise ProviderConnectionError("provider_auth_failed")

        client = Client(
            refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
        )
        monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)
        with pytest.raises(ProviderConnectionError, match="provider_auth_failed"):
            await provider_service.fetch_mmf_file_download_url(
                session, user_id, "file-1"
            )

    with Session(engine) as fresh_session:
        persisted = fresh_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == user_id,
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
            )
        ).one()
        assert persisted.access_token == "old-access"
        assert persisted.refresh_token == "old-refresh"
    engine.dispose()


@pytest.mark.anyio
async def test_mmf_selected_download_one_401_rotation_persists_in_fresh_session(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'provider-refresh-401.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    with Session(engine) as session:
        user = User(username="provider-refresh-401", hashed_password="x")
        session.add(user)
        session.flush()
        assert user.id is not None
        user_id = user.id
        session.add(
            ProviderConnection(
                user_id=user_id,
                provider=CaptureProvider.MYMINIFACTORY,
                access_token="old-access",
                refresh_token="old-refresh",
                token_expires_at=utcnow() + timedelta(minutes=10),
            )
        )
        session.commit()

        class Client(_MetadataClient):
            def __init__(self) -> None:
                super().__init__(
                    refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
                )
                self.calls = 0

            async def file_download_url(self, _file_id, _tokens):
                self.calls += 1
                if self.calls == 1:
                    raise ProviderConnectionError("provider_auth_failed")
                return "https://downloads.example.test/signed?token=transient"

        client = Client()
        monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)
        await provider_service.fetch_mmf_file_download_url(session, user_id, "file-1")

    with Session(engine) as fresh_session:
        persisted = fresh_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == user_id,
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
            )
        ).one()
        assert persisted.access_token == "new-access"
        assert persisted.refresh_token == "new-refresh"
    engine.dispose()


@pytest.mark.anyio
async def test_mmf_selected_download_retries_one_401_with_rotated_credentials(
    db_session: Session, monkeypatch
) -> None:
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    connection = ProviderConnection(
        user_id=105,
        provider=CaptureProvider.MYMINIFACTORY,
        access_token="old-access",
        refresh_token="old-refresh",
        token_expires_at=utcnow() + timedelta(minutes=10),
    )
    db_session.add(connection)
    db_session.commit()

    class Client(_MetadataClient):
        def __init__(self) -> None:
            super().__init__(
                refreshed=MyMiniFactoryTokens("new-access", "new-refresh", 3600)
            )
            self.download_tokens: list[str] = []

        async def file_download_url(
            self, _file_id: str, tokens: MyMiniFactoryTokens
        ) -> str:
            self.download_tokens.append(tokens.access_token)
            if len(self.download_tokens) == 1:
                raise ProviderConnectionError("provider_auth_failed")
            return "https://downloads.example.test/signed?token=transient"

    client = Client()
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)

    url = await provider_service.fetch_mmf_file_download_url(db_session, 105, "file-1")

    assert url == "https://downloads.example.test/signed?token=transient"
    assert client.download_tokens == ["old-access", "new-access"]
    assert client.refresh_calls == ["old-refresh"]
    assert connection.access_token == "new-access"
    assert connection.refresh_token == "new-refresh"
    with Session(db_session.get_bind()) as fresh_session:
        persisted = fresh_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == 105,
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
            )
        ).one()
        assert persisted.access_token == "new-access"
        assert persisted.refresh_token == "new-refresh"


@pytest.mark.anyio
async def test_mmf_401_refreshes_once_then_retries_with_rotated_token(
    db_session: Session, monkeypatch
) -> None:
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    connection = ProviderConnection(
        user_id=102,
        provider=CaptureProvider.MYMINIFACTORY,
        access_token="old",
        refresh_token="refresh",
        token_expires_at=utcnow() + timedelta(minutes=10),
    )
    db_session.add(connection)
    db_session.commit()

    class Client(_MetadataClient):
        async def model_metadata(self, _model_id, tokens):
            self.metadata_tokens.append(tokens.access_token)
            if len(self.metadata_tokens) == 1:
                raise ProviderConnectionError("provider_auth_failed")
            return ProviderModelMetadata("model-1", "Model", None, None, None)

    client = Client(refreshed=MyMiniFactoryTokens("new", "new-refresh", 3600))
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)
    await provider_service.fetch_mmf_model_metadata(db_session, 102, "model-1")
    assert client.metadata_tokens == ["old", "new"]
    assert client.refresh_calls == ["refresh"]
    assert connection.access_token == "new"


@pytest.mark.anyio
async def test_mmf_second_auth_failure_is_redacted_after_one_refresh(
    db_session: Session, monkeypatch
) -> None:
    _overlay["mmf_client_id"] = "client-id"
    _overlay["mmf_client_secret"] = "client-secret"
    connection = ProviderConnection(
        user_id=103,
        provider=CaptureProvider.MYMINIFACTORY,
        access_token="old-secret-token",
        refresh_token="refresh-secret",
        token_expires_at=utcnow() + timedelta(minutes=10),
    )
    db_session.add(connection)
    db_session.commit()

    class Client(_MetadataClient):
        async def model_metadata(self, _model_id, tokens):
            self.metadata_tokens.append(tokens.access_token)
            raise ProviderConnectionError("provider_auth_failed")

    client = Client(
        refreshed=MyMiniFactoryTokens("new-secret-token", "new-refresh", 3600)
    )
    monkeypatch.setattr(provider_service, "get_mmf_client", lambda: client)
    with pytest.raises(ProviderConnectionError, match="^provider_auth_failed$"):
        await provider_service.fetch_mmf_model_metadata(db_session, 103, "model-1")
    assert client.refresh_calls == ["refresh-secret"]
    assert client.metadata_tokens == ["old-secret-token", "new-secret-token"]
    # A rotated credential is not committed when the retry also fails.
    with Session(db_session.get_bind()) as fresh_session:
        persisted = fresh_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == 103,
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
            )
        ).one()
        assert persisted.access_token == "old-secret-token"
        assert persisted.refresh_token == "refresh-secret"
