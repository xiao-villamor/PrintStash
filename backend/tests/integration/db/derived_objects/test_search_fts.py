"""Alembic ignores registered FTS shadows while still exposing unrelated drift."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlmodel import SQLModel

from app.db.derived_objects import managed_names
from app.db.projections import bind_content_projection, content_changed
from app.modules.search.lexical_index import rebuild_partition
from app.modules.search.projection import LibraryProjection


class TestSearchDerivedObjects:
    def test_excludes_only_registered_fts_objects_from_autogenerate(
        self, db_session, make_model
    ):
        model = make_model()
        previous = bind_content_projection(LibraryProjection())
        try:
            content_changed(db_session, "model", [model.id])
            rebuild_partition(db_session)
            db_session.commit()
        finally:
            bind_content_projection(previous)
        connection = db_session.connection()
        names = managed_names(connection)
        assert names == {
            "search_passages_fts",
            "search_passages_fts_config",
            "search_passages_fts_docsize",
            "search_passages_fts_idx",
            "search_passages_fts_data",
        }
        context = MigrationContext.configure(
            connection,
            opts={
                "include_object": lambda obj, name, kind, reflected, compare: (
                    not (kind == "table" and name in names)
                )
            },
        )

        assert compare_metadata(context, SQLModel.metadata) == []

    def test_detects_unmanaged_schema_drift(self, db_session):
        db_session.execute(text("CREATE TABLE unrelated_data (id INTEGER PRIMARY KEY)"))
        connection = db_session.connection()
        names = managed_names(connection)
        context = MigrationContext.configure(
            connection,
            opts={
                "include_object": lambda obj, name, kind, reflected, compare: (
                    not (kind == "table" and name in names)
                )
            },
        )
        try:
            changes = compare_metadata(context, SQLModel.metadata)
            assert any(
                change[0] == "remove_table" and change[1].name == "unrelated_data"
                for change in changes
            )
        finally:
            db_session.execute(text("DROP TABLE unrelated_data"))
            db_session.commit()
