"""Independent similarity installs on existing libraries without Family tables."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from app.db.migrate import _alembic_config
from app.db.url import normalize_database_url
from tests.factories.migration_rows import seed_schema_row

PARENT = "18c4551a00dc"
REVISION = "f3736257acd2"
TABLES = {
    "geometry_fingerprints",
    "similarity_runs",
    "similarity_candidates",
    "similarity_candidate_observations",
    "similarity_review_decisions",
    "embedding_spaces",
    "index_generations",
    "passage_vectors",
}


def exercise_roundtrip(url: str, *, released_postgres: bool = False) -> None:
    config = _alembic_config(url)
    engine = create_engine(url)
    try:
        if released_postgres:
            from tests.factories.migration_rows import (
                RELEASED_V0121_REVISION,
                create_released_v0121_postgres_schema,
            )

            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, PARENT)
        with engine.begin() as connection:
            seed_schema_row(
                connection, "models", id=104, name="Preserved design", hash="a" * 64
            )
            seed_schema_row(
                connection,
                "files",
                id=201,
                model_id=104,
                file_type="STL",
                sha256="b" * 64,
                version=1,
            )
            seed_schema_row(
                connection,
                "files",
                id=202,
                model_id=104,
                file_type="GCODE",
                sha256="c" * 64,
                version=2,
                is_recommended=True,
            )
            seed_schema_row(connection, "system_config", id=1, configured_at=None)
        for target in (REVISION, PARENT, REVISION):
            if target == PARENT:
                command.downgrade(config, target)
            else:
                command.upgrade(config, target)
            with engine.connect() as connection:
                inspector = inspect(connection)
                tables = set(inspector.get_table_names())
                assert (
                    TABLES <= tables
                    if target == REVISION
                    else TABLES.isdisjoint(tables)
                )
                assert "model_families" not in tables
                assert connection.execute(
                    text("SELECT name, hash FROM models WHERE id=104")
                ).one() == ("Preserved design", "a" * 64)
                assert connection.execute(
                    text(
                        "SELECT version, sha256 FROM files WHERE model_id=104 ORDER BY version"
                    )
                ).all() == [(1, "b" * 64), (2, "c" * 64)]
                assert bool(
                    connection.execute(
                        text("SELECT is_recommended FROM files WHERE id=202")
                    ).scalar_one()
                )
                if target == REVISION:
                    assert (
                        connection.execute(
                            text(
                                "SELECT similarity_settings_json FROM system_config WHERE id=1"
                            )
                        ).scalar_one()
                        is None
                    )
                    for table in TABLES:
                        assert all(
                            "family" not in key["referred_table"]
                            for key in inspector.get_foreign_keys(table)
                        )
                    assert {
                        tuple(index["column_names"])
                        for index in inspector.get_indexes("geometry_fingerprints")
                    } >= {
                        (f"{group}_hash_{index}",)
                        for group in ("physical", "normalized")
                        for index in range(4)
                    }
    finally:
        engine.dispose()


class TestSimilarityMigration:
    def test_preserves_existing_sqlite_library(self, tmp_path):
        exercise_roundtrip(f"sqlite:///{tmp_path / 'similarity.sqlite'}")

    @pytest.mark.postgres
    def test_preserves_existing_postgres_library(self):
        from tests.containers import postgres_url

        root_url = normalize_database_url(postgres_url())
        database = f"similarity_upgrade_{uuid4().hex}"
        admin = create_engine(root_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
        isolated_url = (
            make_url(root_url)
            .set(database=database)
            .render_as_string(hide_password=False)
        )
        try:
            exercise_roundtrip(isolated_url, released_postgres=True)
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
            admin.dispose()
