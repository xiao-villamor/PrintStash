"""Defends mmf selected download rotation rolls back after second 401 at the services provider connections integration boundary.

A regression could expose provider secrets or persist a broken credential rotation.
"""

from __future__ import annotations

from ._provider_connections_shared import (
    CaptureProvider,
    MyMiniFactoryTokens,
    ProviderConnection,
    ProviderConnectionError,
    ProviderModelMetadata,
    Session,
    SQLModel,
    User,
    _MetadataClient,
    _overlay,
    _set_sqlite_pragmas,
    create_engine,
    event,
    provider_service,
    pytest,
    select,
    timedelta,
    utcnow,
)


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
