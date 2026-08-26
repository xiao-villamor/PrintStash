"""Exercise import progress through queued work and durable API state.

Failures mean clients can lose or misread an import's terminal outcome.
"""

from __future__ import annotations

import io
import json

# --------------------------------------------------------------------------- #
# Everything below drives ``app.api.v1.ingest`` internals directly — pure
# helpers, the pending-registry TTL, and the URL/archive/collection background
# tasks — following the same "patch the module's own network-facing
# functions" approach as ``test_import_resolvers.py``.
# --------------------------------------------------------------------------- #
import uuid as _uuid  # noqa: E402
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.v1 import ingest as ingest_module  # noqa: E402
from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import BackgroundJob, ExternalLibrary, User
from app.db.session import get_session_factory  # noqa: E402
from app.schemas.ingest import IngestJobStatus
from app.services import import_resolvers, importer, runtime_config  # noqa: E402
from app.services.auth import create_access_token, hash_password
from app.services.importer import ImportError_  # noqa: E402
from app.services.jobs import (
    JobRegistry,
    registry,  # noqa: E402
    safe_error,
    safe_item,
)


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    from app.core.config import settings

    settings.incoming_dir.mkdir(parents=True, exist_ok=True)


def _regular_user(session: Session, username: str = "regular") -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _cube_stl_bytes() -> bytes:
    return (
        b"solid cube\nfacet normal 0 0 1\nouter loop\n"
        b"endloop\nendfacet\nendsolid cube\n"
    )


# --------------------------------------------------------------------------- #
# ZIP archive endpoints: direct upload + inspect-in-background.
# --------------------------------------------------------------------------- #
def _zip_bytes(*, entry: str = "cube.stl", content: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr(entry, content or _cube_stl_bytes())
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# URL import endpoint + background task branches.
# --------------------------------------------------------------------------- #

__all__ = [
    "AsyncMock",
    "BackgroundJob",
    "ExternalLibrary",
    "ImportError_",
    "IngestJobStatus",
    "JobRegistry",
    "Path",
    "Session",
    "TestClient",
    "User",
    "_configure_storage",
    "_cube_stl_bytes",
    "_overlay",
    "_regular_user",
    "_uuid",
    "_zip_bytes",
    "create_access_token",
    "get_session_factory",
    "import_resolvers",
    "importer",
    "ingest_module",
    "io",
    "json",
    "patch",
    "pytest",
    "registry",
    "runtime_config",
    "safe_error",
    "safe_item",
    "timedelta",
    "utcnow",
    "zipfile",
]
