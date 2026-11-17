"""Upgrades recognize sliced archives without replacing originals or recommendations."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

BEFORE = "3c9a19d6deb9"
SLICED = "8d0710caf97a"


class TestSlicedRevisionsMigration:
    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
    )
    def test_upgrades_existing_sliced_revisions(self, tmp_path, dialect):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        url = (
            f"sqlite:///{tmp_path / 'sliced.sqlite'}"
            if dialect == "sqlite"
            else normalize_database_url(fresh_postgres_database("sliced_upgrade"))
        )
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url)
        if dialect == "postgresql":
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, BEFORE)
        with engine.begin() as connection:
            for number in (1, 2):
                seed_schema_row(
                    connection,
                    "models",
                    id=number,
                    name=f"Model {number}",
                    slug=f"model-{number}",
                    hash=str(number) * 64,
                )
            for number, model, kind, filename, recommended in (
                (1, 1, "GCODE", "previous.gcode", True),
                (2, 1, "THREE_MF", "new.GCODE.3MF", False),
                (3, 2, "THREE_MF", "first.gcode.3mf", False),
                (4, 2, "THREE_MF", "second.gcode.3mf", False),
            ):
                seed_schema_row(
                    connection,
                    "files",
                    id=number,
                    model_id=model,
                    file_type=kind,
                    original_filename=filename,
                    sha256=str(number) * 64,
                    version=number,
                    path=f"original/{filename}",
                    is_recommended=recommended,
                )
        command.upgrade(config, SLICED)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, file_type, is_recommended, path FROM files ORDER BY id"
                )
            ).all()
            assert [row.file_type for row in rows] == ["GCODE"] * 4
            assert [row.id for row in rows if row.is_recommended] == [1, 4]
            assert rows[1].path == "original/new.GCODE.3MF"
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE files SET is_recommended = false WHERE id = 1")
            )
            connection.execute(
                text("UPDATE files SET is_recommended = true WHERE id = 2")
            )
        command.downgrade(config, BEFORE)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT file_type FROM files WHERE id = 2")
                ).scalar_one()
                == "THREE_MF"
            )
            assert (
                connection.execute(text("SELECT count(*) FROM files")).scalar_one() == 4
            )
            assert connection.execute(
                text("SELECT id FROM files WHERE is_recommended = true")
            ).scalars().all() == [1]
        command.upgrade(config, SLICED)
        engine.dispose()
