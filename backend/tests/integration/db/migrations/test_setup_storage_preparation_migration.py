"""Upgrades preserve configured installations without scheduling new preparation."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PREVIOUS = "046685afd7ea"
REVISION = "199d0382f2d1"


def _config(path: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


class TestPreparationMigration:
    def test_upgrade_preserves_the_installation_marker(self, tmp_path):
        path = tmp_path / "upgrade.sqlite"
        config = _config(path)
        command.upgrade(config, PREVIOUS)
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """INSERT INTO system_config (id, auto_mark_known_good, external_libraries_enabled, notifications_enabled, spoolman_enabled, spoolman_write_enabled, spoolman_write_force, created_at, updated_at, configured_at) VALUES (1, 1, 0, 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '2026-09-01 00:00:00')"""
                    )
                )
            command.upgrade(config, REVISION)
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT configured_at, setup_storage_pending FROM system_config WHERE id=1"
                    )
                ).one() == ("2026-09-01 00:00:00", 0)
        finally:
            engine.dispose()

    def test_downgrade_then_upgrade_restores_preparation_column(self, tmp_path):
        path = tmp_path / "roundtrip.sqlite"
        config = _config(path)
        command.upgrade(config, REVISION)
        command.downgrade(config, PREVIOUS)
        command.upgrade(config, REVISION)
        engine = create_engine(f"sqlite:///{path}")
        try:
            assert "setup_storage_pending" in {
                column["name"]
                for column in inspect(engine).get_columns("system_config")
            }
        finally:
            engine.dispose()
