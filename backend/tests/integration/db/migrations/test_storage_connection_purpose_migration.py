"""Remote connection purposes preserve existing library profiles on upgrade."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

REVISION = "4608a3ae9e23"
PREVIOUS = "23bdc08ade8b"


def _config(db_path: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def test_existing_profiles_become_library_connections_and_downgrade_cleanly(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "storage-connection-purpose.sqlite"
    config = _config(db_path)
    command.upgrade(config, PREVIOUS)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO storage_connections (
                        name, kind, config_json, secret_json, enabled,
                        created_at, updated_at
                    ) VALUES (
                        'Existing library', 'S3', '{}', '{}', 1,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        command.upgrade(config, REVISION)

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT purpose FROM storage_connections "
                        "WHERE name = 'Existing library'"
                    )
                ).scalar_one()
                == "LIBRARY"
            )
        assert "ix_storage_connections_purpose" in {
            row["name"] for row in inspect(engine).get_indexes("storage_connections")
        }

        command.downgrade(config, PREVIOUS)

        assert "purpose" not in {
            column["name"]
            for column in inspect(engine).get_columns("storage_connections")
        }
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT kind FROM storage_connections "
                        "WHERE name = 'Existing library'"
                    )
                ).scalar_one()
                == "S3"
            )
    finally:
        engine.dispose()
