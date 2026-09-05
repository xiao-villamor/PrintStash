"""Existing multipart requirements acquire quantity one during an additive upgrade."""

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestMultipartBuildsMigration:
    def test_existing_parts_receive_quantity_one(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'old-composition.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "046685afd7ea")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO multipart_models (id, name, slug, created_at, updated_at) VALUES (1, 'Existing chair', 'existing-chair', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO multipart_parts (id, multipart_model_id, name, name_key, sort_order, created_at, updated_at) VALUES (1, 1, 'Leg', 'leg', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT name, quantity FROM multipart_parts WHERE id=1")
                ).one() == ("Leg", 1)
            assert {
                "multipart_builds",
                "multipart_build_parts",
                "multipart_build_attempts",
                "multipart_build_confirmations",
            }.issubset(inspect(engine).get_table_names())
        finally:
            engine.dispose()
