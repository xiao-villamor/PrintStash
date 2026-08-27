"""Defends ``test_cults_validation_failure_preserves_prior_secret`` behavior for the ``models`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, select

from app.db.models import CaptureProvider, ProviderConnection, User
from app.services import provider_connections
from app.services.auth import hash_password
from app.services.capture_provider_connections import ProviderConnectionError


def _user(session: Session) -> User:
    user = User(
        username="cults-validation", hashed_password=hash_password("Password123")
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None
    return user


def test_cults_validation_failure_preserves_prior_secret(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db_session)
    assert user.id is not None
    old = provider_connections.connect_cults(
        db_session, user.id, "old-user", "old-secret"
    )
    db_session.commit()

    class _Client:
        async def validate_credentials(self, _candidate):
            raise ProviderConnectionError("provider_auth_failed")

    monkeypatch.setattr(
        provider_connections, "CultsMetadataClient", lambda _transport: _Client()
    )
    with pytest.raises(ProviderConnectionError, match="provider_auth_failed"):
        asyncio.run(
            provider_connections.validate_and_connect_cults(
                db_session, user.id, "new-user", "new-secret"
            )
        )
    current = db_session.exec(
        select(ProviderConnection).where(
            ProviderConnection.provider == CaptureProvider.CULTS
        )
    ).one()
    assert current.credential_secret == old.credential_secret


def test_cults_validation_rotates_only_after_success(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db_session)
    assert user.id is not None

    class _Client:
        async def validate_credentials(self, _candidate):
            return None

    monkeypatch.setattr(
        provider_connections, "CultsMetadataClient", lambda _transport: _Client()
    )
    row = asyncio.run(
        provider_connections.validate_and_connect_cults(
            db_session, user.id, "new-user", "new-secret"
        )
    )
    assert row.credential_secret == "new-user\nnew-secret"
