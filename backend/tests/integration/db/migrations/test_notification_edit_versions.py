"""Notification migration preserves channel data and independent configuration triggers."""

from io import StringIO

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories import build_notification_channel, build_system_config

PREVIOUS = "7af8bb13c174"
REVISION = "c9852ff533ee"


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def legacy_notification_url(request, tmp_path):
    url = (
        fresh_postgres_database("notification_edit_upgrade")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'notification-edit.sqlite'}"
    )
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        with Session(engine) as session:
            build_system_config(session, currency="EUR", notifications_enabled=True)
            build_notification_channel(
                session, name="Preserved", events=["print_completed"]
            )
    finally:
        engine.dispose()
    command.downgrade(_alembic_config(url), PREVIOUS)
    return url


class TestNotificationEditMigration:
    def test_preserves_notification_data_through_migration(
        self, legacy_notification_url
    ):
        config = _alembic_config(legacy_notification_url)
        command.upgrade(config, REVISION)
        engine = create_engine(normalize_database_url(legacy_notification_url))
        try:
            with engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT name,edit_version,length(edit_identity),events_json FROM notification_channels"
                    )
                ).one() == ("Preserved", 1, 32, '["print_completed"]')
                assert connection.execute(
                    text(
                        "SELECT notifications_enabled,notification_edit_version,vault_edit_version FROM system_config"
                    )
                ).one() == (True, 1, 1)
                connection.execute(
                    text("UPDATE notification_channels SET last_status='sent'")
                )
                assert (
                    connection.execute(
                        text("SELECT edit_version FROM notification_channels")
                    ).scalar_one()
                    == 1
                )
                connection.execute(
                    text("UPDATE notification_channels SET enabled=false")
                )
                assert (
                    connection.execute(
                        text("SELECT edit_version FROM notification_channels")
                    ).scalar_one()
                    == 2
                )
                connection.execute(
                    text("UPDATE system_config SET notifications_enabled=false")
                )
                assert connection.execute(
                    text(
                        "SELECT notification_edit_version,vault_edit_version FROM system_config"
                    )
                ).one() == (2, 1)
            command.downgrade(config, PREVIOUS)
            command.upgrade(config, REVISION)
            with engine.begin() as connection:
                connection.execute(text("UPDATE system_config SET currency='GBP'"))
                assert connection.execute(
                    text(
                        "SELECT currency,notification_edit_version,vault_edit_version FROM system_config"
                    )
                ).one() == ("GBP", 1, 2)
                assert connection.execute(
                    text("SELECT name,enabled,edit_version FROM notification_channels")
                ).one() == ("Preserved", False, 1)
        finally:
            engine.dispose()


class TestOfflineNotificationEditMigration:
    @pytest.mark.parametrize(
        "url",
        ["sqlite:///offline.sqlite", "postgresql+psycopg://unused/unused"],
        ids=["sqlite", "postgres"],
    )
    def test_renders_the_notification_upgrade_offline(self, url):
        output = StringIO()
        config = _alembic_config(url)
        config.output_buffer = output
        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)
        sql = output.getvalue()
        assert "ADD COLUMN notification_edit_version" in sql
        assert "CREATE TRIGGER" in sql
        assert "ps_notification_notification_channels_edit_v1" in sql
