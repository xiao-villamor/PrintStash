"""Upgrade existing settings to the Spoolman edit version on both supported databases."""

from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database

PREVIOUS = "5291a5f0231c"
REVISION = "bcb8ec58819b"


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", id="postgres", marks=pytest.mark.postgres),
    ]
)
def legacy_config_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    url = (
        fresh_postgres_database("spoolman_edit_upgrade")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'spoolman-edit.sqlite'}"
    )
    if request.param == "postgresql":
        # The historical baseline creates the files/models FK cycle in an
        # order PostgreSQL rejects. Use the supported fresh-install path, then
        # restore the exact previous revision before exercising this upgrade.
        run_migrations(url)
        command.downgrade(_alembic_config(url), PREVIOUS)
    else:
        command.upgrade(_alembic_config(url), PREVIOUS)
    engine = create_engine(normalize_database_url(url))
    try:
        with engine.begin() as connection:
            # A current-model factory would insert spoolman_edit_version into this
            # historical schema. Name the required historical columns instead.
            connection.execute(
                text("""
                INSERT INTO system_config (
                    id, currency, oidc_client_id, backup_retention_days,
                    auto_mark_known_good, external_libraries_enabled,
                    notifications_enabled, spoolman_enabled,
                    spoolman_write_enabled, spoolman_write_force, created_at, updated_at
                ) VALUES (
                    1, 'EUR', 'historical-client', 17,
                    true, false, false, false, false, false,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """)
            )
    finally:
        engine.dispose()
    return url


class TestSpoolmanEditMigration:
    def test_upgrade_preserves_existing_configuration(
        self, legacy_config_url: str
    ) -> None:
        command.upgrade(_alembic_config(legacy_config_url), REVISION)
        engine = create_engine(normalize_database_url(legacy_config_url))
        try:
            with engine.begin() as connection:
                row = connection.execute(
                    text(
                        "SELECT currency, oidc_client_id, backup_retention_days, spoolman_edit_version "
                        "FROM system_config WHERE id=1"
                    )
                ).one()
                assert tuple(row) == ("EUR", "historical-client", 17, 1)
                connection.execute(
                    text(
                        "UPDATE system_config SET spoolman_base_url='GBP', currency='GBP' WHERE id=1"
                    )
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT spoolman_edit_version FROM system_config WHERE id=1"
                        )
                    ).scalar_one()
                    == 2
                )
        finally:
            engine.dispose()

    def test_round_trip_preserves_existing_settings(
        self, legacy_config_url: str
    ) -> None:
        config = _alembic_config(legacy_config_url)
        command.upgrade(config, REVISION)
        engine = create_engine(normalize_database_url(legacy_config_url))
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE system_config SET spoolman_base_url='GBP', currency='GBP' WHERE id=1"
                    )
                )
            command.downgrade(config, PREVIOUS)
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT currency, oidc_client_id, backup_retention_days FROM system_config WHERE id=1"
                    )
                ).one() == ("GBP", "historical-client", 17)
            command.upgrade(config, REVISION)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE system_config SET spoolman_base_url='USD', currency='USD' WHERE id=1"
                    )
                )
                assert connection.execute(
                    text(
                        "SELECT currency, spoolman_edit_version FROM system_config WHERE id=1"
                    )
                ).one() == ("USD", 2)
        finally:
            engine.dispose()


class TestOfflineSpoolmanEditMigration:
    @pytest.mark.parametrize(
        "url",
        [
            pytest.param("sqlite:///offline-review.sqlite", id="sqlite"),
            pytest.param("postgresql+psycopg://unused/unused", id="postgres"),
        ],
    )
    def test_renders_the_upgrade_without_a_database_connection(self, url: str) -> None:
        output = StringIO()
        config = _alembic_config(url)
        config.output_buffer = output
        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)
        sql = output.getvalue()
        assert "ADD COLUMN spoolman_edit_version" in sql
        assert "CREATE TRIGGER" in sql
        assert "ps_spoolman_edit_v1" in sql
