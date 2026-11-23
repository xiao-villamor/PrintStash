"""A Model Document upgrade preserves library data and revision triggers."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import seed_schema_row


class TestModelDocumentsMigration:
    @pytest.mark.parametrize(
        "dialect",
        ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)],
    )
    def test_model_documents_migration(self, tmp_path: Path, dialect: str) -> None:
        previous = "f766f983403b"
        if dialect == "sqlite":
            url = f"sqlite:///{tmp_path / 'model-documents.sqlite'}"
            config = _alembic_config(url)
            command.upgrade(config, previous)
        else:
            url = fresh_postgres_database("model_docs_upgrade")
            config = _alembic_config(url)
            run_migrations(url)
            command.downgrade(config, previous)
        engine = create_engine(normalize_database_url(url))
        try:
            with engine.begin() as conn:
                seed_schema_row(
                    conn,
                    "collections",
                    id=1,
                    name="Assembly",
                    slug="assembly",
                    path="assembly",
                )
                seed_schema_row(
                    conn,
                    "models",
                    id=2,
                    name="Bracket",
                    slug="bracket",
                    hash="a" * 64,
                    collection_id=1,
                )
                seed_schema_row(
                    conn,
                    "documents",
                    id=3,
                    name="Existing",
                    kind="MARKDOWN",
                    body="Retained",
                )
            command.upgrade(config, "head")
            with engine.begin() as conn:
                assert conn.execute(
                    text("SELECT name, body, model_id FROM documents")
                ).one() == ("Existing", "Retained", None)
                initial_revision = conn.execute(
                    text("SELECT revision FROM library_revision")
                ).scalar_one()
                conn.execute(text("UPDATE documents SET name = 'Updated' WHERE id = 3"))
                assert (
                    conn.execute(
                        text("SELECT revision FROM library_revision")
                    ).scalar_one()
                    > initial_revision
                )
                seed_schema_row(
                    conn,
                    "documents",
                    id=4,
                    name="Assembly",
                    kind="PDF",
                    filename="assembly.pdf",
                    model_id=2,
                )
            command.downgrade(config, previous)
            with engine.connect() as conn:
                assert conn.execute(
                    text("SELECT collection_id, filename FROM documents WHERE id = 4")
                ).one() == (1, "assembly.pdf")
            command.upgrade(config, "head")
            with engine.connect() as conn:
                assert any(
                    fk["constrained_columns"] == ["model_id"]
                    and fk["referred_table"] == "models"
                    for fk in inspect(conn).get_foreign_keys("documents")
                )
                if dialect == "sqlite":
                    assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        finally:
            engine.dispose()
