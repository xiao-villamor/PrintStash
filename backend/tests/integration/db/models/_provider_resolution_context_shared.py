"""Exercise provider resolution with the request's active session factory.

Failures expose cross-context reads that can resolve the wrong connection state.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import cast

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    CaptureProvider,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    ProviderConnection,
    User,
)
from app.db.session import SessionFactory, SQLiteSessionFactory
from app.services import import_resolvers, provider_connections
from app.services.capture_provider_connections import (
    ProviderConnectionError,
    ProviderFileMetadata,
    ProviderIdentity,
    ProviderModelMetadata,
)


class _Factory:
    def scoped_session(self):
        class _Session:
            def __enter__(self):
                return object()

            def __exit__(self, *_args):
                return False

        return _Session()


def _factory() -> SessionFactory:
    return cast(SessionFactory, _Factory())


__all__ = [
    "CaptureProvider",
    "InboxItem",
    "InboxItemState",
    "InboxSourceKind",
    "ProviderConnection",
    "ProviderConnectionError",
    "ProviderFileMetadata",
    "ProviderIdentity",
    "ProviderModelMetadata",
    "SQLiteSessionFactory",
    "Session",
    "User",
    "_factory",
    "asyncio",
    "import_resolvers",
    "json",
    "provider_connections",
    "pytest",
    "timedelta",
    "utcnow",
]
