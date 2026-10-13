"""Pending mesh work upgrades independently of historical outputs."""

import io
import shutil

import pytest
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from alembic import command
from app.db import migrate
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    create_released_v0121_sqlite_schema,
    seed_released_v0121_rows,
)

PREVIOUS = "8fd749bf52a1"
REVISION = "3bcdfeec6c4f"


@pytest.fixture(scope="module")
def previous_mesh_schema(tmp_path_factory):
    path = tmp_path_factory.mktemp("before-mesh-continuation") / "vault.sqlite"
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            create_released_v0121_sqlite_schema(connection)
            seed_released_v0121_rows(connection)
    finally:
        engine.dispose()
    config = migrate._alembic_config(url)
    command.stamp(config, RELEASED_V0121_REVISION)
    command.upgrade(config, PREVIOUS)
    return path


@pytest.fixture(scope="module")
def previous_mesh_postgres_schema():
    from tests.containers import fresh_postgres_database

    url = fresh_postgres_database("mesh_continuation_upgrade")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            create_released_v0121_postgres_schema(connection)
            seed_released_v0121_rows(connection)
    finally:
        engine.dispose()
    config = migrate._alembic_config(url)
    command.stamp(config, RELEASED_V0121_REVISION)
    command.upgrade(config, PREVIOUS)
    return url


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def mesh_upgrade_url(request, tmp_path):
    if request.param == "postgres":
        url = request.getfixturevalue("previous_mesh_postgres_schema")
        # Each check starts at the prior revision while retaining the exact
        # released rows. Each pytest process owns an isolated database.
        command.downgrade(migrate._alembic_config(url), PREVIOUS)
        return url
    path = tmp_path / "vault.sqlite"
    shutil.copy2(request.getfixturevalue("previous_mesh_schema"), path)
    return f"sqlite:///{path}"


class TestMeshContinuationMigration:
    def test_upgrade_preserves_existing_outputs(self, mesh_upgrade_url):
        engine = create_engine(mesh_upgrade_url)
        try:
            with engine.connect() as connection:
                original_files = connection.execute(
                    text("SELECT id,path,sha256,thumbnail_path FROM files ORDER BY id")
                ).all()
                original_metadata = connection.execute(
                    text("SELECT * FROM metadata ORDER BY id")
                ).all()
            command.upgrade(migrate._alembic_config(mesh_upgrade_url), REVISION)
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT id,path,sha256,thumbnail_path FROM files ORDER BY id"
                        )
                    ).all()
                    == original_files
                )
                assert (
                    connection.execute(text("SELECT * FROM metadata ORDER BY id")).all()
                    == original_metadata
                )
                assert (
                    connection.execute(
                        text("SELECT COUNT(*) FROM mesh_fingerprint_continuations")
                    ).scalar_one()
                    == 0
                )
            keys = inspect(engine).get_foreign_keys("mesh_fingerprint_continuations")
            assert [
                (
                    key["constrained_columns"],
                    key["referred_table"],
                    key["options"].get("ondelete"),
                )
                for key in keys
            ] == [(["file_id"], "files", "CASCADE")]

            # REVISION pins the historical output contract above. Convergence
            # with live models requires the full current chain, not that snapshot.
            command.upgrade(migrate._alembic_config(mesh_upgrade_url), "head")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT id,path,sha256,thumbnail_path FROM files ORDER BY id"
                        )
                    ).all()
                    == original_files
                )
                assert (
                    connection.execute(text("SELECT * FROM metadata ORDER BY id")).all()
                    == original_metadata
                )
                assert (
                    connection.execute(
                        text("SELECT COUNT(*) FROM mesh_fingerprint_continuations")
                    ).scalar_one()
                    == 0
                )
                context = MigrationContext.configure(
                    connection,
                    opts={"compare_type": True, "compare_server_default": True},
                )
                changes = produce_migrations(context, SQLModel.metadata).upgrade_ops
                assert changes.is_empty(), changes.as_diffs()
        finally:
            engine.dispose()

    def test_downgrade_roundtrip_retains_library(self, mesh_upgrade_url):
        command.upgrade(migrate._alembic_config(mesh_upgrade_url), REVISION)
        command.downgrade(migrate._alembic_config(mesh_upgrade_url), PREVIOUS)
        engine = create_engine(mesh_upgrade_url)
        try:
            assert (
                "mesh_fingerprint_continuations"
                not in inspect(engine).get_table_names()
            )
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT path FROM files WHERE id=1")
                    ).scalar_one()
                    == "models/released-model.stl"
                )
            command.upgrade(migrate._alembic_config(mesh_upgrade_url), REVISION)
            assert "mesh_fingerprint_continuations" in inspect(engine).get_table_names()
        finally:
            engine.dispose()

    def test_renders_operator_ddl_without_connection(self):
        output = io.StringIO()
        cfg = migrate._alembic_config("postgresql+psycopg://unused/unused")
        cfg.output_buffer = output
        command.upgrade(cfg, f"{PREVIOUS}:{REVISION}", sql=True)
        ddl = output.getvalue()
        assert "CREATE TABLE mesh_fingerprint_continuations" in ddl
        assert "CHECK (attempts > 0)" in ddl
        assert "REFERENCES files (id) ON DELETE CASCADE" in ddl
        assert "REFERENCES jobs" not in ddl
