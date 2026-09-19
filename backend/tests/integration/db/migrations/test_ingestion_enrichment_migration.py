"""Existing sources survive both directions through background-work installation."""

from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestIngestionEnrichmentMigrationContract:
    def test_installs_background_work_without_rewriting_existing_sources(
        self, tmp_path
    ):
        url = f"sqlite:///{tmp_path / 'enrichment.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "b49f72e927c9")
        engine = create_engine(url)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=1,
                name="Existing",
                slug="existing",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "files",
                id=1,
                model_id=1,
                path="models/original.stl",
                filename="original.stl",
                sha256="b" * 64,
                file_type="STL",
                size_bytes=100,
                thumbnail_path="thumbs/existing.webp",
            )
            connection.execute(
                text(
                    "UPDATE models SET thumbnail_path='thumbs/existing.webp', thumbnail_file_id=1 WHERE id=1"
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT path, sha256 FROM files WHERE id=1")
            ).one() == ("models/original.stl", "b" * 64)
            assert connection.execute(
                text(
                    "SELECT thumbnail_selection_version, thumbnail_path, thumbnail_file_id FROM models WHERE id=1"
                )
            ).one() == (0, "thumbs/existing.webp", 1)
            assert (
                connection.execute(
                    text("SELECT thumbnail_path FROM files WHERE id=1")
                ).scalar_one()
                == "thumbs/existing.webp"
            )
            assert {
                "artifact_analysis_generations",
                "search_projection_requests",
                "ingestion_reviews",
            } <= set(inspect(connection).get_table_names())
        engine.dispose()

    def test_removes_background_work_without_losing_library_rows(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'enrichment.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "head")
        engine = create_engine(url)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=1,
                name="Existing",
                slug="existing",
                hash="a" * 64,
            )
        command.downgrade(config, "b49f72e927c9")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM models WHERE id=1")
                ).scalar_one()
                == "Existing"
            )
            assert not {
                "artifact_analysis_generations",
                "search_projection_requests",
                "ingestion_reviews",
            } & set(inspect(connection).get_table_names())
        engine.dispose()
