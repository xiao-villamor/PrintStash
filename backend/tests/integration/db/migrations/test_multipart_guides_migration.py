"""Multipart guides add a nullable, non-owning Document relationship."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from alembic import command
from app.db.migrate import run_migrations
from app.db.url import normalize_database_url
from tests.containers import postgres_url
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PARENT = "30c1c4a321a5"
REVISION = "7f460c4cf1f3"


def _config(database: Path | str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    url = database if isinstance(database, str) else f"sqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url)
    return config


def _reset_postgres(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")


@pytest.fixture(scope="module")
def postgres_migration_engine() -> Iterator[Engine]:
    url = postgres_url()
    engine = create_engine(normalize_database_url(url), pool_pre_ping=True)
    _reset_postgres(engine)
    config = _config(
        engine.url.render_as_string(hide_password=False).replace("%", "%%")
    )
    with engine.begin() as connection:
        create_released_v0121_postgres_schema(connection)
    command.stamp(config, RELEASED_V0121_REVISION)
    command.upgrade(config, PARENT)
    try:
        yield engine
    finally:
        _reset_postgres(engine)
        run_migrations(url)
        engine.dispose()


class TestMultipartGuidesMigration:
    def test_upgrade_preserves_a_document_before_linking_it(
        self, tmp_path: Path
    ) -> None:
        database = tmp_path / "multipart-guides.sqlite"
        config = _config(database)
        command.upgrade(config, PARENT)
        engine = create_engine(f"sqlite:///{database}")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO multipart_models (
                        id, name, slug, created_at, updated_at
                    ) VALUES (
                        7, 'Lamp', 'lamp', '2026-09-02', '2026-09-02'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO documents (
                        id, name, kind, body, created_at, updated_at
                    ) VALUES (
                        9, 'Assembly', 'MARKDOWN', '# Guide',
                        '2026-09-02', '2026-09-02'
                    )
                    """
                )
            )

        command.upgrade(config, REVISION)
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            assert "multipart_model_id" in {
                column["name"]
                for column in inspect(connection).get_columns("documents")
            }
            connection.execute(
                text("UPDATE documents SET multipart_model_id = 7 WHERE id = 9")
            )
            connection.execute(text("DELETE FROM multipart_models WHERE id = 7"))
            assert (
                connection.execute(
                    text("SELECT multipart_model_id FROM documents WHERE id = 9")
                ).scalar_one()
                is None
            )
        engine.dispose()

    def test_postgres_sql_contains_the_nullable_foreign_key(self) -> None:
        config = _config("postgresql+psycopg://user:pass@localhost/printstash")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            command.upgrade(config, f"{PARENT}:{REVISION}", sql=True)

        rendered = output.getvalue()
        assert "multipart_model_id" in rendered
        assert "ON DELETE SET NULL" in rendered

    def test_postgres_group_delete_detaches_its_guide(
        self, postgres_migration_engine: Engine
    ) -> None:
        engine = postgres_migration_engine
        config = _config(
            engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, REVISION)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO multipart_models (id, name, slug, created_at, updated_at)
                    VALUES (7, 'Lamp', 'lamp', NOW(), NOW())
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO documents (
                        id, name, kind, body, multipart_model_id, created_at, updated_at
                    ) VALUES (9, 'Assembly', 'MARKDOWN', '# Guide', 7, NOW(), NOW())
                    """
                )
            )
            connection.execute(text("DELETE FROM multipart_models WHERE id = 7"))
            assert (
                connection.execute(
                    text("SELECT multipart_model_id FROM documents WHERE id = 9")
                ).scalar_one()
                is None
            )
