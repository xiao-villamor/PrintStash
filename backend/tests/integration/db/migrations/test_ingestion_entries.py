"""The additive entry ledger upgrades existing installations without changing Jobs."""

import io

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db import migrate
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    create_released_v0121_sqlite_schema,
    seed_schema_row,
)

PREVIOUS = "85a30cfcf7ba"
REVISION = "809afb73e776"


@pytest.fixture(
    scope="module",
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)],
)
def ledger_database(request, tmp_path_factory):
    if request.param == "postgres":
        from tests.containers import fresh_postgres_database

        url = fresh_postgres_database("ingestion_entry_upgrade")
        create_released = create_released_v0121_postgres_schema
    else:
        url = (
            f"sqlite:///{tmp_path_factory.mktemp('ingestion-ledger') / 'vault.sqlite'}"
        )
        create_released = create_released_v0121_sqlite_schema
    engine = create_engine(url)
    with engine.begin() as connection:
        create_released(connection)
    config = migrate._alembic_config(url)
    command.stamp(config, RELEASED_V0121_REVISION)
    command.upgrade(config, PREVIOUS)
    with engine.begin() as connection:
        seed_schema_row(
            connection,
            "jobs",
            id="ledger-history",
            kind="ingestion.collection",
            subject_key="history",
            state="completed",
            status_json='{"processed":2}',
            execution_epoch="historical",
            priority="interactive",
        )
    yield url, engine
    engine.dispose()


def _historical_job(engine):
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT id, kind, state, status_json, execution_epoch FROM jobs WHERE id = 'ledger-history'"
            )
        ).one()


class TestIngestionEntriesMigration:
    def test_preserves_existing_job_facts(self, ledger_database):
        url, engine = ledger_database
        before = _historical_job(engine)
        command.upgrade(migrate._alembic_config(url), "head")
        assert _historical_job(engine) == before
        assert "ingestion_entries" in inspect(engine).get_table_names()
        assert {
            index["name"]
            for index in inspect(engine).get_indexes("ingestion_entries")
            if not index.get("duplicates_constraint")
        } == {
            "ix_ingestion_entries_job_state_order",
            "ix_ingestion_entries_inbox_state_order",
            "ix_ingestion_entries_job_page",
            "ix_ingestion_entries_inbox_page",
        }

        assert {
            constraint["name"]
            for constraint in inspect(engine).get_unique_constraints(
                "ingestion_entries"
            )
        } == {"uq_ingestion_entry_job_key", "uq_ingestion_entry_inbox_key"}

    def test_round_trips_the_additive_ledger(self, ledger_database):
        url, engine = ledger_database
        config = migrate._alembic_config(url)
        command.upgrade(config, "head")
        before = _historical_job(engine)
        command.downgrade(config, PREVIOUS)
        assert "ingestion_entries" not in inspect(engine).get_table_names()
        assert _historical_job(engine) == before
        command.upgrade(config, "head")
        assert "ingestion_entries" in inspect(engine).get_table_names()
        assert _historical_job(engine) == before

    @pytest.mark.parametrize(
        "url",
        ["sqlite:///unused.sqlite", "postgresql://unused:unused@localhost/unused"],
    )
    def test_renders_the_additive_ledger_without_database_io(self, url):
        output = io.StringIO()
        config = migrate._alembic_config(url)
        config.output_buffer = output
        command.upgrade(config, PREVIOUS + ":" + REVISION, sql=True)
        sql = output.getvalue()
        assert "CREATE TABLE ingestion_entries" in sql
        assert "ix_ingestion_entries_job_state_order" in sql
        assert "SET NULL" in sql
        assert "DROP TABLE jobs" not in sql
