"""Project derivation upgrades retain Artifacts; rollback drops only its derived work."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

BEFORE = "748a97ada8ec"
PROFILE = "ba28991f0dd2"


class TestProjectProfileMigration:
    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
    )
    def test_round_trips_without_losing_the_library(self, tmp_path, dialect):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        url = (
            f"sqlite:///{tmp_path / 'project-profile.sqlite'}"
            if dialect == "sqlite"
            else normalize_database_url(fresh_postgres_database("profile_upgrade"))
        )
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url)
        if dialect == "postgresql":
            # PostgreSQL installations start from create_all, not the legacy
            # SQLite-only baseline. Exercise upgrades from the released schema.
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, BEFORE)
        with engine.begin() as connection:
            seed_schema_row(
                connection, "models", id=1, name="Rack", slug="rack", hash="a" * 64
            )
            seed_schema_row(
                connection,
                "files",
                id=1,
                model_id=1,
                file_type="3mf",
                original_filename="rack.3mf",
                sha256="a" * 64,
                version=1,
                path="rack.3mf",
            )
        command.upgrade(config, PROFILE)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "artifact_derivatives",
                file_id=1,
                kind="project_profile",
                recipe_version=1,
                state="ready",
            )
            seed_schema_row(
                connection,
                "jobs",
                id="profile-job",
                kind="derivatives.project_profile",
                subject_key="file/1",
                priority="backfill",
                state="completed",
            )

        command.downgrade(config, BEFORE)

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT original_filename FROM files WHERE id=1")
                ).scalar_one()
                == "rack.3mf"
            )
            assert (
                connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM artifact_derivatives)"
                        " + (SELECT count(*) FROM jobs)"
                    )
                ).scalar_one()
                == 0
            )
        command.upgrade(config, PROFILE)
        engine.dispose()
