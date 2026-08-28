"""Paginating the library and the trash without dropping or repeating a row.

Ties are the normal case here, not the corner: a batch ZIP import stamps every model with
the same `updated_at`, and a bulk trash shares one `deleted_at`. Sorting on that column
alone leaves the database free to order tied rows differently on each query, so page two
can repeat a row page one already showed and skip one it never did. The id tiebreaker is
what makes the order total, and these tests walk every page and check the set.

The cursor carries the sort and the filters it was issued under. Presenting it back with a
different sort would resume from a position that no longer means anything, so it is
rejected rather than honoured.

The test database seeds an `__external__` sentinel model, so assertions target the models
each test creates rather than an absolute row count.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from app.db.models import FileType, Metadata, Model, PrintJobState, User
from app.schemas.models import ModelFilters, ModelSort
from app.services import model_views as mv
from tests.factories import (
    build_file,
    build_model,
    build_print_job,
    build_user,
)


@pytest.fixture
def superuser(db_session: Session) -> User:
    user = build_user(db_session, "lister", superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_models(db_session: Session, *, count: int, ts: datetime) -> list[int]:
    ids: list[int] = []
    for i in range(count):
        m = build_model(
            db_session,
            name=f"Model {i:02d}",
            slug=f"model-{i:02d}",
            hash=f"{i:064d}",
            updated_at=ts,
        )
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


class TestListItems:
    def test_pagination_visits_every_model_once_when_timestamps_tie(
        self, db_session: Session, superuser: User
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

    def test_ordering_is_deterministic_when_timestamps_tie(
        self, db_session: Session, superuser: User
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
        self, db_session: Session, superuser: User
    ) -> None:
        m = build_model(
            db_session, name="Articulated Dragon", slug="dragon", hash="d" * 64
        )

        for query in ("dragon", "DRAGON", "Dragon", "drAGon"):
            found = {item.id for item in mv.list_items(db_session, superuser, q=query)}
            assert m.id in found, f"case-insensitive search failed for {query!r}"

        # A non-matching query must not return it.
        miss = {item.id for item in mv.list_items(db_session, superuser, q="griffin")}
        assert m.id not in miss

    def test_list_items_excludes_external_sentinel(
        self, db_session: Session, superuser: User
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
            db_session.exec(
                select(Model).where(Model.hash == SENTINEL_MODEL_HASH)
            ).first()
            is not None
        )

    def test_list_items_includes_daily_workflow_print_outcomes(
        self, db_session: Session, superuser: User
    ) -> None:
        model = build_model(db_session, name="Outcome", slug="outcome", hash="o" * 64)
        artifact = build_file(
            db_session,
            model,
            path="outcome.gcode",
            filename="outcome.gcode",
            file_type=FileType.GCODE,
            size_bytes=10,
            sha256="f" * 64,
        )
        finished = datetime(2026, 2, 1, tzinfo=timezone.utc)
        build_print_job(
            db_session,
            artifact,
            remote_filename="outcome.gcode",
            state=PrintJobState.COMPLETED,
            actual_duration_s=120,
            cost=1.25,
            finished_at=finished,
        )
        build_print_job(
            db_session,
            artifact,
            remote_filename="outcome.gcode",
            state=PrintJobState.FAILED,
            actual_duration_s=60,
            cost=0.25,
            finished_at=finished,
        )

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

    def test_name_sorted_cursor_pages_cover_every_model_once(
        self, db_session: Session, superuser: User
    ) -> None:
        created: dict[int, str] = {}
        for index, name in enumerate(["Zulu", "alpha", "Echo", "bravo", "Delta"]):
            model = build_model(
                db_session, name=name, slug=f"cursor-{index}", hash=f"c{index:063d}"
            )
            created[model.id] = name

        ids, total = _cursor_page_ids(
            db_session, superuser, ModelSort.NAME_ASC, page_size=2
        )
        names = [created[model_id] for model_id in ids if model_id in created]

        assert names == ["alpha", "bravo", "Delta", "Echo", "Zulu"]
        assert len(ids) == len(set(ids))
        assert total == len(created)

    def test_cursor_pages_apply_every_metric_sort_globally(
        self, db_session: Session, superuser: User
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
            model = build_model(
                db_session,
                name=name,
                slug=f"metric-{name.lower()}",
                hash=f"d{index:063d}",
            )
            artifact = build_file(
                db_session,
                model,
                path=f"{name}.gcode",
                filename=f"{name}.gcode",
                file_type=FileType.GCODE,
                size_bytes=10,
                sha256=f"e{index:063d}",
            )
            db_session.add(
                Metadata(
                    file_id=artifact.id,
                    estimated_time_s=estimate,
                    filament_weight_g=filament,
                )
            )
            for state, duration, cost, finished_at in jobs:
                build_print_job(
                    db_session,
                    artifact,
                    state=state,
                    actual_duration_s=duration,
                    cost=cost,
                    finished_at=finished_at,
                )
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
        self, db_session: Session, superuser: User
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


class TestListTrashed:
    def test_trashed_pagination_visits_every_model_exactly_once(
        self, db_session: Session, superuser: User
    ) -> None:
        tied = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        created: set[int] = set()
        for i in range(20):
            m = build_model(
                db_session,
                name=f"Trashed {i:02d}",
                slug=f"trashed-{i:02d}",
                hash=f"{i + 1000:064d}",
                deleted_at=tied,
            )
            created.add(m.id)

        seen = _paginate_ids(
            lambda limit, offset: mv.list_trashed(
                db_session, superuser, limit=limit, offset=offset, retention_days=30
            ),
            page_size=7,
        )

        assert len(seen) == len(set(seen)), (
            "a trashed model was duplicated across pages"
        )
        assert created <= set(seen)
        assert sum(1 for i in seen if i in created) == 20
