"""Upgrades retain library/settings data while adding empty, opt-in inference configuration."""

from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestInferenceEndpointsMigration:
    def test_preserves_existing_settings_on_upgrade(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'inference.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "c3707090f459")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "system_config",
                    id=1,
                    similarity_settings_json='{"enabled":true}',
                )
                seed_schema_row(
                    connection, "models", id=12, name="Existing bracket", hash="a" * 64
                )

            command.upgrade(config, "head")

            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT similarity_settings_json, ai_search_settings_json FROM system_config"
                    )
                ).one() == ('{"enabled":true}', None)
                assert (
                    connection.execute(text("SELECT name FROM models")).scalar_one()
                    == "Existing bracket"
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM inference_endpoints")
                    ).scalar_one()
                    == 0
                )
        finally:
            engine.dispose()

    def test_downgrades_without_losing_existing_settings(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'inference.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "system_config",
                    id=1,
                    similarity_settings_json='{"enabled":true}',
                    ai_search_settings_json='{"enabled":false}',
                )

            command.downgrade(config, "c3707090f459")

            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT similarity_settings_json FROM system_config")
                    ).scalar_one()
                    == '{"enabled":true}'
                )
                assert (
                    "inference_endpoints" not in inspect(connection).get_table_names()
                )
        finally:
            engine.dispose()
