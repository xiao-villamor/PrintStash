"""Search edits gain their own version without losing settings or vault triggers."""

from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories import build_system_config

PREVIOUS = "09232f601ed9"
REVISION = "0a76fbca8438"


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", id="postgres", marks=pytest.mark.postgres),
    ]
)
def legacy_search_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    url = (
        fresh_postgres_database("search_edit_upgrade")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'search-edit.sqlite'}"
    )
    # Fresh schema then downgrade also handles the historical PostgreSQL FK cycle.
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        with Session(engine) as session:
            build_system_config(
                session,
                currency="EUR",
                ai_search_settings_json='{"enabled":true,"timezone":"Europe/Madrid"}',
            )
    finally:
        engine.dispose()
    command.downgrade(_alembic_config(url), PREVIOUS)
    return url


class TestSearchEditMigration:
    def test_upgrades_existing_search_settings(self, legacy_search_url: str) -> None:
        command.upgrade(_alembic_config(legacy_search_url), REVISION)
        engine = create_engine(normalize_database_url(legacy_search_url))
        try:
            with engine.begin() as connection:
                row = connection.execute(
                    text(
                        "SELECT ai_search_settings_json, currency, search_edit_version, vault_edit_version FROM system_config WHERE id=1"
                    )
                ).one()
                assert tuple(row) == (
                    '{"enabled":true,"timezone":"Europe/Madrid"}',
                    "EUR",
                    1,
                    1,
                )
                connection.execute(
                    text(
                        "UPDATE system_config SET ai_search_settings_json=:value WHERE id=1"
                    ),
                    {"value": '{"enabled":false}'},
                )
                assert connection.execute(
                    text(
                        "SELECT search_edit_version, vault_edit_version FROM system_config WHERE id=1"
                    )
                ).one() == (2, 1)
        finally:
            engine.dispose()

    def test_round_trip_preserves_the_independent_vault_trigger(
        self, legacy_search_url: str
    ) -> None:
        config = _alembic_config(legacy_search_url)
        command.upgrade(config, REVISION)
        command.downgrade(config, PREVIOUS)
        engine = create_engine(normalize_database_url(legacy_search_url))
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE system_config SET currency='GBP' WHERE id=1")
                )
                assert (
                    connection.execute(
                        text("SELECT vault_edit_version FROM system_config WHERE id=1")
                    ).scalar_one()
                    == 2
                )
            command.upgrade(config, REVISION)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE system_config SET ai_search_settings_json=:value WHERE id=1"
                    ),
                    {"value": '{"enabled":false}'},
                )
                assert connection.execute(
                    text(
                        "SELECT currency, vault_edit_version, search_edit_version FROM system_config WHERE id=1"
                    )
                ).one() == ("GBP", 2, 2)
        finally:
            engine.dispose()


class TestOfflineSearchEditMigration:
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
        assert "ADD COLUMN search_edit_version" in sql
        assert "CREATE TRIGGER" in sql
        assert "ps_search_settings_edit_v1" in sql
