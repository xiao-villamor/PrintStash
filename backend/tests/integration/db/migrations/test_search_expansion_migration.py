"""Sparse tables upgrade populated libraries without rewriting original passages."""

from sqlalchemy import create_engine, event, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestSearchExpansionMigration:
    def test_preserves_original_passages_through_upgrade(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'sparse.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "2d8bebe0f959")
        engine = create_engine(url)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        try:
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "search_passages",
                    id=1,
                    subject_type="model",
                    subject_id=1,
                    visibility_segment_key="x",
                    access_dependencies_json="[]",
                    chunk_index=0,
                    recipe_version=1,
                    content_hash="a" * 64,
                    text="original bicycle",
                    title="bicycle",
                )
            command.upgrade(config, "b49f72e927c9")
            with engine.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT text FROM search_passages WHERE id=1")
                    ).scalar_one()
                    == "original bicycle"
                )
                seed_schema_row(
                    connection,
                    "search_expansions",
                    passage_id=1,
                    input_hash="a" * 64,
                    recipe="b" * 64,
                    phase="ready",
                    attempts=1,
                )
                seed_schema_row(
                    connection,
                    "search_expansion_terms",
                    passage_id=1,
                    term="bike",
                    weight=1.25,
                )
                assert (
                    connection.execute(
                        text("SELECT weight FROM search_expansion_terms")
                    ).scalar_one()
                    == 1.25
                )
            command.downgrade(config, "2d8bebe0f959")
            with engine.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT text FROM search_passages WHERE id=1")
                    ).scalar_one()
                    == "original bicycle"
                )
            command.upgrade(config, "b49f72e927c9")
            with engine.begin() as connection:
                assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        finally:
            engine.dispose()
