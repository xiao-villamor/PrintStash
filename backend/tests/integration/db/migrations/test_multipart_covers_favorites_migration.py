"""Multipart covers and favorites are additive to an existing set library."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PARENT = "270a39e42dea"
REVISION = "23bdc08ade8b"


def _config(database: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


class TestMultipartCoversFavoritesMigration:
    def test_upgrade_adds_external_cover_column(self, tmp_path: Path) -> None:
        database = tmp_path / "multipart-external-cover.sqlite"
        config = _config(database)
        command.upgrade(config, PARENT)

        command.upgrade(config, REVISION)

        engine = create_engine(f"sqlite:///{database}")
        try:
            assert "cover_image_url" in {
                column["name"]
                for column in inspect(engine).get_columns("multipart_models")
            }
        finally:
            engine.dispose()

    def test_upgrade_adds_favorite_table(self, tmp_path: Path) -> None:
        database = tmp_path / "multipart-favorites.sqlite"
        config = _config(database)
        command.upgrade(config, PARENT)

        command.upgrade(config, REVISION)

        engine = create_engine(f"sqlite:///{database}")
        try:
            assert "multipart_model_stars" in inspect(engine).get_table_names()
            assert {"user_id", "multipart_model_id"} <= {
                column["name"]
                for column in inspect(engine).get_columns("multipart_model_stars")
            }
        finally:
            engine.dispose()

    def test_downgrade_preserves_existing_set(self, tmp_path: Path) -> None:
        database = tmp_path / "multipart-cover-favorite-downgrade.sqlite"
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
        command.upgrade(config, REVISION)

        command.downgrade(config, PARENT)

        assert "multipart_model_stars" not in inspect(engine).get_table_names()
        assert "cover_image_url" not in {
            column["name"] for column in inspect(engine).get_columns("multipart_models")
        }
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM multipart_models WHERE id = 7")
                ).scalar_one()
                == "Lamp"
            )
        engine.dispose()
