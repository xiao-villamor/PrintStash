"""Recursive collection reads hold on PostgreSQL.

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
from sqlmodel import Session, create_engine, select

from app.core.time import utcnow
from app.db.migrate import run_migrations
from app.db.models import Collection, CollectionRole
from app.db.url import normalize_database_url
from app.modules.library import collection_tree, library_search
from tests.containers import postgres_url
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    build_tag,
    build_user,
    grant_collection_role,
    tag_collection,
    tag_file,
    tag_model,
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


class TestEffectiveTagsOnPostgres:
    def test_counts_a_nested_tag_once(self, pg_session: Session) -> None:
        root = build_collection(pg_session, "Tagged root")
        child = build_collection(pg_session, "Tagged child", parent=root)
        leaf = build_collection(pg_session, "Tagged leaf", parent=child)
        tag = build_tag(pg_session, "Inherited")
        tag_collection(pg_session, root, tag)
        tag_collection(pg_session, child, tag)
        build_model(pg_session, "Nested", collection=leaf)

        counts = dict(
            pg_session.exec(library_search.accessible_tag_counts_stmt()).all()
        )

        assert counts[tag.id] == 1

    @pytest.mark.parametrize("association", ["model", "file", "collection"], ids=str)
    def test_reads_newly_linked_tags_after_an_earlier_read(
        self, pg_session: Session, association: str
    ) -> None:
        root = build_collection(pg_session, "Parent")
        child = build_collection(pg_session, "Child", parent=root)
        model = build_model(pg_session, "Part", collection=child)
        artifact = build_file(pg_session, model)
        tag = build_tag(pg_session, "New")
        statement = library_search.effective_tag_pairs()
        assert pg_session.exec(statement).all() == []

        link, subject = {
            "model": (tag_model, model),
            "file": (tag_file, artifact),
            "collection": (tag_collection, root),
        }[association]
        link(pg_session, subject, tag)

        assert pg_session.exec(statement).all() == [(tag.id, model.id)]

    def test_excludes_a_newly_trashed_tag_after_an_earlier_read(
        self, pg_session: Session
    ) -> None:
        model = build_model(pg_session, "Part")
        tag = build_tag(pg_session, "Live")
        tag_model(pg_session, model, tag)
        statement = library_search.effective_tag_pairs()
        assert pg_session.exec(statement).all() == [(tag.id, model.id)]

        tag.deleted_at = utcnow()
        pg_session.add(tag)
        pg_session.commit()

        assert pg_session.exec(statement).all() == []

    def test_includes_a_restored_tag_after_an_earlier_read(
        self, pg_session: Session
    ) -> None:
        model = build_model(pg_session, "Part")
        tag = build_tag(pg_session, "Restorable", deleted_at=utcnow())
        tag_model(pg_session, model, tag)
        statement = library_search.effective_tag_pairs()
        assert pg_session.exec(statement).all() == []

        tag.deleted_at = None
        pg_session.add(tag)
        pg_session.commit()

        assert pg_session.exec(statement).all() == [(tag.id, model.id)]

    def test_scopes_each_read_to_its_current_collection_selection(
        self, pg_session: Session
    ) -> None:
        left = build_collection(pg_session, "Left")
        right = build_collection(pg_session, "Right")
        left_model = build_model(pg_session, "Left part", collection=left)
        right_model = build_model(pg_session, "Right part", collection=right)
        tag = build_tag(pg_session, "Shared")
        tag_model(pg_session, left_model, tag)
        tag_model(pg_session, right_model, tag)
        expected = {(tag.id, left_model.id), (tag.id, right_model.id)}
        assert (
            set(pg_session.exec(library_search.effective_tag_pairs()).all()) == expected
        )

        left_scope = select(Collection.id).where(Collection.id == left.id)
        assert pg_session.exec(
            library_search.effective_tag_pairs(left_scope)
        ).all() == [(tag.id, left_model.id)]
        right_scope = select(Collection.id).where(Collection.id == right.id)
        assert pg_session.exec(
            library_search.effective_tag_pairs(right_scope)
        ).all() == [(tag.id, right_model.id)]

        assert (
            set(pg_session.exec(library_search.effective_tag_pairs()).all()) == expected
        )

    def test_reads_rolled_back_association_state(self, pg_session: Session) -> None:
        model = build_model(pg_session, "Part")
        tag = build_tag(pg_session, "Durable")
        tag_model(pg_session, model, tag)
        expected = [(tag.id, model.id)]
        statement = library_search.effective_tag_pairs()
        assert pg_session.exec(statement).all() == expected
        tag.deleted_at = utcnow()
        pg_session.add(tag)
        pg_session.flush()
        assert pg_session.exec(statement).all() == []

        pg_session.rollback()

        assert pg_session.exec(statement).all() == expected


class TestOutlinerOnPostgres:
    def test_pages_mixed_entries(self, pg_session: Session) -> None:
        from app.modules.library import outliner
        from app.schemas.outliner import OutlinerQuery
        from tests.factories import build_multipart_model

        admin = build_user(pg_session, superuser=True)
        folder = build_collection(pg_session, "Folder")
        model = build_model(pg_session, "Same", collection=folder)
        group = build_multipart_model(pg_session, "Same", collection=folder)
        first = outliner.entries(
            pg_session, admin, OutlinerQuery(collection_id=folder.id, limit=1)
        )
        second = outliner.entries(
            pg_session,
            admin,
            OutlinerQuery(collection_id=folder.id, limit=1, cursor=first.next_cursor),
        )
        assert [(row.kind.value, row.id) for row in first.items + second.items] == [
            ("model", model.id),
            ("multipart", group.id),
        ]
        assert second.next_cursor is None

    def test_counts_filtered_descendants(self, pg_session: Session) -> None:
        from app.modules.library import outliner
        from app.schemas.outliner import OutlinerQuery
        from tests.factories import tag_model

        admin = build_user(pg_session, superuser=True)
        parent = build_collection(pg_session, "Parent")
        child = build_collection(pg_session, "Child", parent=parent)
        tag = build_tag(pg_session, "chosen")
        tag_model(pg_session, build_model(pg_session, "Hit", collection=child), tag)
        build_collection(pg_session, "Empty")
        page = outliner.collections(pg_session, admin, OutlinerQuery(tag=[tag.slug]))
        assert [(row.id, row.subtree_entry_count) for row in page.items] == [
            (parent.id, 1)
        ]

    @pytest.mark.parametrize("needle", ["%", "Á"], ids=["percent", "accent"])
    def test_search_respects_collection_access(
        self, pg_session: Session, needle: str
    ) -> None:
        from app.modules.library import outliner
        from app.schemas.outliner import OutlinerQuery

        parent = build_collection(pg_session, "Private")
        child = build_collection(pg_session, "Granted", parent=parent)
        viewer = build_user(pg_session)
        grant_collection_role(pg_session, viewer, child, CollectionRole.VIEW)
        visible = build_model(pg_session, f"{needle} visible", collection=child)
        build_model(pg_session, f"{needle} private", collection=parent)
        page = outliner.search(pg_session, viewer, OutlinerQuery(q=needle))
        assert [(row.id, row.collection_label) for row in page.items] == [
            (visible.id, "Granted")
        ]

    def test_preserves_versioned_mixed_search(self, pg_session: Session) -> None:
        from app.modules.library import outliner
        from app.schemas.outliner import OutlinerQuery
        from tests.factories import build_multipart_model

        admin = build_user(pg_session, superuser=True)
        folder = build_collection(pg_session, "Versioned folder")
        model = build_model(
            pg_session, "Versioned model", collection=folder, edit_version=7
        )
        multipart = build_multipart_model(
            pg_session, "Versioned set", collection=folder, edit_version=9
        )
        page = outliner.search(pg_session, admin, OutlinerQuery(q="Versioned"))
        rows = {row.kind.value: row.model_dump() for row in page.items}
        assert rows["model"]["id"] == model.id
        assert rows["model"]["edit_version"] == 7
        assert rows["multipart"]["id"] == multipart.id
        assert rows["multipart"]["edit_version"] == 9
        assert rows["collection"]["id"] == folder.id
        assert "edit_version" not in rows["collection"]
