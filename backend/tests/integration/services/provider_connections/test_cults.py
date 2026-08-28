"""Storing, validating, and dropping a Cults login.

Cults has no OAuth: the credential PrintStash holds *is* the user's username and
password. Two rules follow from that and are what this file defends. The pair is stored
as one encrypted field, so a database dump is not a password dump. And a candidate pair
is validated against the provider **before** it replaces the stored one — a typo must
leave the working login intact rather than silently breaking every future import.

Dropping a connection also drops the owner's cached provider metadata: serving data
fetched with a credential the user just revoked is a leak.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import CaptureProvider, ProviderConnection, User
from app.services import import_resolvers
from app.services import provider_connections as service
from app.services.capture_provider_connections import (
    ProviderConnectionError,
    ProviderModelMetadata,
)
from tests.factories import build_user


class _AcceptingCults:
    async def validate_credentials(self, _candidate: object) -> None:
        return None


class _RejectingCults:
    async def validate_credentials(self, _candidate: object) -> None:
        raise ProviderConnectionError("provider_auth_failed")


@pytest.fixture
def user(db_session: Session) -> User:
    row = build_user(db_session, "cults-service")
    assert row.id is not None
    return row


class TestHasActiveProviderConnection:
    def test_reports_a_cults_login_that_is_stored(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        service.connect_cults(db_session, user.id, "someone", "secret")

        assert (
            service.has_active_provider_connection(
                db_session, user.id, CaptureProvider.CULTS
            )
            is True
        )

    def test_reports_a_myminifactory_connection_that_has_both_tokens(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        db_session.add(
            ProviderConnection(
                user_id=user.id,
                provider=CaptureProvider.MYMINIFACTORY,
                access_token="access",
                refresh_token="refresh",
            )
        )
        db_session.flush()

        assert (
            service.has_active_provider_connection(
                db_session, user.id, CaptureProvider.MYMINIFACTORY
            )
            is True
        )

    def test_rejects_a_myminifactory_row_that_lost_its_refresh_token(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        db_session.add(
            ProviderConnection(
                user_id=user.id,
                provider=CaptureProvider.MYMINIFACTORY,
                access_token="access",
                refresh_token=None,
            )
        )
        db_session.flush()

        # A half-written row cannot be refreshed, so it is not a connection.
        assert (
            service.has_active_provider_connection(
                db_session, user.id, CaptureProvider.MYMINIFACTORY
            )
            is False
        )

    def test_rejects_a_cults_row_whose_secret_is_not_a_pair(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        row = ProviderConnection(user_id=user.id, provider=CaptureProvider.CULTS)
        row.credential_secret = "username-with-no-password"
        db_session.add(row)
        db_session.flush()

        assert (
            service.has_active_provider_connection(
                db_session, user.id, CaptureProvider.CULTS
            )
            is False
        )

    def test_reports_no_connection_when_there_is_no_row(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        assert (
            service.has_active_provider_connection(
                db_session, user.id, CaptureProvider.CULTS
            )
            is False
        )

    def test_drops_the_cached_metadata_when_there_is_no_row(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        key = (user.id, "cults", "cached-model")
        import_resolvers._provider_metadata_cache[key] = (
            ProviderModelMetadata("cached-model", "stale", None, None, None),
            utcnow() + timedelta(minutes=5),
        )

        service.has_active_provider_connection(
            db_session, user.id, CaptureProvider.CULTS
        )

        assert key not in import_resolvers._provider_metadata_cache


class TestConnectCults:
    def test_stores_the_credential_pair_as_one_secret(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        row = service.connect_cults(db_session, user.id, "someone", "secret")

        assert row.credential_secret == "someone\nsecret"

    def test_replaces_the_secret_on_an_existing_connection(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        service.connect_cults(db_session, user.id, "old-user", "old-secret")

        service.connect_cults(db_session, user.id, "new-user", "new-secret")

        row = db_session.exec(select(ProviderConnection)).one()
        assert row.credential_secret == "new-user\nnew-secret"

    def test_drops_the_owners_cached_metadata(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        key = (user.id, "cults", "cached-model")
        import_resolvers._provider_metadata_cache[key] = (
            ProviderModelMetadata("cached-model", "stale", None, None, None),
            utcnow() + timedelta(minutes=5),
        )

        service.connect_cults(db_session, user.id, "someone", "secret")

        assert key not in import_resolvers._provider_metadata_cache


class TestValidateAndConnectCults:
    def test_stores_a_pair_the_provider_accepts(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert user.id is not None
        monkeypatch.setattr(
            service, "CultsMetadataClient", lambda _transport: _AcceptingCults()
        )

        row = asyncio.run(
            service.validate_and_connect_cults(
                db_session, user.id, "new-user", "new-secret"
            )
        )

        assert row.credential_secret == "new-user\nnew-secret"

    def test_raises_when_the_provider_rejects_the_pair(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert user.id is not None
        monkeypatch.setattr(
            service, "CultsMetadataClient", lambda _transport: _RejectingCults()
        )

        with pytest.raises(ProviderConnectionError, match="provider_auth_failed"):
            asyncio.run(
                service.validate_and_connect_cults(
                    db_session, user.id, "new-user", "new-secret"
                )
            )

    def test_leaves_the_working_login_in_place_when_validation_fails(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert user.id is not None
        service.connect_cults(db_session, user.id, "old-user", "old-secret")
        db_session.commit()
        monkeypatch.setattr(
            service, "CultsMetadataClient", lambda _transport: _RejectingCults()
        )

        with pytest.raises(ProviderConnectionError):
            asyncio.run(
                service.validate_and_connect_cults(
                    db_session, user.id, "new-user", "new-secret"
                )
            )

        # Overwriting a working login with a typo would break every future import.
        current = db_session.exec(select(ProviderConnection)).one()
        assert current.credential_secret == "old-user\nold-secret"


class TestFetchCultsModelMetadata:
    def test_refuses_when_the_user_has_no_cults_connection(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        with pytest.raises(ProviderConnectionError, match="provider_not_connected"):
            asyncio.run(
                service.fetch_cults_model_metadata(db_session, user.id, "some-model")
            )

    def test_refuses_when_the_stored_secret_is_not_a_pair(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        row = ProviderConnection(user_id=user.id, provider=CaptureProvider.CULTS)
        row.credential_secret = "username-with-no-password"
        db_session.add(row)
        db_session.flush()

        with pytest.raises(ProviderConnectionError, match="provider_not_connected"):
            asyncio.run(
                service.fetch_cults_model_metadata(db_session, user.id, "some-model")
            )

    def test_asks_the_provider_with_the_stored_pair(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert user.id is not None
        service.connect_cults(db_session, user.id, "someone", "secret")
        seen: list[tuple[str, str, str]] = []

        class _Client:
            async def creation_metadata(self, slug: str, credentials):
                seen.append((slug, credentials.username, credentials.password))
                return ProviderModelMetadata(slug, "Widget", None, None, None)

        monkeypatch.setattr(
            service, "CultsMetadataClient", lambda _transport: _Client()
        )

        metadata = asyncio.run(
            service.fetch_cults_model_metadata(db_session, user.id, "widget")
        )

        assert metadata.title == "Widget"
        assert seen == [("widget", "someone", "secret")]


class TestDisconnectProviderConnection:
    def test_removes_the_connection(self, db_session: Session, user: User) -> None:
        assert user.id is not None
        service.connect_cults(db_session, user.id, "someone", "secret")

        removed = service.disconnect_provider_connection(
            db_session, user.id, CaptureProvider.CULTS
        )

        assert removed is True
        db_session.flush()
        assert db_session.exec(select(ProviderConnection)).all() == []

    def test_reports_nothing_removed_when_there_was_no_connection(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        assert (
            service.disconnect_provider_connection(
                db_session, user.id, CaptureProvider.CULTS
            )
            is False
        )

    def test_drops_the_owners_cached_metadata_even_when_there_was_no_connection(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        key = (user.id, "cults", "cached-model")
        import_resolvers._provider_metadata_cache[key] = (
            ProviderModelMetadata("cached-model", "stale", None, None, None),
            utcnow() + timedelta(minutes=5),
        )

        service.disconnect_provider_connection(
            db_session, user.id, CaptureProvider.CULTS
        )

        assert key not in import_resolvers._provider_metadata_cache
