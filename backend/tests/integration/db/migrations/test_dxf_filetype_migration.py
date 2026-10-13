"""An existing Artifact survives PostgreSQL's native-enum to text upgrade."""

from uuid import uuid4

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from alembic import command
from app.db.migrate import _alembic_config
from app.db.url import normalize_database_url
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    seed_schema_row,
)

PREDECESSOR = "a23d2e57ff0c"
REVISION = "6f27f2e6090a"


def _exercise_upgrade(url: str, *, postgres: bool) -> None:
    config = _alembic_config(url)
    engine = create_engine(url)
    try:
        if postgres:
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, PREDECESSOR)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=1,
                name="Existing",
                slug="dxf-migration-existing",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "files",
                id=1,
                model_id=1,
                original_filename="existing.stl",
                file_type="STL",
                path="/library/existing.stl",
                version=1,
                size_bytes=3,
                sha256="b" * 64,
            )

        command.upgrade(config, REVISION)

        # Reflect this historical revision: current File includes later columns.
        with engine.begin() as connection:
            files = Table("files", MetaData(), autoload_with=connection)
            existing = (
                connection.execute(
                    select(files).where(files.c.original_filename == "existing.stl")
                )
                .mappings()
                .one()
            )
            assert existing["file_type"] == "STL"
            assert existing["sha256"] == "b" * 64
            seed_schema_row(
                connection,
                "files",
                id=2,
                model_id=existing["model_id"],
                original_filename="drawing.dxf",
                file_type="DXF",
                path="/library/drawing.dxf",
                version=2,
                size_bytes=12,
                sha256="c" * 64,
            )
            assert (
                connection.execute(
                    select(files.c.original_filename).where(files.c.file_type == "DXF")
                ).scalar_one()
                == "drawing.dxf"
            )

        indexes = {row["name"] for row in inspect(engine).get_indexes("files")}
        assert "uq_files_live_recommended_gcode_text" in indexes
        with pytest.raises(RuntimeError, match="DXF Artifacts"):
            command.downgrade(config, "-1")
        with engine.connect() as connection:
            assert set(
                connection.execute(select(files.c.original_filename)).scalars()
            ) == {
                "existing.stl",
                "drawing.dxf",
            }
            drawing_id = connection.execute(
                select(files.c.id).where(files.c.original_filename == "drawing.dxf")
            ).scalar_one()
        assert drawing_id is not None
        # This database intentionally stops at the historical DXF revision.
        # Delete revision-local rows without asking the current Metadata mapper
        # to select columns introduced by later migrations.
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM metadata WHERE file_id = :file_id"),
                {"file_id": drawing_id},
            )
            connection.execute(
                text("DELETE FROM files WHERE id = :file_id"),
                {"file_id": drawing_id},
            )

        command.downgrade(config, PREDECESSOR)
        with engine.connect() as connection:
            assert list(
                connection.execute(select(files.c.original_filename)).scalars()
            ) == ["existing.stl"]
        assert "uq_files_live_recommended_gcode" in {
            row["name"] for row in inspect(engine).get_indexes("files")
        }
    finally:
        engine.dispose()


class TestDxfFiletypeMigration:
    def test_sqlite_upgrade_preserves_existing_artifact(self, tmp_path) -> None:
        _exercise_upgrade(
            f"sqlite:///{tmp_path / 'dxf-upgrade.sqlite'}", postgres=False
        )

    @pytest.mark.postgres
    def test_postgres_upgrade_preserves_existing_artifact(self) -> None:
        from tests.containers import postgres_url

        root_url = normalize_database_url(postgres_url())
        database = f"dxf_upgrade_{uuid4().hex}"
        admin = create_engine(root_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
        isolated_url = (
            make_url(root_url)
            .set(database=database)
            .render_as_string(hide_password=False)
        )
        try:
            _exercise_upgrade(isolated_url, postgres=True)
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
            admin.dispose()
