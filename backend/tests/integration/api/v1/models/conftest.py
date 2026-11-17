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

from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    User,
)
from tests import factories


@pytest.fixture
def make_model(db_session: Session):
    """A live model row. Slugs are generated, so two calls never collide."""
    made = {"n": 0}

    def build(name: str = "Bracket", **overrides: Any) -> Model:
        made["n"] += 1
        slug = overrides.pop("slug", f"model-{made['n']}")
        collection = overrides.pop("collection", None)
        return factories.build_model(
            db_session,
            name,
            collection=collection,
            slug=slug,
            hash=f"{made['n']:064d}",
            **overrides,
        )

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
    """The factory's builder, plus an explicit ``path`` for callers that pin one.

    It used to construct the row itself and silently ignored ``parent=``,
    leaving a child whose path claimed it was a root.
    """

    def build(
        name: str,
        *,
        path: str | None = None,
        parent: Collection | None = None,
        **overrides: Any,
    ) -> Collection:
        if path is not None:
            overrides["path"] = path
        return factories.build_collection(db_session, name, parent=parent, **overrides)

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
    """Bind the adapter to this test's enrolled roots, not a previous test's roots."""
    from tests._env import use_local_storage

    return use_local_storage(tmp_path)
