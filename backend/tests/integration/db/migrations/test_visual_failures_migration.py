"""Visual quarantine extends the durable source identity without losing retries."""

from sqlalchemy import create_engine, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestVisualFailuresMigration:
    def test_extends_existing_quarantine_with_artifact_identity(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'visual.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "7fec0b53bb5c")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seed_schema_row(connection, "models", id=1, hash="a" * 64)
                seed_schema_row(connection, "files", id=2, model_id=1)
                seed_schema_row(
                    connection, "embedding_spaces", id=3, native_dimension=3
                )
                seed_schema_row(
                    connection, "index_generations", id=4, space_id=3, index_dimension=3
                )
                seed_schema_row(
                    connection,
                    "search_passages",
                    id=5,
                    subject_type="model",
                    subject_id=1,
                    recipe_version=1,
                    access_dependencies_json="[]",
                )
                seed_schema_row(
                    connection,
                    "search_index_failures",
                    id=6,
                    generation_id=4,
                    passage_id=5,
                    input_hash="b" * 64,
                    attempts=2,
                    state="retry",
                    error_code="inference_timeout",
                )
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT passage_id, file_id, attempts FROM search_index_failures WHERE id=6"
                    )
                ).one() == (5, None, 2)
                seed_schema_row(
                    connection,
                    "search_index_failures",
                    id=7,
                    generation_id=4,
                    passage_id=None,
                    file_id=2,
                    input_hash="c" * 64,
                    attempts=3,
                    state="quarantined",
                    error_code="embedding_view_failed",
                )
                assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            command.downgrade(config, "7fec0b53bb5c")
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT id, passage_id, attempts FROM search_index_failures")
                ).all() == [(6, 5, 2)]
            command.upgrade(config, "head")
        finally:
            engine.dispose()
