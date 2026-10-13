"""Shared row builders for the `/files` endpoint groups.

Every group here needs the same two rows — a model and a file pointing at a storage
key — and the same escape hatch for making a blob disappear the way an out-of-band
delete would. The storage backend is a process singleton that survives the per-test
database wipe, so a key written by an earlier test can still be sitting there; the
`remove_blob` fixture is how a test states "this key is empty" without going through the
production delete path, which deliberately refuses unowned objects.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import Session

from app.db.models import File, FileType, Model
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories.library import build_file, build_model


@pytest.fixture
def make_model(db_session: Session):
    def build(
        slug: str, *, name: str = "M", hash_: str | None = None, **fields: Any
    ) -> Model:
        if hash_ is not None:
            fields["hash"] = hash_
        return build_model(db_session, name=name, slug=slug, **fields)

    return build


@pytest.fixture
def make_file(db_session: Session):
    def build(
        model: Model,
        *,
        filename: str = "part.stl",
        ftype: str = "stl",
        path: str | None = None,
        size_bytes: int = 10,
        sha256: str | None = None,
        **fields: Any,
    ) -> File:
        if sha256 is not None:
            fields["sha256"] = sha256
        external = fields.pop("is_external", False)
        return build_file(
            db_session,
            model,
            filename=filename,
            file_type=FileType(ftype),
            path=path or f"/nonexistent/{filename}",
            size_bytes=size_bytes,
            external=external,
            **fields,
        )

    return build


@pytest.fixture
def remove_blob():
    """Make a storage key empty, the way a delete outside PrintStash would."""

    def remove(key: str) -> None:
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.unlink(missing_ok=True)

    return remove
