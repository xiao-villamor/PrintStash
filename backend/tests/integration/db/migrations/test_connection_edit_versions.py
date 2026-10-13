"""Connection upgrades preserve encrypted profile data and independent edit versions."""

from io import StringIO

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.models import StorageConnection
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories import build_storage_connection

PREVIOUS = "c9852ff533ee"
REVISION = "46d9d9f875bc"


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def legacy_connection_url(request, tmp_path):
    url = (
        fresh_postgres_database("connection_edit_upgrade")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'connection-edit.sqlite'}"
    )
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        with Session(engine) as session:
            row = build_storage_connection(session, name="Preserved")
            original_config = row.config_json
            original_secrets = row.secret_json
    finally:
        engine.dispose()
    command.downgrade(_alembic_config(url), PREVIOUS)
    return url, original_config, original_secrets


class TestConnectionEditMigration:
    def test_preserves_connection_data_through_migration(self, legacy_connection_url):
        url, original_config, original_secrets = legacy_connection_url
        config = _alembic_config(url)
        command.upgrade(config, REVISION)
        engine = create_engine(normalize_database_url(url))
        try:
            with engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT name,edit_version,length(edit_identity) FROM storage_connections"
                    )
                ).one() == ("Preserved", 1, 32)
                connection.execute(text("UPDATE storage_connections SET enabled=false"))
                assert (
                    connection.execute(
                        text("SELECT edit_version FROM storage_connections")
                    ).scalar_one()
                    == 2
                )
            command.downgrade(config, PREVIOUS)
            command.upgrade(config, REVISION)
            with Session(engine) as session:
                row = session.get(StorageConnection, 1)
                assert row is not None
                assert row.config_json == original_config
                assert row.secret_json == original_secrets
                assert row.enabled is False
                assert row.edit_version == 1
        finally:
            engine.dispose()


class TestOfflineConnectionEditMigration:
    @pytest.mark.parametrize(
        "url",
        ["sqlite:///offline.sqlite", "postgresql+psycopg://unused/unused"],
        ids=["sqlite", "postgres"],
    )
    def test_renders_the_connection_upgrade_offline(self, url):
        output = StringIO()
        config = _alembic_config(url)
        config.output_buffer = output

        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)

        assert "ADD COLUMN edit_version" in output.getvalue()
        assert "ps_connection_storage_connections_edit_v1" in output.getvalue()
