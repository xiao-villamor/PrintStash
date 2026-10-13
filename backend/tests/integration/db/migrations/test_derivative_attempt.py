"""Upgrades revoke unprovable old executions while retaining published outputs."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import (
    create_pre_derivative_attempt_schema,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestDerivativeAttemptUpgrade:
    @pytest.mark.parametrize(
        "dialect",
        [
            pytest.param("sqlite", id="sqlite"),
            pytest.param("postgresql", marks=pytest.mark.postgres, id="postgresql"),
        ],
    )
    def test_preserves_outputs_while_revoking_legacy_running_attempts(
        self, dialect, tmp_path
    ):
        url = (
            f"sqlite:///{tmp_path / 'old-vault.sqlite'}"
            if dialect == "sqlite"
            else normalize_database_url(
                fresh_postgres_database("derivative_attempt_upgrade")
            )
        )
        engine = create_engine(url)
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", url)
        with engine.begin() as connection:
            create_pre_derivative_attempt_schema(connection)
            seed_schema_row(
                connection,
                "models",
                id=1,
                name="Existing",
                slug="existing",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "files",
                id=1,
                model_id=1,
                path="existing.stl",
                original_filename="existing.stl",
                file_type="stl",
                version=1,
                size_bytes=10,
                sha256="b" * 64,
            )
            seed_schema_row(
                connection,
                "artifact_derivatives",
                id=1,
                file_id=1,
                kind="thumbnail",
                recipe_version=2,
                state="running",
                attempts=3,
                failure_reason=None,
                storage_key="old.webp",
                output_json='{"size":10}',
            )
            seed_schema_row(
                connection,
                "artifact_derivatives",
                id=2,
                file_id=1,
                kind="metadata",
                recipe_version=3,
                state="ready",
                attempts=1,
                failure_reason=None,
                output_json='{"triangle_count":12}',
            )
            seed_schema_row(
                connection,
                "artifact_derivatives",
                id=3,
                file_id=1,
                kind="viewer_stl",
                recipe_version=1,
                state="ready",
                attempts=1,
                storage_key="existing-viewer.stl",
                output_json='{"size":20}',
            )
            for job_id, attempts, state in [
                ("waiting", 0, "queued"),
                ("legacy", 4, "running"),
                ("done", 2, "completed"),
                ("viewer", 1, "completed"),
            ]:
                seed_schema_row(
                    connection,
                    "jobs",
                    id=job_id,
                    kind="derivatives.viewer_stl"
                    if job_id == "viewer"
                    else "derivatives.mesh",
                    subject_key=f"file/{job_id}",
                    state=state,
                    attempts=attempts,
                    priority="backfill",
                    resubmits=0,
                    status_json="{}",
                )
        command.stamp(config, "f1e72b50f1cc")

        command.upgrade(config, "67494831ae72")

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT state, attempts, failure_reason, storage_key, output_json, attempt_token FROM artifact_derivatives WHERE id=1"
                )
            ).one() == (
                "failed",
                2,
                "attempt_interrupted",
                "old.webp",
                '{"size":10}',
                None,
            )
            assert connection.execute(
                text(
                    "SELECT state, output_json, attempt_token FROM artifact_derivatives WHERE id=2"
                )
            ).one() == ("ready", '{"triangle_count":12}', None)
            assert connection.execute(
                text(
                    "SELECT state, storage_key, output_json, attempt_token FROM artifact_derivatives WHERE id=3"
                )
            ).one() == ("ready", "existing-viewer.stl", '{"size":20}', None)
            assert connection.execute(
                text(
                    "SELECT id, attempts, execution_epoch, submitted_epoch FROM jobs ORDER BY id"
                )
            ).all() == [
                ("done", 2, "done", "done"),
                ("legacy", 4, "legacy", "legacy"),
                ("viewer", 1, "viewer", "viewer"),
                ("waiting", 0, "waiting", None),
            ]
        command.downgrade(config, "f1e72b50f1cc")
        command.upgrade(config, "67494831ae72")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT storage_key FROM artifact_derivatives WHERE id=1")
                ).scalar_one()
                == "old.webp"
            )
        engine.dispose()
