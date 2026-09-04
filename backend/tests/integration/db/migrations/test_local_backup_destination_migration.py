"""Local backup destination choices preserve upgraded installations' behavior.

The new fields default to enabled so upgrading never silently stops the local
copy operators relied on before remote-only publication became configurable.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

REVISION = "e916c791628c"
PREVIOUS = "fd6007599d0b"


def _config(db_path: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


class TestUpgrade:
    def test_preserves_local_publication_for_existing_installations(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "local-backup-destinations.sqlite"
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
                            spoolman_write_force, automatic_backups_enabled,
                            automatic_backup_time_utc, created_at, updated_at
                        ) VALUES (
                            1, 1, 0, 0, 0, 0, 0, 0, '02:00',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    )
                )

            command.upgrade(config, REVISION)

            with engine.connect() as connection:
                selections = connection.execute(
                    text(
                        "SELECT manual_local_backup_enabled, "
                        "automatic_local_backup_enabled "
                        "FROM system_config WHERE id = 1"
                    )
                ).one()
            assert selections == (1, 1)
        finally:
            engine.dispose()


class TestDowngrade:
    def test_removes_local_destination_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "local-backup-destinations-downgrade.sqlite"
        config = _config(db_path)
        command.upgrade(config, REVISION)
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            command.downgrade(config, PREVIOUS)

            columns = {
                column["name"]
                for column in inspect(engine).get_columns("system_config")
            }
            assert "manual_local_backup_enabled" not in columns
            assert "automatic_local_backup_enabled" not in columns
        finally:
            engine.dispose()
