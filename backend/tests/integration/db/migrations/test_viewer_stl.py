"""Existing Artifacts and Jobs survive adding durable viewer demand."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import (
    create_pre_derivative_controls_schema,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestViewerStlUpgrade:
    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
    )
    def test_retains_library_on_upgrade(self, tmp_path, dialect):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        url = (
            f"sqlite:///{tmp_path / 'old-viewer.sqlite'}"
            if dialect == "sqlite"
            else normalize_database_url(fresh_postgres_database("viewer_upgrade"))
        )
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url)
        if dialect == "postgresql":
            with engine.begin() as connection:
                create_pre_derivative_controls_schema(connection)
            command.stamp(config, "489225f7b46b")
        command.upgrade(config, "a1d9da54fb03")
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=1,
                name="Existing model",
                slug="existing",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "files",
                id=1,
                model_id=1,
                file_type="3mf",
                original_filename="existing.3mf",
                sha256="a" * 64,
                version=1,
                path="existing.3mf",
            )
            seed_schema_row(
                connection,
                "jobs",
                id="existing-job",
                kind="derivatives.mesh",
                subject_key="file/1",
                priority="interactive",
                state="completed",
            )
        command.upgrade(config, "f1e72b50f1cc")

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT original_filename, viewer_requested_at FROM files WHERE id=1"
                )
            ).one() == ("existing.3mf", None)
            assert (
                connection.execute(
                    text("SELECT kind FROM jobs WHERE id='existing-job'")
                ).scalar_one()
                == "derivatives.mesh"
            )
        command.downgrade(config, "a1d9da54fb03")
        command.upgrade(config, "f1e72b50f1cc")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT original_filename FROM files WHERE id=1")
                ).scalar_one()
                == "existing.3mf"
            )
        engine.dispose()
