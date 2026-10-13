"""Scratch custody survives upgrade and blocks any downgrade that would erase it."""

from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import (
    create_pre_scratch_windows_schema,
    seed_ingestion_scratch_receipt,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PREVIOUS = "8ce8989462c0"
REVISION = "8298455ff341"
CLEANUP_KIND = "ingestion.scratch_cleanup"
FROZEN = datetime(2026, 1, 1)


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", marks=pytest.mark.postgres, id="postgresql"),
    ]
)
def scratch_database(request, tmp_path):
    url = (
        f"sqlite:///{tmp_path / 'scratch.sqlite'}"
        if request.param == "sqlite"
        else normalize_database_url(fresh_postgres_database("scratch_upgrade"))
    )
    engine = create_engine(url)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    with engine.begin() as connection:
        create_pre_scratch_windows_schema(connection)
        seed_schema_row(
            connection,
            "users",
            id=7,
            username="historical-owner",
            hashed_password="historical-password",
        )
        seed_schema_row(
            connection,
            "jobs",
            id="historical-job",
            kind="ingestion.upload",
            subject_key="historical-upload",
            owner_user_id=7,
            priority="interactive",
            state="completed",
            execution_epoch="historical-execution",
            status_json='{"processed":1}',
            attempts=2,
            resubmits=1,
            created_at=FROZEN,
            updated_at=FROZEN,
        )
        seed_schema_row(
            connection,
            "reconcile_cursors",
            source="ingestion.upload",
            nudged_at=FROZEN,
            scan_position=17,
            scan_high_water=42,
            discovery_next_priority="backfill",
        )
        seed_schema_row(
            connection,
            "derivative_group_regenerations",
            definition="derivatives.mesh",
            kind="metadata",
            requested_at=FROZEN,
            requested_by=7,
        )
    command.stamp(config, PREVIOUS)
    try:
        yield engine, config
    finally:
        engine.dispose()


class TestScratchUpgrade:
    def test_preserves_previous_work_rows(self, scratch_database):
        engine, config = scratch_database
        command.upgrade(config, REVISION)

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT id, kind, subject_key, owner_user_id, priority, state, execution_epoch, status_json, attempts, resubmits FROM jobs"
                )
            ).one() == (
                "historical-job",
                "ingestion.upload",
                "historical-upload",
                7,
                "interactive",
                "completed",
                "historical-execution",
                '{"processed":1}',
                2,
                1,
            )
            assert connection.execute(
                text(
                    "SELECT source, scan_position, scan_high_water, discovery_next_priority FROM reconcile_cursors"
                )
            ).one() == ("ingestion.upload", 17, 42, "backfill")
            assert connection.execute(
                text(
                    "SELECT definition, kind, requested_by FROM derivative_group_regenerations"
                )
            ).one() == ("derivatives.mesh", "metadata", 7)
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM ingestion_scratch_windows")
                ).scalar_one()
                == 0
            )

    def test_roundtrip_preserves_previous_work(self, scratch_database):
        engine, config = scratch_database
        command.upgrade(config, REVISION)
        command.downgrade(config, PREVIOUS)
        assert "ingestion_scratch_windows" not in inspect(engine).get_table_names()
        command.upgrade(config, REVISION)

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT execution_epoch, status_json, attempts FROM jobs WHERE id='historical-job'"
                )
            ).one() == ("historical-execution", '{"processed":1}', 2)
            assert connection.execute(
                text("SELECT scan_position, scan_high_water FROM reconcile_cursors")
            ).one() == (17, 42)
            assert (
                connection.execute(
                    text("SELECT requested_by FROM derivative_group_regenerations")
                ).scalar_one()
                == 7
            )
        assert "ingestion_scratch_windows" in inspect(engine).get_table_names()

    @pytest.mark.parametrize(
        "table,column",
        [
            pytest.param("jobs", "kind", id="job-kind"),
            pytest.param("reconcile_cursors", "source", id="source-kind"),
            pytest.param(
                "derivative_group_regenerations", "definition", id="regeneration-kind"
            ),
        ],
    )
    def test_accepts_cleanup_definition_in_each_existing_contract(
        self, scratch_database, table, column
    ):
        engine, config = scratch_database
        command.upgrade(config, REVISION)

        with engine.begin() as connection:
            connection.execute(
                text(f"UPDATE {table} SET {column}=:kind"), {"kind": CLEANUP_KIND}
            )
        with engine.connect() as connection:
            assert (
                connection.execute(text(f"SELECT {column} FROM {table}")).scalar_one()
                == CLEANUP_KIND
            )

    def test_rejects_unknown_job_definitions(self, scratch_database):
        engine, config = scratch_database
        command.upgrade(config, REVISION)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE jobs SET kind='ingestion.invented'"))

    def test_offline_postgres_upgrade_renders_custody_contract(self):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", "postgresql+psycopg://unused/unused")
        output = StringIO()
        with redirect_stdout(output):
            command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)

        ddl = output.getvalue()
        assert "CREATE TABLE ingestion_scratch_windows" in ddl
        assert "FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE SET NULL" in ddl
        assert "scratch_positive_budget CHECK (max_bytes > 0)" in ddl
        assert "CREATE INDEX ix_ingestion_scratch_due" in ddl
        for constraint in (
            "ck_jobs_kind_values",
            "ck_reconcile_cursors_source_values",
            "ck_derivative_group_regenerations_definition_values",
        ):
            statement = next(
                line
                for line in ddl.splitlines()
                if f"ADD CONSTRAINT {constraint}" in line
            )
            assert "'ingestion.scratch_cleanup'" in statement


class TestScratchCustody:
    @pytest.mark.parametrize(
        "phase,physical",
        [
            pytest.param("preparing", {}, id="preparing"),
            pytest.param("open", {"device": 1, "inode": 4}, id="open"),
            pytest.param("sealed", {"device": 1, "inode": 4}, id="sealed"),
            pytest.param(
                "release_pending", {"device": 1, "inode": 4}, id="release-pending"
            ),
            pytest.param(
                "transferred",
                {
                    "device": 1,
                    "inode": 4,
                    "transferred_path": "/vault/staging/transferred",
                },
                id="transferred",
            ),
            pytest.param("retiring", {}, id="retiring"),
        ],
    )
    def test_receipt_blocks_destructive_downgrade(
        self, scratch_database, phase, physical
    ):
        engine, config = scratch_database
        command.upgrade(config, REVISION)
        with engine.begin() as connection:
            identity = seed_ingestion_scratch_receipt(
                connection, phase=phase, **physical
            )
            before = connection.execute(
                text(
                    "SELECT phase, capacity_operation_id, marker_token, max_bytes FROM ingestion_scratch_windows WHERE id=:id"
                ),
                {"id": identity},
            ).one()

        with pytest.raises(
            RuntimeError, match="scratch receipts must be reconciled before downgrade"
        ):
            command.downgrade(config, PREVIOUS)

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT phase, capacity_operation_id, marker_token, max_bytes FROM ingestion_scratch_windows WHERE id=:id"
                    ),
                    {"id": identity},
                ).one()
                == before
            )
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM jobs WHERE id='historical-job'")
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == REVISION
            )

    def test_retains_origin_when_pruned_job_unlinks(self, scratch_database):
        engine, config = scratch_database
        command.upgrade(config, REVISION)
        if engine.dialect.name == "sqlite":

            @event.listens_for(engine, "connect")
            def foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")

            engine.dispose()
        with engine.begin() as connection:
            identity = seed_ingestion_scratch_receipt(
                connection,
                job_id="historical-job",
                origin_job_id="historical-job",
                execution_epoch="historical-execution",
            )
            connection.execute(text("DELETE FROM jobs WHERE id='historical-job'"))

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT job_id, origin_job_id, execution_epoch FROM ingestion_scratch_windows WHERE id=:id"
                ),
                {"id": identity},
            ).one() == (None, "historical-job", "historical-execution")

    @pytest.mark.parametrize(
        "invalid",
        [
            pytest.param({"kind": "invented"}, id="unknown-kind"),
            pytest.param({"phase": "invented"}, id="unknown-phase"),
            pytest.param({"max_bytes": 0}, id="zero-budget"),
            pytest.param({"max_bytes": -1}, id="negative-budget"),
            pytest.param(
                {"origin_job_id": "historical-job", "execution_epoch": ""},
                id="empty-epoch",
            ),
            pytest.param(
                {
                    "phase": "open",
                    "device": 1,
                    "inode": 4,
                    "lock_device": None,
                    "lock_inode": None,
                },
                id="open-without-lock-identity",
            ),
            pytest.param(
                {"phase": "transferred", "device": 1, "inode": 4},
                id="transfer-without-destination",
            ),
            pytest.param(
                {
                    "phase": "sealed",
                    "device": 1,
                    "inode": 4,
                    "transferred_path": "/vault/staging/other",
                },
                id="sealed-with-transfer-destination",
            ),
            pytest.param(
                {"phase": "retiring", "device": 1}, id="retiring-partial-identity"
            ),
            pytest.param(
                {"output_name": "", "output_device": 1, "output_inode": 4},
                id="empty-output-name",
            ),
            pytest.param(
                {"output_device": 1, "output_inode": 4}, id="output-without-name"
            ),
            pytest.param(
                {"origin_job_id": "historical-job"}, id="missing-execution-epoch"
            ),
            pytest.param(
                {"origin_job_id": "", "execution_epoch": "epoch"}, id="empty-origin"
            ),
            pytest.param({"execution_epoch": "epoch"}, id="missing-origin"),
            pytest.param({"phase": "open"}, id="open-without-physical-identity"),
            pytest.param(
                {"device": 1, "inode": 4}, id="preparing-with-physical-identity"
            ),
            pytest.param({"lock_inode": None}, id="partial-lock-identity"),
            pytest.param({"output_name": "part.stl"}, id="partial-output-identity"),
        ],
    )
    def test_rejects_invalid_custody_evidence(self, scratch_database, invalid):
        engine, config = scratch_database
        command.upgrade(config, REVISION)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                seed_ingestion_scratch_receipt(connection, **invalid)

    @pytest.mark.parametrize(
        "column",
        ["path", "lock_path", "capacity_operation_id"],
        ids=["workspace", "lock", "capacity-credit"],
    )
    def test_rejects_duplicate_custody_claims(self, scratch_database, column):
        engine, config = scratch_database
        command.upgrade(config, REVISION)
        with engine.begin() as connection:
            first = seed_ingestion_scratch_receipt(connection)
            value = connection.execute(
                text(f"SELECT {column} FROM ingestion_scratch_windows WHERE id=:id"),
                {"id": first},
            ).scalar_one()

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                seed_ingestion_scratch_receipt(connection, **{column: value})


class TestScratchDowngrade:
    def test_removes_only_scratch_cleanup_intent(self, scratch_database):
        engine, config = scratch_database
        command.upgrade(config, REVISION)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "jobs",
                id="cleanup-job",
                kind=CLEANUP_KIND,
                subject_key="scratch/42",
                priority="interactive",
                state="completed",
                execution_epoch="cleanup-epoch",
                status_json="{}",
            )
            seed_schema_row(
                connection,
                "reconcile_cursors",
                source=CLEANUP_KIND,
                discovery_next_priority="backfill",
            )
            seed_schema_row(
                connection,
                "derivative_group_regenerations",
                definition=CLEANUP_KIND,
                kind="metadata",
            )
        command.downgrade(config, PREVIOUS)

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT id,kind FROM jobs ORDER BY id")
            ).all() == [("historical-job", "ingestion.upload")]
            assert connection.execute(
                text("SELECT source FROM reconcile_cursors")
            ).all() == [("ingestion.upload",)]
            assert connection.execute(
                text("SELECT definition,kind FROM derivative_group_regenerations")
            ).all() == [("derivatives.mesh", "metadata")]
        assert "ingestion_scratch_windows" not in inspect(engine).get_table_names()

    @pytest.mark.parametrize(
        "table,column",
        [
            pytest.param("jobs", "kind", id="job-kind"),
            pytest.param("reconcile_cursors", "source", id="source-kind"),
            pytest.param(
                "derivative_group_regenerations", "definition", id="regeneration-kind"
            ),
        ],
    )
    def test_restores_previous_closed_definition_set(
        self, scratch_database, table, column
    ):
        engine, config = scratch_database
        command.upgrade(config, REVISION)
        command.downgrade(config, PREVIOUS)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE {table} SET {column}=:kind"), {"kind": CLEANUP_KIND}
                )

    def test_refuses_offline_downgrade_without_custody_verification(self):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", "postgresql+psycopg://unused/unused")
        with pytest.raises(
            RuntimeError, match="scratch downgrade requires online custody verification"
        ):
            command.downgrade(config, f"{REVISION}:{PREVIOUS}", sql=True)
