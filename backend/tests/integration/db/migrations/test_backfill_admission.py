"""A durable admission marker preserves historical Job execution facts."""

from __future__ import annotations

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
    seed_schema_row,
)

PREVIOUS = "2c02cfac74a0"
REVISION = "85a30cfcf7ba"
JOB_COLUMNS = (
    "id",
    "kind",
    "subject_key",
    "owner_user_id",
    "priority",
    "state",
    "status_json",
    "execution_epoch",
    "submitted_epoch",
    "attempts",
    "resubmits",
    "app_version",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
)


def _facts(engine):
    with engine.connect() as connection:
        return connection.execute(
            text(f"SELECT {','.join(JOB_COLUMNS)} FROM jobs ORDER BY id")
        ).all()


def _ownership(engine):
    inspector = inspect(engine)
    return (
        inspector.get_pk_constraint("jobs"),
        inspector.get_foreign_keys("jobs"),
        sorted(
            (
                {
                    **index,
                    "dialect_options": {
                        key: str(value) if key.endswith("_where") else value
                        for key, value in index["dialect_options"].items()
                    },
                }
                for index in inspector.get_indexes("jobs")
            ),
            key=lambda item: item["name"],
        ),
    )


@pytest.fixture(
    scope="module",
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)],
)
def prior_admission_database(request, tmp_path_factory):
    if request.param == "postgres":
        from tests.containers import fresh_postgres_database

        url = fresh_postgres_database("backfill_admission_upgrade")
        create_released = create_released_v0121_postgres_schema
    else:
        path = tmp_path_factory.mktemp("before-backfill-admission") / "vault.sqlite"
        url = f"sqlite:///{path}"
        create_released = create_released_v0121_sqlite_schema
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            create_released(connection)
        config = migrate._alembic_config(url)
        command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, PREVIOUS)
        assert {
            column["name"] for column in inspect(engine).get_columns("jobs")
        } == set(JOB_COLUMNS)
        with engine.begin() as connection:
            for job_id, state, epoch, submitted, attempts in (
                ("queued", "queued", "queued-current", None, 0),
                ("running", "running", "running-current", "running-current", 2),
                ("completed", "completed", "terminal-current", "terminal-accepted", 1),
            ):
                seed_schema_row(
                    connection,
                    "jobs",
                    id=job_id,
                    kind="derivatives.mesh",
                    subject_key=f"qualification/{job_id}",
                    priority="backfill",
                    state=state,
                    execution_epoch=epoch,
                    submitted_epoch=submitted,
                    attempts=attempts,
                    resubmits=1 if state == "running" else 0,
                    status_json='{"stage":"preserved"}',
                    app_version="1.0.0-qualification",
                )
    finally:
        engine.dispose()
    return url


@pytest.fixture
def admission_upgrade_url(prior_admission_database, tmp_path):
    if prior_admission_database.startswith("sqlite:"):
        path = tmp_path / "vault.sqlite"
        shutil.copyfile(prior_admission_database.removeprefix("sqlite:///"), path)
        return f"sqlite:///{path}"
    command.downgrade(migrate._alembic_config(prior_admission_database), PREVIOUS)
    return prior_admission_database


@pytest.fixture
def admission_upgrade(admission_upgrade_url):
    engine = create_engine(admission_upgrade_url)
    before = _facts(engine)
    ownership = _ownership(engine)
    config = migrate._alembic_config(admission_upgrade_url)
    try:
        command.upgrade(config, REVISION)
        yield engine, config, before, ownership
    finally:
        engine.dispose()


class TestBackfillAdmissionUpgrade:
    def test_preserves_existing_execution_facts(self, admission_upgrade):
        engine, _config, before, _ownership_before = admission_upgrade
        assert _facts(engine) == before
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT id,backfill_admission_epoch FROM jobs ORDER BY id")
            ).all() == [("completed", None), ("queued", None), ("running", None)]

    def test_preserves_job_ownership_constraints(self, admission_upgrade):
        engine, _config, _before, ownership = admission_upgrade
        assert _ownership(engine) == ownership
        columns = {
            column["name"]: column for column in inspect(engine).get_columns("jobs")
        }
        marker = columns["backfill_admission_epoch"]
        assert marker["nullable"] is True
        assert marker["type"].length == 64
        assert marker["default"] is None
        assert set(columns) == {*JOB_COLUMNS, "backfill_admission_epoch"}

    @pytest.mark.parametrize("epoch", [None, "running-current", "stale-attempt"])
    def test_accepts_nullable_execution_identity(self, admission_upgrade, epoch):
        engine, _config, before, _ownership_before = admission_upgrade
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE jobs SET backfill_admission_epoch=:epoch WHERE id='running'"
                ),
                {"epoch": epoch},
            )
            assert (
                connection.execute(
                    text("SELECT backfill_admission_epoch FROM jobs WHERE id='running'")
                ).scalar_one()
                == epoch
            )
        assert _facts(engine) == before

    def test_rejects_empty_admission_identity(self, admission_upgrade):
        engine, _config, _before, _ownership_before = admission_upgrade
        with pytest.raises(IntegrityError, match="backfill_admission_epoch_present"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE jobs SET backfill_admission_epoch='' WHERE id='running'"
                    )
                )

    def test_roundtrip_preserves_execution_history(self, admission_upgrade):
        engine, config, before, ownership = admission_upgrade
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE jobs SET backfill_admission_epoch=execution_epoch WHERE id='running'"
                )
            )
        command.downgrade(config, PREVIOUS)
        assert {
            column["name"] for column in inspect(engine).get_columns("jobs")
        } == set(JOB_COLUMNS)
        assert _facts(engine) == before
        assert _ownership(engine) == ownership
        command.upgrade(config, REVISION)
        assert _facts(engine) == before
        assert _ownership(engine) == ownership
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT backfill_admission_epoch FROM jobs WHERE id='running'")
                ).scalar_one()
                is None
            )

    @pytest.mark.parametrize(
        "url",
        ["sqlite:///unused.sqlite", "postgresql+psycopg://unused/unused"],
        ids=["sqlite", "postgres"],
    )
    def test_renders_operator_ddl(self, url):
        output = io.StringIO()
        config = migrate._alembic_config(url)
        config.output_buffer = output
        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)
        command.downgrade(config, f"{REVISION}:{PREVIOUS}", sql=True)
        ddl = output.getvalue()
        assert "backfill_admission_epoch" in ddl
        assert "backfill_admission_epoch_present" in ddl
        assert "VARCHAR(64)" in ddl
        assert "uq_jobs_active_subject" in ddl or "ALTER TABLE jobs" in ddl
        assert "CREATE TYPE" not in ddl
