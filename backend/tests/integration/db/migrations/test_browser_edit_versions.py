"""Browser edit migration preserves credentials and installs the same fresh contract."""

from io import StringIO

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories import build_user

PREVIOUS = "0a76fbca8438"
REVISION = "7af8bb13c174"


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def legacy_browser_url(request, tmp_path):
    url = (
        fresh_postgres_database("browser_edit_upgrade")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'browsers.sqlite'}"
    )
    if request.param == "postgresql":
        run_migrations(url)
        command.downgrade(_alembic_config(url), PREVIOUS)
    else:
        command.upgrade(_alembic_config(url), PREVIOUS)
    engine = create_engine(normalize_database_url(url))
    try:
        with Session(engine) as session:
            actor = build_user(session)
            actor_id = actor.id
        with engine.begin() as connection:
            # The current entity contains a column absent from the historical DB.
            connection.execute(
                text(
                    "INSERT INTO browser_devices (id,user_id,name,credential_hash,created_at) VALUES (1,:owner,'Browser',:hash,CURRENT_TIMESTAMP)"
                ),
                {"owner": actor_id, "hash": "a" * 64},
            )
    finally:
        engine.dispose()
    return url


class TestBrowserEditMigration:
    def test_preserves_device_data_through_migration(self, legacy_browser_url):
        config = _alembic_config(legacy_browser_url)
        command.upgrade(config, REVISION)
        engine = create_engine(normalize_database_url(legacy_browser_url))
        try:
            with engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT name,credential_hash,edit_version FROM browser_devices"
                    )
                ).one() == ("Browser", "a" * 64, 1)
                connection.execute(text("UPDATE browser_devices SET name='Office'"))
                assert (
                    connection.execute(
                        text("SELECT edit_version FROM browser_devices")
                    ).scalar_one()
                    == 2
                )
            command.downgrade(config, PREVIOUS)
            command.upgrade(config, REVISION)
            with engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT name,credential_hash,edit_version FROM browser_devices"
                    )
                ).one() == ("Office", "a" * 64, 1)
                connection.execute(
                    text("UPDATE browser_devices SET last_used_at=CURRENT_TIMESTAMP")
                )
                assert (
                    connection.execute(
                        text("SELECT edit_version FROM browser_devices")
                    ).scalar_one()
                    == 1
                )
                connection.execute(
                    text("UPDATE browser_devices SET revoked_at=CURRENT_TIMESTAMP")
                )
                assert (
                    connection.execute(
                        text("SELECT edit_version FROM browser_devices")
                    ).scalar_one()
                    == 2
                )
        finally:
            engine.dispose()

    @pytest.mark.parametrize(
        "url", ["sqlite:///offline.sqlite", "postgresql+psycopg://unused/unused"]
    )
    def test_renders_browser_upgrade_without_a_connection(self, url):
        output = StringIO()
        config = _alembic_config(url)
        config.output_buffer = output
        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)
        sql = output.getvalue()
        assert "ADD COLUMN edit_version" in sql
        assert "ps_browser_devices_edit_v1" in sql
