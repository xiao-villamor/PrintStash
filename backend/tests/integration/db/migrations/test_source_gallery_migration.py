"""Existing source covers retain their identity when galleries are introduced."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


def test_upgrades_existing_source_cover(tmp_path):
    database = tmp_path / "gallery.sqlite"
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "ba28991f0dd2")
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=7,
                name="Existing",
                slug="existing",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "model_provenance_sources",
                id=8,
                model_id=7,
                provider="thingiverse",
                canonical_url="https://www.thingiverse.com/thing:1",
                identity_key="b" * 64,
            )
            seed_schema_row(
                connection,
                "model_source_covers",
                id=9,
                provenance_source_id=8,
                storage_key="covers/original.webp",
                content_type="image/webp",
                size_bytes=20,
            )
        command.upgrade(config, "14982d207a3e")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT id, position, storage_key FROM model_source_covers")
            ).one() == (9, 0, "covers/original.webp")
        command.downgrade(config, "ba28991f0dd2")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT id, storage_key FROM model_source_covers")
            ).one() == (9, "covers/original.webp")
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_postgres_gallery_upgrade():
    from sqlalchemy.exc import IntegrityError

    from app.db.migrate import run_migrations
    from app.db.url import normalize_database_url
    from tests.containers import postgres_url
    from tests.factories.migration_rows import (
        RELEASED_V0121_REVISION,
        create_released_v0121_postgres_schema,
    )

    url = postgres_url()
    engine = create_engine(normalize_database_url(url), pool_pre_ping=True)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
            create_released_v0121_postgres_schema(connection)
        command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, "ba28991f0dd2")
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=7,
                name="Existing",
                slug="existing",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "model_provenance_sources",
                id=8,
                model_id=7,
                provider="thingiverse",
                canonical_url="https://www.thingiverse.com/thing:1",
                identity_key="b" * 64,
            )
            seed_schema_row(
                connection,
                "model_source_covers",
                id=9,
                provenance_source_id=8,
                storage_key="covers/original.webp",
                content_type="image/webp",
                size_bytes=20,
            )
        command.upgrade(config, "14982d207a3e")
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT position, storage_key FROM model_source_covers WHERE id=9")
            ).one() == (0, "covers/original.webp")
            seed_schema_row(
                connection,
                "model_source_covers",
                id=10,
                provenance_source_id=8,
                position=1,
                storage_key="covers/second.webp",
                content_type="image/webp",
                size_bytes=20,
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "model_source_covers",
                    id=11,
                    provenance_source_id=8,
                    position=1,
                    storage_key="covers/third.webp",
                    content_type="image/webp",
                    size_bytes=20,
                )
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM model_source_covers WHERE id=10"))
        command.downgrade(config, "ba28991f0dd2")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT storage_key FROM model_source_covers WHERE id=9")
                ).scalar_one()
                == "covers/original.webp"
            )
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        run_migrations(url)
        engine.dispose()
