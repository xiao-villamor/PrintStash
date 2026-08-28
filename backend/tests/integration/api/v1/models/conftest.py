"""Shared row builders for the `/models` endpoint groups.

`app/api/v1/models.py` is the widest router in the app, so its mirror is a folder split by
endpoint group. Nearly every group needs the same two or three rows — a model, a file
under it, sometimes a collection with a role granted on it — and building them inline in
each file is how the same helper came to exist in four slightly different forms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    User,
)


@pytest.fixture
def make_model(db_session: Session):
    """A live model row. Slugs are generated, so two calls never collide."""
    made = {"n": 0}

    def build(name: str = "Bracket", **overrides: Any) -> Model:
        made["n"] += 1
        slug = overrides.pop("slug", f"model-{made['n']}")
        row = Model(
            name=name,
            slug=slug,
            hash=f"{made['n']:064d}",
            **overrides,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


@pytest.fixture
def make_file(db_session: Session):
    """A file under a model, versioned so repeated calls do not collide."""
    made = {"n": 0}

    def build(
        model: Model,
        *,
        file_type: FileType = FileType.GCODE,
        filename: str | None = None,
        **overrides: Any,
    ) -> File:
        made["n"] += 1
        name = filename or f"file-{made['n']}.gcode"
        row = File(
            model_id=model.id,
            path=f"/data/files/{model.slug}/v{made['n']}/{name}",
            original_filename=name,
            file_type=file_type,
            version=overrides.pop("version", made["n"]),
            size_bytes=overrides.pop("size_bytes", 123),
            sha256=overrides.pop("sha256", f"{made['n']:064d}"),
            **overrides,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


@pytest.fixture
def make_collection(db_session: Session):
    def build(name: str, *, path: str | None = None, **overrides: Any) -> Collection:
        slug = overrides.pop("slug", name.lower().replace(" ", "-"))
        row = Collection(name=name, slug=slug, path=path or slug, **overrides)
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


@pytest.fixture
def grant_role(db_session: Session):
    """Give a user a role on a collection, the way an admin share would."""

    def grant(user: User, collection: Collection, role: CollectionRole) -> None:
        db_session.add(
            CollectionPermission(
                user_id=user.id, collection_id=collection.id, role=role
            )
        )
        db_session.commit()

    return grant


@pytest.fixture
def local_storage(tmp_path: Path):
    """Point every storage directory at a throwaway tree for this test."""
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    return tmp_path
