"""The collection tree's subtree counts hold on PostgreSQL.

``collection_tree`` counts a page's subtrees with a substring comparison on
``path`` and string concatenation: SQL whose functions and operators differ
between SQLite and PostgreSQL. A prefix test that silently matched a sibling
(``parts-extra`` under ``parts``), or no descendant at all, would show every
badge wrong on one database and right on the other (#295).
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine

from app.db.migrate import run_migrations
from app.db.models import CollectionRole
from app.db.url import normalize_database_url
from app.modules.library import collection_tree
from tests.containers import postgres_url
from tests.factories import (
    build_collection,
    build_model,
    build_user,
    grant_collection_role,
)


@pytest.fixture
def pg_session() -> Iterator[Session]:
    """A session on a freshly migrated database of its own."""
    url = normalize_database_url(postgres_url())
    database = f"collection_tree_{uuid4().hex}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    isolated = (
        make_url(url).set(database=database).render_as_string(hide_password=False)
    )
    run_migrations(isolated)
    engine = create_engine(isolated)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
        admin.dispose()


class TestCollectionTreeOnPostgres:
    def test_counts_nested_search_matches(self, pg_session: Session) -> None:
        admin = build_user(pg_session, superuser=True)
        parts = build_collection(pg_session, "Parts")
        spare = build_collection(pg_session, "Spare Parts", parent=parts)
        build_model(pg_session, "Direct", collection=parts)
        build_model(pg_session, "Nested", collection=spare)

        page = collection_tree.search(
            pg_session,
            admin,
            query="parts",
            minimum=CollectionRole.VIEW,
            cursor=None,
            limit=10,
        )

        counts = {item.name: item.model_count for item in page.items}
        assert counts == {"Parts": 2, "Spare Parts": 1}

    def test_counts_every_model_in_a_subtree(self, pg_session: Session) -> None:
        admin = build_user(pg_session, superuser=True)
        parts = build_collection(pg_session, "Parts")
        brackets = build_collection(pg_session, "Brackets", parent=parts)
        build_model(pg_session, "Direct", collection=parts)
        build_model(pg_session, "Nested", collection=brackets)

        page = collection_tree.children(
            pg_session, admin, parent_id=None, cursor=None, limit=10
        )

        assert [item.model_count for item in page.items] == [2]

    def test_keeps_a_prefix_sibling_out_of_a_subtree(self, pg_session: Session) -> None:
        admin = build_user(pg_session, superuser=True)
        build_collection(pg_session, "Parts")
        extra = build_collection(pg_session, "Parts Extra")
        build_model(pg_session, "Elsewhere", collection=extra)

        page = collection_tree.children(
            pg_session, admin, parent_id=None, cursor=None, limit=10
        )

        counts = {item.name: item.model_count for item in page.items}
        assert counts == {"Parts": 0, "Parts Extra": 1}

    def test_labels_a_row_with_its_ancestors_names(self, pg_session: Session) -> None:
        admin = build_user(pg_session, superuser=True)
        parts = build_collection(pg_session, "Parts")
        build_collection(pg_session, "Wall Brackets", parent=parts)

        found = collection_tree.lookup(pg_session, admin, "parts/wall-brackets")

        assert found.collection.display_path == "Parts/Wall Brackets"

    def test_searches_names_at_the_minimum_role(self, pg_session: Session) -> None:
        readable = build_collection(pg_session, "Readable brackets")
        writable = build_collection(pg_session, "Writable brackets")
        user = build_user(pg_session)
        grant_collection_role(pg_session, user, readable, CollectionRole.VIEW)
        grant_collection_role(pg_session, user, writable, CollectionRole.EDIT)

        page = collection_tree.search(
            pg_session,
            user,
            query="BRACKETS",
            minimum=CollectionRole.EDIT,
            cursor=None,
            limit=10,
        )

        assert [item.name for item in page.items] == ["Writable brackets"]
