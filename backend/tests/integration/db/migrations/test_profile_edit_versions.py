"""Preset edit upgrades preserve existing rows on SQLite and PostgreSQL."""

from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database

PREVIOUS = "f92f1ea1dfe2"
REVISION = "09232f601ed9"


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", id="postgres", marks=pytest.mark.postgres),
    ]
)
def legacy_profile_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    url = (
        fresh_postgres_database("profile_edit_upgrade")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'profiles.sqlite'}"
    )
    if request.param == "postgresql":
        # The historical initial migration cannot create its mutually referencing
        # files/models tables on PostgreSQL. Start from the supported fresh path.
        run_migrations(url)
        command.downgrade(_alembic_config(url), PREVIOUS)
    else:
        command.upgrade(_alembic_config(url), PREVIOUS)
    engine = create_engine(normalize_database_url(url))
    try:
        with engine.begin() as connection:
            # Current factories target current columns; these are historical rows.
            connection.execute(
                text(
                    "INSERT INTO filament_profiles (id,name,material_type,cost_per_kg,notes,spoolman_filament_id,density_g_cm3,created_at,updated_at) VALUES (1,'Local PLA','PLA',25,'Original',NULL,NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),(2,'Synced PETG','PETG',30,'Synced',42,1.25,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO printer_profiles (id,name,printer_model,nozzle_diameter_mm,notes,created_at,updated_at) VALUES (1,'Printer preset','Voron',0.4,'Original',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()
    return url


class TestProfileEditMigration:
    def test_upgrade_preserves_existing_profile_rows(
        self, legacy_profile_url: str
    ) -> None:
        command.upgrade(_alembic_config(legacy_profile_url), REVISION)
        engine = create_engine(normalize_database_url(legacy_profile_url))
        try:
            with engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT name,cost_per_kg,spoolman_filament_id,density_g_cm3 FROM filament_profiles ORDER BY id"
                    )
                ).all() == [
                    ("Local PLA", 25, None, None),
                    ("Synced PETG", 30, 42, 1.25),
                ]
                assert connection.execute(
                    text(
                        "SELECT name,printer_model,nozzle_diameter_mm FROM printer_profiles"
                    )
                ).one() == ("Printer preset", "Voron", 0.4)
                for table in ("filament_profiles", "printer_profiles"):
                    assert (
                        connection.execute(
                            text(f"SELECT edit_version FROM {table} WHERE id=1")
                        ).scalar_one()
                        == 1
                    )
                    connection.execute(
                        text(f"UPDATE {table} SET notes='Changed' WHERE id=1")
                    )
                    assert (
                        connection.execute(
                            text(f"SELECT edit_version FROM {table} WHERE id=1")
                        ).scalar_one()
                        == 2
                    )
        finally:
            engine.dispose()

    def test_round_trip_preserves_profile_changes(
        self, legacy_profile_url: str
    ) -> None:
        config = _alembic_config(legacy_profile_url)
        command.upgrade(config, REVISION)
        engine = create_engine(normalize_database_url(legacy_profile_url))
        try:
            with engine.begin() as connection:
                for table in ("filament_profiles", "printer_profiles"):
                    connection.execute(
                        text(f"UPDATE {table} SET notes='Changed' WHERE id=1")
                    )
            command.downgrade(config, PREVIOUS)
            command.upgrade(config, REVISION)
            with engine.begin() as connection:
                for table in ("filament_profiles", "printer_profiles"):
                    assert (
                        connection.execute(
                            text(f"SELECT notes FROM {table} WHERE id=1")
                        ).scalar_one()
                        == "Changed"
                    )
                    connection.execute(
                        text(f"UPDATE {table} SET notes='Revised' WHERE id=1")
                    )
                    assert connection.execute(
                        text(f"SELECT notes,edit_version FROM {table} WHERE id=1")
                    ).one() == ("Revised", 2)
        finally:
            engine.dispose()


class TestOfflineProfileEditMigration:
    @pytest.mark.parametrize(
        "url", ["sqlite:///offline-review.sqlite", "postgresql+psycopg://unused/unused"]
    )
    def test_renders_profile_upgrade_without_a_connection(self, url: str) -> None:
        output = StringIO()
        config = _alembic_config(url)
        config.output_buffer = output
        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)
        sql = output.getvalue()
        assert "ADD COLUMN edit_version" in sql
        assert "ps_filament_profiles_edit_v1" in sql
        assert "ps_printer_profiles_edit_v1" in sql
        assert "edit_identity" in sql
