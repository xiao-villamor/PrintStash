"""Multipart cover selection survives upgrades and follows Model deletion."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PARENT = "7f460c4cf1f3"
REVISION = "3beaa172254a"


def _config(database: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


class TestMultipartCoverMigration:
    def test_round_trip_preserves_existing_multipart_model(
        self, tmp_path: Path
    ) -> None:
        database = tmp_path / "multipart-cover-round-trip.sqlite"
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

        assert "cover_model_id" in {
            column["name"] for column in inspect(engine).get_columns("multipart_models")
        }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT name, cover_model_id FROM multipart_models WHERE id = 7")
            ).one() == ("Lamp", None)

        command.downgrade(config, PARENT)

        assert "cover_model_id" not in {
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

    def test_model_delete_clears_selected_cover(self, tmp_path: Path) -> None:
        database = tmp_path / "multipart-cover-delete.sqlite"
        config = _config(database)
        command.upgrade(config, REVISION)
        engine = create_engine(f"sqlite:///{database}")
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(
                text(
                    """
                    INSERT INTO models (
                        id, name, slug, hash, next_file_version, created_at, updated_at
                    ) VALUES (
                        11, 'Cover', 'cover', :hash, 1, '2026-09-02', '2026-09-02'
                    )
                    """
                ),
                {"hash": "a" * 64},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO multipart_models (
                        id, name, slug, cover_model_id, created_at, updated_at
                    ) VALUES (
                        7, 'Lamp', 'lamp', 11, '2026-09-02', '2026-09-02'
                    )
                    """
                )
            )
            connection.execute(text("DELETE FROM models WHERE id = 11"))

            assert (
                connection.execute(
                    text("SELECT cover_model_id FROM multipart_models WHERE id = 7")
                ).scalar_one()
                is None
            )
        engine.dispose()
