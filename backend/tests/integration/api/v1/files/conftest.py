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

from app.db.models import File, Model
from app.services.storage_backend import get_backend


@pytest.fixture
def make_model(db_session: Session):
    def build(
        slug: str, *, name: str = "M", hash_: str | None = None, **fields: Any
    ) -> Model:
        row = Model(name=name, slug=slug, hash=hash_ or f"{slug:h<64}"[:64], **fields)
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


@pytest.fixture
def make_file(db_session: Session):
    def build(
        model: Model,
        *,
        filename: str = "part.stl",
        ftype: str = "stl",
        path: str | None = None,
        sha256: str = "a" * 64,
        **fields: Any,
    ) -> File:
        row = File(
            model_id=model.id,
            path=path or f"/nonexistent/{filename}",
            original_filename=filename,
            file_type=ftype,
            version=1,
            size_bytes=10,
            sha256=sha256,
            **fields,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


@pytest.fixture
def remove_blob():
    """Make a storage key empty, the way a delete outside PrintStash would."""

    def remove(key: str) -> None:
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.unlink(missing_ok=True)

    return remove
