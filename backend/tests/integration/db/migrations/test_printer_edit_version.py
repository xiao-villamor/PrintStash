"""Upgrade existing settings to the printer-edit version on both supported databases."""

from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database

PREVIOUS = "ae086ab363c8"
REVISION = "f92f1ea1dfe2"


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", id="postgres", marks=pytest.mark.postgres),
    ]
)
def legacy_printer_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    url = (
        fresh_postgres_database("printer_edit_upgrade")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'printer-edit.sqlite'}"
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
            # A current-model factory would insert edit_version into this
            # historical schema. Name the required historical columns instead.
            connection.execute(
                text("""
                INSERT INTO printers (
                    id, name, moonraker_url, provider, status, notes, created_at, updated_at
                ) VALUES (
                    1, 'Historical printer', 'http://printer.test', 'MOONRAKER', 'UNKNOWN',
                    'Original notes', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """)
            )
    finally:
        engine.dispose()
    return url


class TestPrinterEditMigration:
    def test_upgrade_preserves_existing_printer_settings(
        self, legacy_printer_url: str
    ) -> None:
        command.upgrade(_alembic_config(legacy_printer_url), REVISION)
        engine = create_engine(normalize_database_url(legacy_printer_url))
        try:
            with engine.begin() as connection:
                row = connection.execute(
                    text(
                        "SELECT name, moonraker_url, notes, edit_version "
                        "FROM printers WHERE id=1"
                    )
                ).one()
                assert tuple(row) == (
                    "Historical printer",
                    "http://printer.test",
                    "Original notes",
                    1,
                )
                connection.execute(
                    text("UPDATE printers SET notes='Changed' WHERE id=1")
                )
                assert (
                    connection.execute(
                        text("SELECT edit_version FROM printers WHERE id=1")
                    ).scalar_one()
                    == 2
                )
        finally:
            engine.dispose()

    def test_round_trip_preserves_existing_settings(
        self, legacy_printer_url: str
    ) -> None:
        config = _alembic_config(legacy_printer_url)
        command.upgrade(config, REVISION)
        engine = create_engine(normalize_database_url(legacy_printer_url))
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE printers SET notes='Changed' WHERE id=1")
                )
            command.downgrade(config, PREVIOUS)
            with engine.begin() as connection:
                revision = connection.execute(
                    text("SELECT revision FROM library_revision WHERE id=1")
                ).scalar_one()
                connection.execute(
                    text("UPDATE printers SET last_error='Offline' WHERE id=1")
                )
                assert (
                    connection.execute(
                        text("SELECT revision FROM library_revision WHERE id=1")
                    ).scalar_one()
                    > revision
                )
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT name, moonraker_url, notes FROM printers WHERE id=1")
                ).one() == ("Historical printer", "http://printer.test", "Changed")
            command.upgrade(config, REVISION)
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE printers SET notes='Revised' WHERE id=1")
                )
                assert connection.execute(
                    text("SELECT notes, edit_version FROM printers WHERE id=1")
                ).one() == ("Revised", 2)
        finally:
            engine.dispose()


class TestOfflinePrinterEditMigration:
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
        assert "ADD COLUMN edit_version" in sql
        assert "CREATE TRIGGER" in sql
        assert "ps_printer_edit_v1" in sql
