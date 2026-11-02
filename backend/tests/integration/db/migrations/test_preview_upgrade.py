"""Additive preview upgrades retain primary output and existing discovery state."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from alembic import command
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def preview_upgrade_url(request, tmp_path):
    if request.param == "postgresql":
        from app.db.url import normalize_database_url
        from tests.containers import fresh_postgres_database

        return normalize_database_url(fresh_postgres_database("preview_upgrade"))
    return f"sqlite:///{tmp_path / 'previews.sqlite'}"


class TestPreviewUpgrade:
    def test_retains_existing_primary_output(self, preview_upgrade_url):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", preview_upgrade_url)
        engine = create_engine(preview_upgrade_url)
        if engine.dialect.name == "postgresql":
            # Supported Postgres installations bootstrap from create_all; the
            # historical baseline is SQLite-only. Downgrade this empty schema
            # to establish the preceding release shape.
            SQLModel.metadata.create_all(engine)
            command.stamp(config, "head")
            command.downgrade(config, "506719fa4a57")
        else:
            command.upgrade(config, "506719fa4a57")
        try:
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "models",
                    id=7,
                    name="Existing Model",
                    slug="existing",
                    hash="a" * 64,
                    edit_version=1,
                    thumbnail_path="original.webp",
                )
                seed_schema_row(
                    connection,
                    "reconcile_cursors",
                    source="derivatives.mesh",
                    scan_position=12,
                    scan_high_water=20,
                )
            command.upgrade(config, "09036ee7701a")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT thumbnail_path FROM models WHERE id=7")
                    ).scalar_one()
                    == "original.webp"
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT scan_position FROM reconcile_cursors WHERE source='derivatives.mesh'"
                        )
                    ).scalar_one()
                    == 12
                )
            assert "secondary_deferred_at" in {
                column["name"]
                for column in inspect(engine).get_columns("reconcile_cursors")
            }
            with engine.connect() as connection:
                # Roll back new rows before checking downgrade compatibility.
                transaction = connection.begin()
                seed_schema_row(
                    connection,
                    "files",
                    id=8,
                    model_id=7,
                    file_type="stl",
                    version=1,
                    path="source.stl",
                    sha256="b" * 64,
                )
                seed_schema_row(
                    connection,
                    "artifact_derivatives",
                    file_id=8,
                    kind="previews",
                    state="queued",
                    recipe_version=1,
                    output_json="{}",
                )
                assert (
                    connection.execute(
                        text("SELECT kind FROM artifact_derivatives WHERE file_id=8")
                    ).scalar_one()
                    == "previews"
                )
                transaction.rollback()
            command.downgrade(config, "506719fa4a57")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT thumbnail_path FROM models WHERE id=7")
                    ).scalar_one()
                    == "original.webp"
                )
        finally:
            engine.dispose()
