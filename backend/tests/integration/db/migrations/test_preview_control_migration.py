"""The preview control upgrade swaps the edit trigger without double counting."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from alembic import command
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PREVIOUS = "09036ee7701a"


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def control_url(request, tmp_path):
    if request.param == "postgresql":
        from app.db.url import normalize_database_url
        from tests.containers import fresh_postgres_database

        return normalize_database_url(fresh_postgres_database("preview_control"))
    return f"sqlite:///{tmp_path / 'control.sqlite'}"


def _edit(engine, assignment: str) -> int:
    """Apply one configuration edit and return how far the version moved."""
    with engine.begin() as connection:
        before = connection.execute(
            text("SELECT vault_edit_version FROM system_config WHERE id=1")
        ).scalar_one()
        connection.execute(text(f"UPDATE system_config SET {assignment} WHERE id=1"))
        after = connection.execute(
            text("SELECT vault_edit_version FROM system_config WHERE id=1")
        ).scalar_one()
    return after - before


def _at_previous_release(url: str):
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    engine = create_engine(url)
    if engine.dialect.name == "postgresql":
        # Supported Postgres installations bootstrap from create_all; the
        # historical baseline is SQLite-only.
        SQLModel.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, PREVIOUS)
    else:
        command.upgrade(config, PREVIOUS)
    with engine.begin() as connection:
        seed_schema_row(connection, "system_config", id=1, currency="USD")
    return config, engine


class TestPreviewControlMigration:
    def test_counts_each_edit_once_after_upgrade(self, control_url):
        config, engine = _at_previous_release(control_url)
        try:
            assert _edit(engine, "currency='EUR'") == 1
            command.upgrade(config, "55535d2c13e3")
            with engine.connect() as connection:
                # Null inherits the deployment default: existing vaults keep views.
                assert (
                    connection.execute(
                        text(
                            "SELECT derivatives_previews_enabled FROM system_config WHERE id=1"
                        )
                    ).scalar_one()
                    is None
                )
            assert _edit(engine, "currency='GBP'") == 1
            assert _edit(engine, "derivatives_previews_enabled=false") == 1
        finally:
            engine.dispose()

    def test_restores_version_one_on_downgrade(self, control_url):
        config, engine = _at_previous_release(control_url)
        try:
            command.upgrade(config, "55535d2c13e3")
            command.downgrade(config, PREVIOUS)
            assert "derivatives_previews_enabled" not in {
                column["name"]
                for column in inspect(engine).get_columns("system_config")
            }
            assert _edit(engine, "currency='JPY'") == 1
        finally:
            engine.dispose()
