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
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import File, FileType, Metadata, Model, PrintJob, PrintJobState, User
from app.schemas.models import ModelFilters, ModelSort
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

    assert (
        db_session.exec(select(Model).where(Model.hash == SENTINEL_MODEL_HASH)).first()
        is not None
    )


def test_list_items_includes_daily_workflow_print_outcomes(
    db_session: Session, superuser: User
) -> None:
    model = Model(name="Outcome", slug="outcome", hash="o" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    artifact = File(
        model_id=model.id,
        path="outcome.gcode",
        original_filename="outcome.gcode",
        file_type=FileType.GCODE,
        size_bytes=10,
        sha256="f" * 64,
    )
    db_session.add(artifact)
    db_session.commit()
    db_session.refresh(artifact)
    finished = datetime(2026, 2, 1, tzinfo=timezone.utc)
    db_session.add_all(
        [
            PrintJob(
                model_id=model.id,
                file_id=artifact.id,
                remote_filename="outcome.gcode",
                state=PrintJobState.COMPLETED,
                actual_duration_s=120,
                cost=1.25,
                finished_at=finished,
            ),
            PrintJob(
                model_id=model.id,
                file_id=artifact.id,
                remote_filename="outcome.gcode",
                state=PrintJobState.FAILED,
                actual_duration_s=60,
                cost=0.25,
                finished_at=finished,
            ),
        ]
    )
    db_session.commit()

    item = next(
        row
        for row in mv.list_items(db_session, superuser, limit=100)
        if row.id == model.id
    )

    assert item.print_summary is not None
    assert item.print_summary.success_rate == 0.5
    assert item.print_summary.average_duration_s == 90
    assert item.print_summary.total_cost == 1.5
    assert item.print_summary.last_printed_at == finished


def _cursor_page_ids(
    db_session: Session,
    superuser: User,
    sort: ModelSort,
    *,
    page_size: int = 1,
) -> tuple[list[int], int]:
    ids: list[int] = []
    cursor: str | None = None
    total = 0
    while True:
        page = mv.page_items(
            db_session,
            superuser,
            filters=ModelFilters(),
            sort=sort,
            cursor=cursor,
            limit=page_size,
        )
        ids.extend(row.id for row in page.items)
        total = page.total
        cursor = page.next_cursor
        if cursor is None:
            return ids, total


def test_cursor_page_name_sort_is_global_complete_and_unique(
    db_session: Session, superuser: User
) -> None:
    created: dict[int, str] = {}
    for index, name in enumerate(["Zulu", "alpha", "Echo", "bravo", "Delta"]):
        model = Model(
            name=name,
            slug=f"cursor-{index}",
            hash=f"c{index:063d}",
        )
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        created[model.id] = name

    ids, total = _cursor_page_ids(
        db_session, superuser, ModelSort.NAME_ASC, page_size=2
    )
    names = [created[model_id] for model_id in ids if model_id in created]

    assert names == ["alpha", "bravo", "Delta", "Echo", "Zulu"]
    assert len(ids) == len(set(ids))
    assert total == len(created)


def test_cursor_pages_apply_every_metric_sort_globally(
    db_session: Session, superuser: User
) -> None:
    finished = [
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 3, tzinfo=timezone.utc),
    ]
    specs = [
        ("A", 3.0, 100, [(PrintJobState.COMPLETED, 100, 5.0, finished[0])]),
        (
            "B",
            2.0,
            50,
            [
                (PrintJobState.COMPLETED, 50, 1.0, finished[1]),
                (PrintJobState.FAILED, 50, 1.0, finished[1]),
            ],
        ),
        ("C", 1.0, 25, []),
    ]
    ids_by_name: dict[str, int] = {}
    for index, (name, filament, estimate, jobs) in enumerate(specs):
        model = Model(name=name, slug=f"metric-{name.lower()}", hash=f"d{index:063d}")
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        artifact = File(
            model_id=model.id,
            path=f"{name}.gcode",
            original_filename=f"{name}.gcode",
            file_type=FileType.GCODE,
            size_bytes=10,
            sha256=f"e{index:063d}",
        )
        db_session.add(artifact)
        db_session.commit()
        db_session.refresh(artifact)
        db_session.add(
            Metadata(
                file_id=artifact.id,
                estimated_time_s=estimate,
                filament_weight_g=filament,
            )
        )
        for state, duration, cost, finished_at in jobs:
            db_session.add(
                PrintJob(
                    model_id=model.id,
                    file_id=artifact.id,
                    remote_filename=artifact.original_filename,
                    state=state,
                    actual_duration_s=duration,
                    cost=cost,
                    finished_at=finished_at,
                )
            )
        db_session.commit()
        ids_by_name[name] = model.id

    expected = {
        ModelSort.SUCCESS_DESC: ["A", "B", "C"],
        ModelSort.PRINTED_DESC: ["B", "A", "C"],
        ModelSort.DURATION_ASC: ["C", "B", "A"],
        ModelSort.FILAMENT_ASC: ["C", "B", "A"],
        ModelSort.COST_ASC: ["B", "A", "C"],
    }
    name_by_id = {model_id: name for name, model_id in ids_by_name.items()}
    for sort, expected_names in expected.items():
        ids, _ = _cursor_page_ids(db_session, superuser, sort)
        names = [name_by_id[model_id] for model_id in ids if model_id in name_by_id]
        assert names == expected_names, sort


def test_cursor_page_rejects_sort_mismatch(
    db_session: Session, superuser: User
) -> None:
    first = mv.page_items(
        db_session,
        superuser,
        filters=ModelFilters(),
        sort=ModelSort.DATE_DESC,
        limit=1,
    )
    if first.next_cursor is None:
        _make_models(
            db_session,
            count=2,
            ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        first = mv.page_items(
            db_session,
            superuser,
            filters=ModelFilters(),
            sort=ModelSort.DATE_DESC,
            limit=1,
        )
    assert first.next_cursor is not None
    with pytest.raises(ValueError, match="invalid_model_cursor"):
        mv.page_items(
            db_session,
            superuser,
            filters=ModelFilters(),
            sort=ModelSort.NAME_ASC,
            cursor=first.next_cursor,
            limit=1,
        )
    with pytest.raises(ValueError, match="invalid_model_cursor"):
        mv.page_items(
            db_session,
            superuser,
            filters=ModelFilters(q="different filter"),
            sort=ModelSort.DATE_DESC,
            cursor=first.next_cursor,
            limit=1,
        )


def test_model_page_api_uses_global_sort_and_cursor(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    for index, name in enumerate(["API Page Zulu", "API Page Alpha"]):
        db_session.add(
            Model(
                name=name,
                slug=f"api-page-{index}",
                hash=f"a{index:063d}",
            )
        )
    db_session.commit()

    first = client.get(
        "/api/v1/models/page?q=API%20Page&sort=name-asc&limit=1",
        headers=auth_headers,
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert [item["name"] for item in first_payload["items"]] == ["API Page Alpha"]
    assert first_payload["total"] == 2
    assert first_payload["next_cursor"]

    second = client.get(
        "/api/v1/models/page",
        params={
            "q": "API Page",
            "sort": "name-asc",
            "limit": 1,
            "cursor": first_payload["next_cursor"],
        },
        headers=auth_headers,
    )
    assert second.status_code == 200
    assert [item["name"] for item in second.json()["items"]] == ["API Page Zulu"]
    assert second.json()["next_cursor"] is None


def test_model_page_api_rejects_invalid_cursor_and_outliner_is_lightweight(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = Model(name="Outliner Leaf", slug="outliner-leaf", hash="u" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    invalid = client.get(
        "/api/v1/models/page?cursor=not-a-cursor",
        headers=auth_headers,
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "invalid_model_cursor"

    outliner = client.get("/api/v1/models/outliner", headers=auth_headers)
    assert outliner.status_code == 200
    leaf = next(item for item in outliner.json() if item["id"] == model.id)
    assert set(leaf) == {"id", "name", "collection", "collection_id"}


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
