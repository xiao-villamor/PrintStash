"""Automatic backup configuration upgrades existing installations safely."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

REVISION = "fd6007599d0b"
PREVIOUS = "6610b47f2f67"


def _config(db_path: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


class TestUpgrade:
    def test_preserves_existing_backup_destinations_selected(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "automatic-backup.sqlite"
        config = _config(db_path)
        command.upgrade(config, PREVIOUS)
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO storage_connections (
                            name, kind, purpose, config_json, secret_json, enabled,
                            created_at, updated_at
                        ) VALUES (
                            'Existing backup', 'S3', 'BACKUP', '{}', '{}', 1,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    )
                )

            command.upgrade(config, REVISION)

            with engine.connect() as connection:
                selected = connection.execute(
                    text(
                        "SELECT manual_backup_enabled, automatic_backup_enabled "
                        "FROM storage_connections WHERE name = 'Existing backup'"
                    )
                ).one()
            assert selected == (1, 1)
        finally:
            engine.dispose()

    def test_keeps_automatic_creation_disabled(self, tmp_path: Path) -> None:
        db_path = tmp_path / "automatic-backup-config.sqlite"
        config = _config(db_path)
        command.upgrade(config, PREVIOUS)
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO system_config (
                            id, auto_mark_known_good,
                            external_libraries_enabled, notifications_enabled,
                            spoolman_enabled, spoolman_write_enabled,
                            spoolman_write_force, created_at, updated_at
                        ) VALUES (
                            1, 1, 0, 0, 0, 0, 0,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    )
                )

            command.upgrade(config, REVISION)

            with engine.connect() as connection:
                schedule = connection.execute(
                    text(
                        "SELECT automatic_backups_enabled, automatic_backup_time_utc "
                        "FROM system_config WHERE id = 1"
                    )
                ).one()
            assert schedule == (0, "02:00")
        finally:
            engine.dispose()


class TestDowngrade:
    def test_removes_the_automatic_backup_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "automatic-backup-downgrade.sqlite"
        config = _config(db_path)
        command.upgrade(config, REVISION)
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            command.downgrade(config, PREVIOUS)

            system_columns = {
                column["name"]
                for column in inspect(engine).get_columns("system_config")
            }
            connection_columns = {
                column["name"]
                for column in inspect(engine).get_columns("storage_connections")
            }
            assert "automatic_backups_enabled" not in system_columns
            assert "manual_backup_enabled" not in connection_columns
        finally:
            engine.dispose()
