"""Released libraries retain their cursors when discovery gains fair paging."""

import io
import shutil

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db import migrate
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    create_released_v0121_sqlite_schema,
    seed_released_v0121_rows,
    seed_schema_row,
)

PREVIOUS = "3bcdfeec6c4f"
REVISION = "2c02cfac74a0"


@pytest.fixture(scope="module")
def previous_discovery_schema(tmp_path_factory):
    path = tmp_path_factory.mktemp("before-fair-discovery") / "vault.sqlite"
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            create_released_v0121_sqlite_schema(connection)
            seed_released_v0121_rows(connection)
        config = migrate._alembic_config(url)
        command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, PREVIOUS)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "reconcile_cursors",
                source="derivatives.mesh",
                scan_high_water=123,
                scan_position=20,
                last_pass_submitted=7,
            )
    finally:
        engine.dispose()
    return path


@pytest.fixture(scope="module")
def previous_discovery_postgres_schema():
    from tests.containers import fresh_postgres_database

    url = fresh_postgres_database("discovery_upgrade")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            create_released_v0121_postgres_schema(connection)
            seed_released_v0121_rows(connection)
        config = migrate._alembic_config(url)
        command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, PREVIOUS)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "reconcile_cursors",
                source="derivatives.mesh",
                scan_high_water=123,
                scan_position=20,
                last_pass_submitted=7,
            )
    finally:
        engine.dispose()
    return url


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def discovery_upgrade_url(request, tmp_path):
    if request.param == "postgres":
        url = request.getfixturevalue("previous_discovery_postgres_schema")
        command.downgrade(migrate._alembic_config(url), PREVIOUS)
        return url
    path = tmp_path / "vault.sqlite"
    shutil.copyfile(request.getfixturevalue("previous_discovery_schema"), path)
    return f"sqlite:///{path}"


class TestDiscoveryCursorUpgrade:
    def test_preserves_existing_discovery_state(self, discovery_upgrade_url):
        engine = create_engine(discovery_upgrade_url)
        try:
            with engine.connect() as connection:
                before = connection.execute(
                    text("SELECT id,path,sha256,thumbnail_path FROM files ORDER BY id")
                ).all()
            command.upgrade(migrate._alembic_config(discovery_upgrade_url), REVISION)
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT id,path,sha256,thumbnail_path FROM files ORDER BY id"
                        )
                    ).all()
                    == before
                )
                assert connection.execute(
                    text(
                        "SELECT scan_high_water, scan_position, last_pass_submitted, discovery_next_priority, scan_recent_at, scan_recent_file_id FROM reconcile_cursors WHERE source='derivatives.mesh'"
                    )
                ).one() == (123, 20, 7, "backfill", None, None)
            indexes = inspect(engine).get_indexes("files")
            assert any(
                index["name"] == "ix_files_uploaded_id"
                and index["column_names"] == ["uploaded_at", "id"]
                for index in indexes
            )
            assert any(
                index["name"] == "ix_files_viewer_requested_id"
                and index["column_names"] == ["viewer_requested_at", "id"]
                for index in indexes
            )
        finally:
            engine.dispose()

    def test_rejects_unknown_discovery_turn(self, discovery_upgrade_url):
        command.upgrade(migrate._alembic_config(discovery_upgrade_url), REVISION)
        engine = create_engine(discovery_upgrade_url)
        try:
            with pytest.raises(IntegrityError, match="discovery_next_priority"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE reconcile_cursors SET discovery_next_priority='urgent' WHERE source='derivatives.mesh'"
                        )
                    )
        finally:
            engine.dispose()

    @pytest.mark.parametrize(
        "assignment", ["scan_recent_file_id=1", "scan_recent_at='2026-10-05 00:00:00'"]
    )
    def test_requires_complete_recent_keyset(self, discovery_upgrade_url, assignment):
        command.upgrade(migrate._alembic_config(discovery_upgrade_url), REVISION)
        engine = create_engine(discovery_upgrade_url)
        try:
            with pytest.raises(IntegrityError, match="recent_keyset_complete"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            f"UPDATE reconcile_cursors SET {assignment} WHERE source='derivatives.mesh'"
                        )
                    )
        finally:
            engine.dispose()

    @pytest.mark.parametrize("file_id", [0, -1])
    def test_rejects_nonpositive_recent_position(self, discovery_upgrade_url, file_id):
        command.upgrade(migrate._alembic_config(discovery_upgrade_url), REVISION)
        engine = create_engine(discovery_upgrade_url)
        try:
            with pytest.raises(IntegrityError, match="recent_keyset_positive_id"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE reconcile_cursors SET scan_recent_at='2026-10-05 00:00:00', scan_recent_file_id=:file_id WHERE source='derivatives.mesh'"
                        ),
                        {"file_id": file_id},
                    )
        finally:
            engine.dispose()

    def test_roundtrip_preserves_original_discovery_state(self, discovery_upgrade_url):
        config = migrate._alembic_config(discovery_upgrade_url)
        command.upgrade(config, REVISION)
        engine = create_engine(discovery_upgrade_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE reconcile_cursors SET discovery_next_priority='interactive', scan_recent_at='2026-10-05 00:00:00', scan_recent_file_id=1 WHERE source='derivatives.mesh'"
                    )
                )
            command.downgrade(config, PREVIOUS)
            assert "discovery_next_priority" not in {
                column["name"]
                for column in inspect(engine).get_columns("reconcile_cursors")
            }
            command.upgrade(config, REVISION)
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT scan_high_water, scan_position, discovery_next_priority FROM reconcile_cursors WHERE source='derivatives.mesh'"
                    )
                ).one() == (123, 20, "backfill")
                assert (
                    connection.execute(
                        text("SELECT path FROM files WHERE id=1")
                    ).scalar_one()
                    == "models/released-model.stl"
                )
        finally:
            engine.dispose()

    def test_renders_postgres_operator_ddl(self):
        output = io.StringIO()
        config = migrate._alembic_config("postgresql+psycopg://unused/unused")
        config.output_buffer = output
        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)
        ddl = output.getvalue()
        assert "discovery_next_priority" in ddl
        assert "recent_keyset_complete" in ddl
        assert "ix_files_uploaded_id" in ddl
        assert "CREATE TYPE" not in ddl
