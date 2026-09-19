"""The complete AI Search migration set preserves populated installations."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, make_url, text

from alembic import command
from app.db.migrate import _alembic_config
from app.db.url import normalize_database_url
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    seed_schema_row,
)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def search_migration_database(request, tmp_path):
    root = None
    schema = "search_migration_" + uuid4().hex
    if request.param == "postgres":
        from tests.containers import postgres_url

        root_url = make_url(normalize_database_url(postgres_url()))
        root = create_engine(root_url)
        with root.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        url = root_url.update_query_dict({"options": f"-csearch_path={schema}"})
    else:
        url = make_url(f"sqlite:///{tmp_path / 'search.sqlite'}")
    engine = create_engine(url)
    config = _alembic_config(url.render_as_string(hide_password=False))
    try:
        if root is not None:
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, "0118bda3e719")
        yield engine, config
    finally:
        engine.dispose()
        if root is not None:
            with root.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            root.dispose()


class TestSearchSchemaMigration:
    def test_roundtrips_populated_library(self, search_migration_database):
        engine, config = search_migration_database
        expected = {
            "models": (11, "Bracket", "Human description"),
            "collections": (12, "Workshop"),
            "multipart_models": (13, "Assembly"),
            "documents": (14, "Instructions", "Human document body"),
            "system_config": (1, '{"enabled":true}'),
        }
        queries = {
            "models": "SELECT id, name, description FROM models",
            "collections": "SELECT id, name FROM collections",
            "multipart_models": "SELECT id, name FROM multipart_models",
            "documents": "SELECT id, name, body FROM documents",
            "system_config": "SELECT id, similarity_settings_json FROM system_config",
        }
        with engine.begin() as connection:
            seed_schema_row(connection, "collections", id=12, name="Workshop")
            seed_schema_row(
                connection,
                "models",
                id=11,
                name="Bracket",
                hash="a" * 64,
                description="Human description",
                collection_id=12,
            )
            seed_schema_row(connection, "multipart_models", id=13, name="Assembly")
            seed_schema_row(
                connection,
                "documents",
                id=14,
                name="Instructions",
                kind="MARKDOWN",
                body="Human document body",
            )
            seed_schema_row(
                connection,
                "system_config",
                id=1,
                similarity_settings_json='{"enabled":true}',
            )

        added = {
            "search_passages",
            "inference_endpoints",
            "subject_captions",
            "search_expansions",
            "user_search_preferences",
        }
        for direction, revision in [
            ("upgrade", "head"),
            ("downgrade", "0118bda3e719"),
            ("upgrade", "head"),
        ]:
            getattr(command, direction)(config, revision)
            with engine.connect() as connection:
                for table, query in queries.items():
                    assert (
                        tuple(connection.execute(text(query)).one()) == expected[table]
                    )
                tables = set(inspect(connection).get_table_names())
                if revision == "head":
                    assert added <= tables
                    assert (
                        connection.execute(
                            text("SELECT ai_search_settings_json FROM system_config")
                        ).scalar_one()
                        is None
                    )
                else:
                    assert added.isdisjoint(tables)
                if engine.dialect.name == "sqlite":
                    assert (
                        connection.execute(text("PRAGMA foreign_key_check")).all() == []
                    )
