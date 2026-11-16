"""Chosen Model covers add independent storage without rewriting automatic pointers."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestModelCoverMigration:
    def test_round_trip_preserves_existing_models(self, tmp_path: Path):
        database = tmp_path / "model-covers.sqlite"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
        command.upgrade(config, "b74196f14d23")
        engine = create_engine(f"sqlite:///{database}")
        try:
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "models",
                    id=7,
                    name="Existing Model",
                    slug="existing",
                    hash="a" * 64,
                    edit_version=1,
                )
            command.upgrade(config, "506719fa4a57")

            assert "model_covers" in inspect(engine).get_table_names()
            assert (
                inspect(engine).get_foreign_keys("model_covers")[0]["options"][
                    "ondelete"
                ]
                == "CASCADE"
            )
            with engine.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT name FROM models WHERE id=7")
                    ).scalar_one()
                    == "Existing Model"
                )
                assert (
                    connection.execute(
                        text("SELECT COUNT(*) FROM model_covers")
                    ).scalar_one()
                    == 0
                )
                seed_schema_row(
                    connection,
                    "model_covers",
                    model_id=7,
                    filename="chosen.webp",
                    size_bytes=128,
                    sha256="b" * 64,
                )

            command.downgrade(config, "b74196f14d23")

            assert "model_covers" not in inspect(engine).get_table_names()
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT name FROM models WHERE id=7")
                    ).scalar_one()
                    == "Existing Model"
                )
        finally:
            engine.dispose()
