"""Pagination determinism for the library browse / trash listings.

Models that share a sort timestamp (a batch ZIP import stamps many rows with
the same updated_at; a bulk trash shares deleted_at) must still paginate
without repeating or dropping rows. These tests pin the stable id tiebreaker.

The test DB seeds an ``__external__`` sentinel model, so assertions target the
models each test creates rather than the absolute row count.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from app.db.models import Model, User
from app.services import model_views as mv


@pytest.fixture
def superuser(db_session: Session) -> User:
    user = User(
        username="lister",
        hashed_password="x",
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_models(db_session: Session, *, count: int, ts: datetime) -> list[int]:
    ids: list[int] = []
    for i in range(count):
        m = Model(
            name=f"Model {i:02d}",
            slug=f"model-{i:02d}",
            hash=f"{i:064d}",
            updated_at=ts,
        )
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        ids.append(m.id)
    return ids


def _paginate_ids(fn, page_size: int) -> list[int]:
    seen: list[int] = []
    offset = 0
    while True:
        page = fn(limit=page_size, offset=offset)
        if not page:
            break
        seen.extend(item.id for item in page)
        offset += page_size
    return seen


def test_list_items_pagination_is_complete_and_unique_with_tied_timestamps(
    db_session: Session, superuser: User
) -> None:
    tied = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    created = set(_make_models(db_session, count=25, ts=tied))

    seen = _paginate_ids(
        lambda limit, offset: mv.list_items(
            db_session, superuser, limit=limit, offset=offset
        ),
        page_size=10,
    )

    # No row appears twice across page boundaries...
    assert len(seen) == len(set(seen)), "a model was duplicated across pages"
    # ...and every model we created shows up exactly once.
    assert created <= set(seen)
    assert sum(1 for i in seen if i in created) == 25


def test_list_items_order_is_stable_and_id_tiebroken(
    db_session: Session, superuser: User
) -> None:
    tied = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    created = set(_make_models(db_session, count=15, ts=tied))

    first = [m.id for m in mv.list_items(db_session, superuser, limit=100)]
    second = [m.id for m in mv.list_items(db_session, superuser, limit=100)]
    assert first == second, "ordering must be deterministic across calls"

    # Among the tied-timestamp models, order is strictly id-descending.
    mine = [i for i in first if i in created]
    assert mine == sorted(mine, reverse=True)


def test_list_items_search_is_case_insensitive(
    db_session: Session, superuser: User
) -> None:
    m = Model(name="Articulated Dragon", slug="dragon", hash="d" * 64)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    for query in ("dragon", "DRAGON", "Dragon", "drAGon"):
        found = {item.id for item in mv.list_items(db_session, superuser, q=query)}
        assert m.id in found, f"case-insensitive search failed for {query!r}"

    # A non-matching query must not return it.
    miss = {item.id for item in mv.list_items(db_session, superuser, q="griffin")}
    assert m.id not in miss


def test_list_items_excludes_external_sentinel(
    db_session: Session, superuser: User
) -> None:
    """The seeded ``__external__`` sentinel model must never surface in the grid
    (regression: it leaked into the library browse after a container restart)."""
    from app.db.models import SENTINEL_MODEL_HASH

    _make_models(db_session, count=3, ts=datetime(2026, 1, 1, tzinfo=timezone.utc))

    items = mv.list_items(db_session, superuser, limit=100)
    assert all(it.slug != "__external__" for it in items)
    # And the sentinel row really does exist in the DB, so this is a filter, not
    # an absence.
    from sqlmodel import select

    assert db_session.exec(
        select(Model).where(Model.hash == SENTINEL_MODEL_HASH)
    ).first() is not None


def test_list_trashed_pagination_is_complete_and_unique(
    db_session: Session, superuser: User
) -> None:
    tied = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    created: set[int] = set()
    for i in range(20):
        m = Model(
            name=f"Trashed {i:02d}",
            slug=f"trashed-{i:02d}",
            hash=f"{i + 1000:064d}",
            deleted_at=tied,
        )
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        created.add(m.id)

    seen = _paginate_ids(
        lambda limit, offset: mv.list_trashed(
            db_session, superuser, limit=limit, offset=offset, retention_days=30
        ),
        page_size=7,
    )

    assert len(seen) == len(set(seen)), "a trashed model was duplicated across pages"
    assert created <= set(seen)
    assert sum(1 for i in seen if i in created) == 20
