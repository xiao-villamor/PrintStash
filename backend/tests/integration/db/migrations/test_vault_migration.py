"""Existing libraries and notification outboxes survive the migration workflow DDL."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from app.db.migrate import _alembic_config
from app.db.url import normalize_database_url
from tests.factories.migration_rows import seed_schema_row

PREDECESSOR = "7d07ebda70ef"
REVISION = "18c4551a00dc"
TABLES = {"vault_generations", "vault_migration_runs", "vault_migration_objects"}


def exercise_upgrade(url: str, *, released_postgres: bool = False) -> None:
    config = _alembic_config(url)
    engine = create_engine(url)
    try:
        if released_postgres:
            from tests.factories.migration_rows import (
                RELEASED_V0121_REVISION,
                create_released_v0121_postgres_schema,
            )

            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, PREDECESSOR)
        with engine.begin() as connection:
            seed_schema_row(
                connection, "models", id=104, name="Existing model", hash="a" * 64
            )
            seed_schema_row(
                connection, "notification_channels", id=104, target="WEBHOOK"
            )
            seed_schema_row(
                connection,
                "notification_deliveries",
                id=104,
                channel_id=104,
                event_type="PRINT_COMPLETED",
                status="PENDING",
                context_json='{"preserved":true}',
            )

        for target in (REVISION, PREDECESSOR, REVISION):
            if target == PREDECESSOR:
                command.downgrade(config, target)
            else:
                command.upgrade(config, target)
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT name FROM models WHERE id=104")
                    ).scalar_one()
                    == "Existing model"
                )
                assert tuple(
                    connection.execute(
                        text(
                            "SELECT event_type, context_json FROM notification_deliveries WHERE id=104"
                        )
                    ).one()
                ) == ("PRINT_COMPLETED", '{"preserved":true}')
                present = set(inspect(connection).get_table_names())
                assert (
                    TABLES <= present
                    if target == REVISION
                    else TABLES.isdisjoint(present)
                )
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "notification_deliveries",
                id=105,
                channel_id=104,
                event_type="VAULT_MIGRATION",
                status="PENDING",
            )
            assert (
                connection.execute(
                    text("SELECT event_type FROM notification_deliveries WHERE id=105")
                ).scalar_one()
                == "VAULT_MIGRATION"
            )
    finally:
        engine.dispose()


def test_vault_workflow_upgrade_preserves_sqlite_data(tmp_path):
    exercise_upgrade(f"sqlite:///{tmp_path / 'upgrade.sqlite'}")


@pytest.mark.postgres
def test_vault_workflow_upgrade_preserves_postgres_data():
    from tests.containers import postgres_url

    root_url = normalize_database_url(postgres_url())
    database = f"vault_migration_upgrade_{uuid4().hex}"
    admin = create_engine(root_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    isolated_url = (
        make_url(root_url).set(database=database).render_as_string(hide_password=False)
    )
    try:
        exercise_upgrade(isolated_url, released_postgres=True)
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
        admin.dispose()
