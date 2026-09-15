"""Caption migration is additive and preserves human library fields."""

from sqlalchemy import create_engine, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestCaptionMigration:
    def test_preserves_existing_library_content(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'captions.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "10104316c059")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "models",
                    id=1,
                    hash="a" * 64,
                    description="Human description",
                )
                seed_schema_row(
                    connection, "documents", id=2, body="Human document body"
                )
                seed_schema_row(
                    connection, "system_config", id=1, ai_search_settings_json="{}"
                )
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT description FROM models WHERE id=1")
                    ).scalar_one()
                    == "Human description"
                )
                assert (
                    connection.execute(
                        text("SELECT body FROM documents WHERE id=2")
                    ).scalar_one()
                    == "Human document body"
                )
                assert connection.execute(
                    text(
                        "SELECT ai_search_settings_json, ai_search_configured_by FROM system_config WHERE id=1"
                    )
                ).one() == ("{}", None)
                seed_schema_row(
                    connection,
                    "subject_captions",
                    id=3,
                    subject_type="model",
                    subject_id=1,
                    model_id=1,
                    collection_id=None,
                    multipart_model_id=None,
                    document_id=None,
                    state="edited",
                    phase="ready",
                    text="Separate caption",
                    attempts=0,
                )
                assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            command.downgrade(config, "10104316c059")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT description FROM models WHERE id=1")
                    ).scalar_one()
                    == "Human description"
                )
            command.upgrade(config, "head")
        finally:
            engine.dispose()
