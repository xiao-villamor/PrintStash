"""A library at a size where the cost of a read shows: thousands of collections.

The per-row builders commit one row at a time, which is right for a test's
handful of rows and hopeless for the 25,000 collections and 100,000 Models
PrintStash commits to serving (#295). This builder writes the same rows in
bulk, encoded exactly as ``build_collection`` and ``build_model`` encode them:
live, with ``path`` extending the parent's, and each Model filed in a
collection. Unique columns are derived from row ids with a scheme no per-row
builder uses, so a scaled library and ordinary rows can share a test.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import func, insert
from sqlmodel import Session, select

from app.db.models import Collection, Model

_BATCH = 500


@dataclass(frozen=True)
class ScaledLibrary:
    """What was seeded: enough to assert against without re-reading it."""

    collection_ids: tuple[int, ...]
    model_count: int


def _columns(row: Collection | Model) -> dict:
    """The row's column values, defaults included, ready for a bulk insert."""
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns  # type: ignore[attr-defined]
    }


def build_library_at_scale(
    session: Session,
    *,
    collections: int,
    models: int,
    under: Collection | None = None,
    fanout: int = 8,
) -> ScaledLibrary:
    """Seed a tree of *collections* holding *models*, breadth first.

    Each level has *fanout* children per collection, so depth grows with the
    logarithm of the size, like a real folder tree. Pass *under* to grow the
    tree beneath an existing collection (one a user was granted, say); without
    it the first level is *fanout* roots. Models are spread round-robin over
    every seeded collection. Commits, like every builder.
    """
    if collections < 1 or models < 0 or fanout < 1:
        raise ValueError("library_scale_shape_invalid")
    next_id = (session.exec(select(func.max(Collection.id))).one() or 0) + 1
    level: list[tuple[int | None, str | None]] = [
        (under.id, under.path) if under is not None else (None, None)
    ]
    seeded: list[Collection] = []
    while len(seeded) < collections:
        following: list[tuple[int | None, str | None]] = []
        for parent_id, parent_path in level:
            for _ in range(fanout):
                if len(seeded) == collections:
                    break
                slug = f"scale-{next_id}"
                path = f"{parent_path}/{slug}" if parent_path else slug
                seeded.append(
                    Collection(
                        id=next_id,
                        name=f"Scale folder {next_id}",
                        slug=slug,
                        path=path,
                        parent_id=parent_id,
                    )
                )
                following.append((next_id, path))
                next_id += 1
        level = following
    rows = [_columns(collection) for collection in seeded]
    for start in range(0, len(rows), _BATCH):
        session.execute(insert(Collection.__table__), rows[start : start + _BATCH])  # type: ignore[arg-type]

    first_model = (session.exec(select(func.max(Model.id))).one() or 0) + 1
    # One Model carries every default; each row copies it and replaces only its
    # identity. Building 100,000 validated Models would dominate the seeding.
    template = _columns(Model(name="", slug="", hash="", collection_id=None))
    model_rows = []
    for offset in range(models):
        model_id = first_model + offset
        model_rows.append(
            template
            | {
                "id": model_id,
                "name": f"Scale model {model_id}",
                "slug": f"scale-model-{model_id}",
                "hash": hashlib.sha256(f"scale-model-{model_id}".encode()).hexdigest(),
                "collection_id": seeded[offset % len(seeded)].id,
            }
        )
        if len(model_rows) == _BATCH:
            session.execute(insert(Model.__table__), model_rows)  # type: ignore[arg-type]
            model_rows = []
    if model_rows:
        session.execute(insert(Model.__table__), model_rows)  # type: ignore[arg-type]
    session.commit()
    return ScaledLibrary(
        collection_ids=tuple(int(c.id) for c in seeded if c.id is not None),
        model_count=models,
    )
