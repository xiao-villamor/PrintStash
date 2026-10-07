"""Source upgrades preserve indexed children and independent edit versions."""

from io import StringIO

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.models import ExternalLibrary, File
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories import build_external_library, build_file, build_model

PREVIOUS = "46d9d9f875bc"
REVISION = "5291a5f0231c"


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def legacy_source_url(request, tmp_path):
    url = (
        fresh_postgres_database("source_edit_upgrade")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'source-edit.sqlite'}"
    )
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        with Session(engine) as session:
            row = build_external_library(
                session,
                tmp_path / "source",
                name="Preserved",
                scan_schedule="0 2 * * *",
            )
            build_file(
                session, build_model(session), external=True, external_library_id=row.id
            )
            original_config = row.root_path
            original_secrets = row.scan_schedule
    finally:
        engine.dispose()
    command.downgrade(_alembic_config(url), PREVIOUS)
    return url, original_config, original_secrets


class TestSourceEditMigration:
    def test_preserves_source_data_through_migration(self, legacy_source_url):
        url, original_config, original_secrets = legacy_source_url
        config = _alembic_config(url)
        command.upgrade(config, REVISION)
        engine = create_engine(normalize_database_url(url))
        try:
            with engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT name,edit_version,length(edit_identity) FROM external_libraries"
                    )
                ).one() == ("Preserved", 1, 32)
                connection.execute(text("UPDATE external_libraries SET enabled=false"))
                assert (
                    connection.execute(
                        text("SELECT edit_version FROM external_libraries")
                    ).scalar_one()
                    == 2
                )
            command.downgrade(config, PREVIOUS)
            command.upgrade(config, REVISION)
            with Session(engine) as session:
                row = session.get(ExternalLibrary, 1)
                assert row is not None
                assert row.root_path == original_config
                assert row.scan_schedule == original_secrets
                assert row.enabled is False
                assert row.edit_version == 1
                child = session.get(File, 1)
                assert child is not None
                assert child.external_library_id == row.id
        finally:
            engine.dispose()


class TestOfflineSourceEditMigration:
    @pytest.mark.parametrize(
        "url",
        ["sqlite:///offline.sqlite", "postgresql+psycopg://unused/unused"],
        ids=["sqlite", "postgres"],
    )
    def test_renders_the_source_upgrade_offline(self, url):
        output = StringIO()
        config = _alembic_config(url)
        config.output_buffer = output

        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)

        assert "ADD COLUMN edit_version" in output.getvalue()
        assert "ps_source_external_libraries_edit_v1" in output.getvalue()
