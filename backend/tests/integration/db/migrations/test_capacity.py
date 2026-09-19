"""Existing library data survives additive capacity schema upgrades."""

from alembic.config import Config
from sqlalchemy import inspect, text
from sqlmodel import create_engine

from alembic import command
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestCapacityUpgrade:
    def test_preserves_existing_models(self, tmp_path):
        database = tmp_path / "capacity.sqlite"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
        command.upgrade(config, "0a6b1f868ae0")
        engine = create_engine(f"sqlite:///{database}")
        identity = 1
        with engine.begin() as connection:
            seed_schema_row(connection, "models", id=identity, name="Preserved model", slug="preserved", hash="a" * 64)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO vault_audit_runs "
                    "(requested_by, mode, state, info_count, warning_count, "
                    "critical_count, progress, cancel_requested, created_at) "
                    "VALUES (1, 'quick', 'completed', 0, 0, 0, 100, 0, "
                    "CURRENT_TIMESTAMP)"
                )
            )
        command.upgrade(config, "head")
        assert {
            "capacity_admission_events",
            "capacity_locks",
            "capacity_reservations",
            "storage_inventory_samples",
        } <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM models WHERE id=:id"), {"id": identity}
                ).scalar_one()
                == "Preserved model"
            )
            assert connection.execute(
                text(
                    "SELECT unclaimed_bytes, unclaimed_unknown_size_count "
                    "FROM vault_audit_runs LIMIT 1"
                )
            ).one() == (0, 0)
        engine.dispose()
