"""Uploaded Multipart Model covers are additive and downgrade safely."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PARENT = "4608a3ae9e23"
REVISION = "6610b47f2f67"


def _config(database: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


class TestMultipartUploadedCoverMigration:
    def test_round_trip_preserves_existing_multipart_metadata(
        self, tmp_path: Path
    ) -> None:
        database = tmp_path / "multipart-uploaded-cover.sqlite"
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

        assert {
            "cover_filename",
            "cover_content_type",
            "cover_size_bytes",
        } <= {
            column["name"] for column in inspect(engine).get_columns("multipart_models")
        }
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT name, cover_filename, cover_content_type, cover_size_bytes
                    FROM multipart_models WHERE id = 7
                    """
                )
            ).one() == ("Lamp", None, None, None)

        command.downgrade(config, PARENT)

        assert {
            "cover_filename",
            "cover_content_type",
            "cover_size_bytes",
        }.isdisjoint(
            column["name"] for column in inspect(engine).get_columns("multipart_models")
        )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM multipart_models WHERE id = 7")
                ).scalar_one()
                == "Lamp"
            )
        engine.dispose()
