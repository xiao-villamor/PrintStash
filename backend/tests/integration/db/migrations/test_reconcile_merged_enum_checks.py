"""The post-merge CHECK rewrite accepts every Job kind from both merged lines."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


def _seed_job(connection, kind: str) -> None:
    seed_schema_row(
        connection,
        "jobs",
        id=7,
        kind=kind,
        subject_key="merged/7",
        priority="interactive",
        state="queued",
        execution_epoch=1,
        status_json="{}",
    )


class TestReconcileMergedEnumChecks:
    @pytest.mark.parametrize(
        "kind",
        [
            pytest.param("derivatives.project_profile", id="capture-line"),
            pytest.param("ingestion.samples", id="main-line"),
        ],
    )
    def test_accepts_jobs_of_either_merged_line(self, tmp_path, kind):
        url = f"sqlite:///{tmp_path / 'reconcile.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "58f2cf2d8d56")
        command.upgrade(config, "e06b32d3155c")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _seed_job(connection, kind)
                assert (
                    connection.execute(
                        text("SELECT kind FROM jobs WHERE id = 7")
                    ).scalar_one()
                    == kind
                )
        finally:
            engine.dispose()

    def test_rejects_an_unknown_job_kind(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'reconcile.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "e06b32d3155c")
        engine = create_engine(url)
        try:
            with pytest.raises(IntegrityError), engine.begin() as connection:
                _seed_job(connection, "ingestion.unknown")
        finally:
            engine.dispose()
