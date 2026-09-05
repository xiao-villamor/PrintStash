"""Run records are additive and preserve historical archive ownership."""

import hashlib

import pytest
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.factories import (
    build_backup_destination_result,
    build_backup_retry_attempt,
    build_backup_run,
)
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    seed_schema_row,
)


class TestBackupRunUpgrade:
    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)]
    )
    def test_upgrade_retains_owned_archive_locators(self, dialect, tmp_path):
        if dialect == "postgres":
            from tests.containers import postgres_url

            url = postgres_url()
        else:
            url = f"sqlite:///{tmp_path / 'upgrade.sqlite'}"
        engine = create_engine(normalize_database_url(url))
        config = _alembic_config(url)
        archive = tmp_path / "historical.tar.gz"
        archive.write_bytes(b"historical owned archive bytes")
        try:
            if dialect == "postgres":
                with engine.begin() as connection:
                    connection.exec_driver_sql("DROP SCHEMA public CASCADE")
                    connection.exec_driver_sql("CREATE SCHEMA public")
                    create_released_v0121_postgres_schema(connection)
                command.stamp(config, RELEASED_V0121_REVISION)
            command.upgrade(config, "5f0f887bdd0b")
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "owned_storage_objects",
                    id=1,
                    backend="local",
                    namespace="historical-root",
                    key=str(archive),
                    object_kind="backup",
                    provider_ref="a" * 64,
                    state="COMMITTED",
                    size_bytes=archive.stat().st_size,
                    sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                    token="historical-token",
                )
                before = connection.execute(
                    text("SELECT * FROM owned_storage_objects WHERE id=1")
                ).one()
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT * FROM owned_storage_objects WHERE id=1")
                    ).one()
                    == before
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM backup_runs")
                    ).scalar_one()
                    == 0
                )
                context = MigrationContext.configure(
                    connection,
                    opts={
                        "compare_type": True,
                        "compare_server_default": True,
                        "target_metadata": SQLModel.metadata,
                    },
                )
                assert produce_migrations(
                    context, SQLModel.metadata
                ).upgrade_ops.is_empty()
            with Session(engine) as session:
                run = build_backup_run(session)
                result = build_backup_destination_result(session, run, ownership_id=1)
                attempt = build_backup_retry_attempt(session, result)
                assert attempt.destination_result_id == result.id
            command.downgrade(config, "5f0f887bdd0b")
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT * FROM owned_storage_objects WHERE id=1")
                    ).one()
                    == before
                )
            assert archive.read_bytes() == b"historical owned archive bytes"
        finally:
            if dialect == "postgres":
                with engine.begin() as connection:
                    connection.exec_driver_sql("DROP SCHEMA public CASCADE")
                    connection.exec_driver_sql("CREATE SCHEMA public")
                run_migrations(url)
            engine.dispose()
