"""Adding printer cost links preserves existing print history on both databases."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import seed_schema_row

_PREVIOUS = "00bd49fab9de"
_COSTS = "caddac5a1ee4"


class TestConnectedPrinterCostsMigration:
    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
    )
    def test_retains_existing_prints_on_upgrade(
        self, tmp_path: Path, dialect: str
    ) -> None:
        url = (
            f"sqlite:///{tmp_path / 'printer-costs.sqlite'}"
            if dialect == "sqlite"
            else fresh_postgres_database("printer_costs")
        )
        config = _alembic_config(url)
        if dialect == "sqlite":
            command.upgrade(config, _PREVIOUS)
        else:
            run_migrations(url)
            command.downgrade(config, _PREVIOUS)
        engine = create_engine(normalize_database_url(url))
        try:
            with engine.begin() as conn:
                seed_schema_row(
                    conn, "models", id=1, name="Clip", slug="clip", hash="a" * 64
                )
                seed_schema_row(
                    conn,
                    "files",
                    id=1,
                    model_id=1,
                    path="clip.gcode",
                    file_type="GCODE",
                    original_filename="clip.gcode",
                    sha256="a" * 64,
                    version=1,
                )
                seed_schema_row(
                    conn,
                    "printers",
                    id=1,
                    name="Workshop",
                    provider="MOONRAKER",
                    status="READY",
                )
                seed_schema_row(
                    conn,
                    "print_jobs",
                    id=1,
                    printer_id=1,
                    file_id=1,
                    model_id=1,
                    remote_filename="clip.gcode",
                    state="COMPLETED",
                    cost=1.25,
                )
            command.upgrade(config, _COSTS)
            with engine.connect() as conn:
                assert conn.execute(
                    text("SELECT name, cost_profile_id FROM printers")
                ).one() == ("Workshop", None)
                assert conn.execute(
                    text(
                        "SELECT printer_id, cost, production_rates, production_cost FROM print_jobs"
                    )
                ).one() == (1, 1.25, None, None)
                if dialect == "sqlite":
                    assert not conn.execute(text("PRAGMA foreign_key_check")).all()
        finally:
            engine.dispose()
