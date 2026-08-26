"""Exercise provider connection ownership, metadata, and secret persistence.

Failures expose credentials or connection state across an account boundary.
"""

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


class _ExchangeClient:
    def __init__(self, tokens: MyMiniFactoryTokens) -> None:
        self.tokens = tokens

    async def exchange_code(self, *_args, **_kwargs) -> MyMiniFactoryTokens:
        return self.tokens


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


__all__ = [
    "BrowserDevice",
    "CaptureProvider",
    "MyMiniFactoryTokens",
    "ProviderConnection",
    "ProviderConnectionError",
    "ProviderModelMetadata",
    "ProviderOAuthState",
    "SQLModel",
    "Session",
    "TestClient",
    "ThreadPoolExecutor",
    "User",
    "_ExchangeClient",
    "_MetadataClient",
    "_headers",
    "_overlay",
    "_set_sqlite_pragmas",
    "asyncio",
    "col",
    "create_engine",
    "event",
    "import_resolvers",
    "postgresql",
    "provider_service",
    "pytest",
    "select",
    "sqlite",
    "threading",
    "time",
    "timedelta",
    "timezone",
    "utcnow",
]
