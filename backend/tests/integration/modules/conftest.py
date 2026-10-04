"""Share persisted Artifact input across capability integration contracts."""

from __future__ import annotations

import hashlib

import pytest
from sqlmodel import Session

from app.db.models import File
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import build_file, build_model


@pytest.fixture
def stored(db_session: Session):
    """An Artifact whose bytes are on the backend, as ingestion leaves it."""

    def build(filename: str, data: bytes, **fields) -> File:
        model = build_model(db_session, filename)
        row = build_file(
            db_session,
            model,
            filename=filename,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            **fields,
        )
        get_backend().write_bytes(data, row.path)
        return row

    return build
