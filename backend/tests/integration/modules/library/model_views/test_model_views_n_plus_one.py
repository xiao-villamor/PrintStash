"""Listing a page of models must cost a fixed number of queries.

``effective_role`` was resolved per row, and each resolution ran two queries (the
collection, then the grants). A 50-model page therefore issued ~100 extra
queries, all returning the same handful of grants.
"""

from __future__ import annotations

from typing import Callable

import pytest
from sqlalchemy import event
from sqlmodel import Session

import app.modules.library.model_views.facets as models_facets
import app.modules.library.model_views.listing as models_listing
import app.modules.library.model_views.pagination as models_pagination
import app.modules.library.model_views.statistics as models_statistics
from app.db.models import CollectionRole
from app.modules.library import taxonomy
from app.schemas.models import ModelFilters, ModelSort
from tests.factories import (
    build_model,
    build_user,
    grant_collection_role,
)


def _seed_models(session: Session, count: int, collection_id: int | None) -> None:
    for i in range(count):
        build_model(
            session,
            name=f"Model {i}",
            slug=f"model-{i}",
            hash=f"{i:064d}",
            collection_id=collection_id,
        )
    session.commit()


def _count_queries(session: Session, fn: Callable[[], object]) -> int:
    statements: list[str] = []

    def _record(conn, cursor, statement, *args):  # noqa: ANN001
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    return len(statements)


class TestListItems:
    def test_batches_family_summaries(
        self, db_session, make_user, make_model, make_family, make_family_member
    ):
        user = make_user(superuser=True)
        [
            make_family_member(make_family(), make_model(), canonical=True)
            for _ in range(2)
        ]
        _ = user.is_superuser
        small = _count_queries(
            db_session, lambda: models_listing.list_items(db_session, user, limit=100)
        )
        [
            make_family_member(make_family(), make_model(), canonical=True)
            for _ in range(30)
        ]
        _ = user.is_superuser

        large = _count_queries(
            db_session, lambda: models_listing.list_items(db_session, user, limit=100)
        )

        assert large == small, f"Family projection grew from {small} to {large} queries"

    @pytest.mark.parametrize("superuser", [False, True])
    def test_list_query_count_is_independent_of_page_size(
        self, db_session: Session, superuser: bool
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Parts")
        assert collection is not None
        user = build_user(db_session, f"lister-{superuser}", superuser=superuser)
        if not superuser:
            grant_collection_role(db_session, user, collection, CollectionRole.EDIT)

        _seed_models(db_session, 2, collection.id)
        small = _count_queries(
            db_session, lambda: models_listing.list_items(db_session, user, limit=100)
        )

        _seed_models_more = 30
        for i in range(_seed_models_more):
            build_model(
                db_session,
                name=f"Extra {i}",
                slug=f"extra-{i}",
                hash=f"e{i:063d}",
                collection_id=collection.id,
            )
        db_session.commit()

        large = _count_queries(
            db_session, lambda: models_listing.list_items(db_session, user, limit=100)
        )

        assert large == small, (
            f"query count grew with page size ({small} -> {large}): a per-row lookup "
            "is running inside the listing loop"
        )

    def test_effective_role_is_still_correct_per_row(self, db_session: Session) -> None:
        """Batching must not flatten roles: each model reports its own inherited role."""
        parts = taxonomy.resolve_or_create_collection(db_session, "Parts")
        toys = taxonomy.resolve_or_create_collection(db_session, "Toys")
        assert parts is not None and toys is not None
        user = build_user(db_session, "mixed")
        grant_collection_role(db_session, user, parts, CollectionRole.ADMIN)
        grant_collection_role(db_session, user, toys, CollectionRole.VIEW)

        build_model(
            db_session, name="P", slug="p", hash="p" * 64, collection_id=parts.id
        )
        build_model(
            db_session, name="T", slug="t", hash="t" * 64, collection_id=toys.id
        )

        items = models_listing.list_items(db_session, user, limit=100)
        roles = {item.name: item.effective_role for item in items}

        assert roles["P"] == CollectionRole.ADMIN
        assert roles["T"] == CollectionRole.VIEW

    def test_cursor_total_is_counted_only_on_the_first_page(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "cursor-count", superuser=True)
        _seed_models(db_session, 4, collection_id=None)
        _ = user.is_superuser
        first_page = None

        def load_first_page():
            nonlocal first_page
            first_page = models_pagination.page_items(
                db_session,
                user,
                filters=ModelFilters(),
                sort=ModelSort.NAME_ASC,
                limit=2,
            )

        first_queries = _count_queries(db_session, load_first_page)
        assert first_page is not None and first_page.next_cursor is not None
        second_queries = _count_queries(
            db_session,
            lambda: models_pagination.page_items(
                db_session,
                user,
                filters=ModelFilters(),
                sort=ModelSort.NAME_ASC,
                cursor=first_page.next_cursor,
                limit=2,
            ),
        )

        assert second_queries == first_queries - 1


class TestFacets:
    def test_facets_are_consolidated_into_one_query(self, db_session: Session) -> None:
        user = build_user(db_session, "facet-query-count", superuser=True)
        _seed_models(db_session, 3, collection_id=None)
        _ = user.is_superuser  # refresh the expired fixture row outside the counter

        count = _count_queries(
            db_session,
            lambda: models_facets.facets(db_session, user, ModelFilters()),
        )

        assert count == 1


class TestVaultStats:
    def test_vault_stats_counts_are_consolidated_into_one_query(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = build_user(db_session, "stats-query-count", superuser=True)
        monkeypatch.setattr(
            models_statistics,
            "_cached_storage_usage",
            lambda session: {"backend": "local", "ok": True},
        )

        count = _count_queries(
            db_session, lambda: models_statistics.vault_stats(db_session, user)
        )

        assert count == 1
