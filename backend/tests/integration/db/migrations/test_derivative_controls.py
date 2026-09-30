"""Existing vaults retain data and inherit deployment processing defaults."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import (
    create_pre_derivative_controls_schema,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestDerivativeControlsUpgrade:
    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
    )
    def test_upgrades_existing_configuration_without_a_policy_backfill(
        self, dialect, tmp_path
    ):
        url = (
            f"sqlite:///{tmp_path / 'old-vault.sqlite'}"
            if dialect == "sqlite"
            else normalize_database_url(fresh_postgres_database("derivative_upgrade"))
        )
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url)
        if dialect == "sqlite":
            command.upgrade(config, "489225f7b46b")
        else:
            # The historical baseline has a PostgreSQL FK cycle; start at the actual
            # predecessor schema, then run the new migration with existing rows.
            with engine.begin() as connection:
                create_pre_derivative_controls_schema(connection)
            command.stamp(config, "489225f7b46b")
        try:
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "system_config",
                    id=1,
                    auto_mark_known_good=False,
                    currency="EUR",
                )
                seed_schema_row(
                    connection,
                    "models",
                    id=1,
                    name="Existing model",
                    slug="existing",
                    hash="a" * 64,
                )
            command.upgrade(config, "a1d9da54fb03")
            with engine.connect() as connection:
                values = connection.execute(
                    text(
                        "SELECT derivatives_mesh_enabled, derivatives_gcode_enabled, derivatives_toolpath_enabled, auto_mark_known_good, currency FROM system_config WHERE id=1"
                    )
                ).one()
                assert values == (None, None, None, False, "EUR")
                assert (
                    connection.execute(
                        text("SELECT name FROM models WHERE id=1")
                    ).scalar_one()
                    == "Existing model"
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM derivative_group_regenerations")
                    ).scalar_one()
                    == 0
                )
        finally:
            engine.dispose()
