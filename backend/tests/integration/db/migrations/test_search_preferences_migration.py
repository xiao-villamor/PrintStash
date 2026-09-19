"""An additive preference table preserves existing users and cascades on erasure."""

from sqlalchemy import create_engine, event, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestSearchPreferencesMigration:
    def test_preserves_existing_user_data(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'search-preferences.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "5e261c89ef07")
        engine = create_engine(url)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        try:
            with engine.begin() as connection:
                seed_schema_row(connection, "users", id=1, username="existing-user")
            command.upgrade(config, "2d8bebe0f959")
            with engine.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT username FROM users WHERE id=1")
                    ).scalar_one()
                    == "existing-user"
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM user_search_preferences")
                    ).scalar_one()
                    == 0
                )
                seed_schema_row(
                    connection,
                    "user_search_preferences",
                    user_id=1,
                    nl_filters_enabled=True,
                    timezone="Europe/Madrid",
                )
                connection.execute(text("DELETE FROM users WHERE id=1"))
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM user_search_preferences")
                    ).scalar_one()
                    == 0
                )
                assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            command.downgrade(config, "5e261c89ef07")
            command.upgrade(config, "2d8bebe0f959")
        finally:
            engine.dispose()

    def test_renders_offline_ddl(self, tmp_path):
        from io import StringIO

        config = _alembic_config(f"sqlite:///{tmp_path / 'offline.sqlite'}")
        output = StringIO()
        config.output_buffer = output
        command.upgrade(config, "5e261c89ef07:2d8bebe0f959", sql=True)
        assert "CREATE TABLE user_search_preferences" in output.getvalue()
