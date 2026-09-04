"""Shared setup for `app/services/provider_connections.py`.

The concurrency rows in this folder need a *real* on-disk SQLite file: the suite's
in-memory engine is shared through one connection, and the properties under test — a
per-user write lock, a conditional one-time reservation — only mean anything across
independent connections. `file_engine` builds one with the production pragmas.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine

from app.core.config import _overlay
from app.db.session import _set_sqlite_pragmas

MMF_CLIENT_ID = "test-client-id"
MMF_CLIENT_SECRET = "test-client-secret"


@pytest.fixture
def file_engine(tmp_path):
    """A throwaway on-disk SQLite database that several connections can share."""

    def build(name: str):
        engine = create_engine(
            f"sqlite:///{tmp_path / name}.sqlite",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        engines.append(engine)
        return engine

    engines: list = []
    yield build
    for engine in engines:
        engine.dispose()


@pytest.fixture
def mmf_configured() -> Iterator[None]:
    """A deployment that has MyMiniFactory OAuth credentials configured."""
    _overlay["mmf_client_id"] = MMF_CLIENT_ID
    _overlay["mmf_client_secret"] = MMF_CLIENT_SECRET
    yield
    _overlay.pop("mmf_client_id", None)
    _overlay.pop("mmf_client_secret", None)
