"""Multipart tags add a reversible association without rewriting either entity."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PARENT = "3beaa172254a"
REVISION = "270a39e42dea"


def _config(database: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


class TestMultipartTagsMigration:
    def test_round_trip_preserves_existing_entities(self, tmp_path: Path) -> None:
        database = tmp_path / "multipart-tags-round-trip.sqlite"
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
                    INSERT INTO tags (id, name, slug, created_at)
                    VALUES (11, 'Lighting', 'lighting', '2026-09-02')
                    """
                )
            )

        command.upgrade(config, REVISION)

        assert "multipart_model_tags" in inspect(engine).get_table_names()
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO multipart_model_tags (multipart_model_id, tag_id)
                    VALUES (7, 11)
                    """
                )
            )
            assert connection.execute(
                text("SELECT multipart_model_id, tag_id FROM multipart_model_tags")
            ).one() == (7, 11)

        command.downgrade(config, PARENT)

        assert "multipart_model_tags" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM multipart_models WHERE id = 7")
                ).scalar_one()
                == "Lamp"
            )
            assert (
                connection.execute(
                    text("SELECT name FROM tags WHERE id = 11")
                ).scalar_one()
                == "Lighting"
            )
        engine.dispose()
