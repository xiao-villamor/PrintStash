"""Upgrading into the job engine keeps what an installation already proved.

Two revisions cross this boundary: ``73abd9b883d9`` renames ``background_jobs`` to
``jobs`` (and every ``background_job_id`` to ``job_id``) and settles the jobs no
process will ever run again; ``a83ea973fad4`` reshapes ``jobs`` and carries the
thumbnail and metadata an upgraded library already has into
``artifact_derivatives`` before the tables that recorded them are dropped.

If this goes red, a self-hoster upgrading either loses Job history and staging
ownership, is left with Jobs that claim to be running forever, or has their whole
library re-rendered from scratch — or, on PostgreSQL, cannot upgrade at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, inspect, text

from alembic import command
from app.db.migrate import _alembic_config, run_migrations, schema_is_current
from app.db.url import normalize_database_url
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    create_released_v0121_sqlite_schema,
    seed_released_v0121_rows,
    seed_schema_row,
)

BEFORE = "0118bda3e719"
SENTINEL = "ext-file-sentinel-0000000000000000000000000000000000000000000"
EARLIER = datetime(2026, 1, 1)
LATER = datetime(2999, 1, 1)
# Every kind the retired registry wrote, and the definition it became.
LEGACY_KINDS = {
    "ingest": "ingestion.upload",
    "gcode": "ingestion.upload",
    "model": "ingestion.upload",
    "artifact": "ingestion.upload",
    "url": "ingestion.url",
    "archive_manifest": "ingestion.archive_inspect",
    "archive": "ingestion.archive_selection",
    "url_selection": "ingestion.url_selection",
    "collection": "ingestion.collection",
    "library_import": "ingestion.library_import",
    "pending_import": "ingestion.inbox_import",
    "external_scan": "sources.scan",
    "ai_search": "search.generation",
    "ai_caption": "search.caption",
    "model_download": "inference.model_download",
}


@dataclass
class Upgraded:
    engine: Engine
    config: object

    def rows(self, sql: str, **params: object) -> list[tuple]:
        with self.engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql), params)]


class TestRunMigrations:
    @pytest.mark.parametrize(
        "starting_point",
        [
            "sqlite-release",
            "sqlite-0.13",
            "sqlite-before-check",
            pytest.param("postgres-release", marks=pytest.mark.postgres),
        ],
    )
    def test_upgrades_legacy_generic_jobs(
        self, tmp_path: Path, starting_point: str
    ) -> None:
        if starting_point == "postgres-release":
            from tests.containers import fresh_postgres_database

            url = fresh_postgres_database("generic_jobs")
            create_released_schema = create_released_v0121_postgres_schema
        else:
            url = f"sqlite:///{tmp_path / 'legacy-generic-jobs.sqlite'}"
            create_released_schema = create_released_v0121_sqlite_schema

        engine = create_engine(normalize_database_url(url))
        config = _alembic_config(url)
        try:
            with engine.begin() as connection:
                create_released_schema(connection)
                seed_released_v0121_rows(connection)
                for job_id, kind, state in (
                    ("legacy-completed", "generic", "completed"),
                    ("legacy-running", "generic", "running"),
                    ("legacy-url", "url", "completed"),
                ):
                    seed_schema_row(
                        connection,
                        "background_jobs",
                        id=job_id,
                        owner_user_id=1,
                        kind=kind,
                        state=state,
                        status_json='{"model_id":1,"file_id":1}',
                    )
                seed_schema_row(
                    connection,
                    "inbox_items",
                    id=1,
                    owner_user_id=1,
                    source_kind="BROWSER",
                    state="COMPLETED",
                    manifest_json="{}",
                    requested_tags_json="[]",
                    background_job_id="legacy-completed",
                )
            command.stamp(config, RELEASED_V0121_REVISION)
            earlier_revision = {
                "sqlite-0.13": "e916c791628c",
                "sqlite-before-check": "ca576b5337d2",
            }.get(starting_point)
            if earlier_revision is not None:
                command.upgrade(config, earlier_revision)

            run_migrations(url)
            run_migrations(url)

            assert schema_is_current(url)
            database = Upgraded(engine=engine, config=config)
            assert database.rows(
                "SELECT id, kind, state, owner_user_id FROM jobs ORDER BY id"
            ) == [
                ("legacy-completed", "ingestion.upload", "completed", 1),
                ("legacy-running", "ingestion.upload", "failed", 1),
                ("legacy-url", "ingestion.url", "completed", 1),
            ]
            assert database.rows("SELECT job_id FROM inbox_items WHERE id = 1") == [
                ("legacy-completed",)
            ]
            assert database.rows("SELECT id, name FROM models") == [
                (1, "Released model")
            ]
        finally:
            engine.dispose()


def _seed(engine: Engine) -> None:
    with engine.begin() as connection:
        seed_schema_row(
            connection,
            "users",
            id=1,
            username="upgrader",
            hashed_password="not-a-real-hash",
            is_active=True,
            is_superuser=False,
        )
        seed_schema_row(connection, "models", id=1, name="Bracket", slug="bracket")
        files = {
            1: ("STL", f"{1:064x}", None),
            2: ("STL", f"{2:064x}", None),
            3: ("GCODE", f"{3:064x}", "thumbs/3.webp"),
            4: ("STL", SENTINEL, "thumbs/4.webp"),
            5: ("THREE_MF", f"{5:064x}", "thumbs/5.webp"),
        }
        for file_id, (file_type, sha, thumbnail) in files.items():
            seed_schema_row(
                connection,
                "files",
                id=file_id,
                model_id=1,
                version=file_id,
                file_type=file_type,
                sha256=sha,
                original_filename=f"part-{file_id}",
                thumbnail_path=thumbnail,
            )
        seed_schema_row(connection, "metadata", file_id=1, triangle_count=100)
        seed_schema_row(connection, "metadata", file_id=2, triangle_count=None)
        seed_schema_row(connection, "metadata", file_id=3)
        seed_schema_row(
            connection,
            "thumbnail_generations",
            file_id=5,
            source_sha256=f"{5:064x}",
            recipe_fingerprint="preview-w512",
            state="ready",
            storage_key="thumbs/5.webp",
            complete=True,
            attempts=1,
        )
        for job_id, state in (
            ("pending-job", "pending"),
            ("running-job", "running"),
            ("completed-job", "completed"),
            ("failed-job", "failed"),
        ):
            seed_schema_row(
                connection,
                "background_jobs",
                id=job_id,
                owner_user_id=1,
                visible=True,
                kind="ingest",
                state=state,
                status_json='{"stage":"parsing","progress":40,"error":"old"}',
                replay_safe=False,
                attempts=1,
                created_at=EARLIER,
                updated_at=EARLIER,
            )
        for legacy in (*LEGACY_KINDS, "thumbnail_rebuild"):
            seed_schema_row(
                connection,
                "background_jobs",
                id=f"legacy-{legacy}",
                owner_user_id=1,
                visible=True,
                kind=legacy,
                state="completed",
                status_json="{}",
                replay_safe=False,
                attempts=1,
                created_at=EARLIER,
                updated_at=EARLIER,
            )
        seed_schema_row(
            connection,
            "staging_leases",
            id="pending-lease",
            path="/staging/pending.stl",
            owner_user_id=1,
            background_job_id="pending-job",
            size_bytes=1,
            sha256="a" * 64,
            expires_at=LATER,
            created_at=EARLIER,
        )
        seed_schema_row(
            connection,
            "inbox_items",
            id=1,
            owner_user_id=1,
            source_kind="BROWSER",
            state="COMPLETED",
            manifest_json="{}",
            requested_tags_json="[]",
            background_job_id="completed-job",
            retryable=False,
            attempt_count=0,
        )


@pytest.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)],
    ids=str,
)
def upgraded(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Upgraded]:
    """A populated pre-engine database, then upgraded to head."""
    if request.param == "postgres":
        from tests.containers import fresh_postgres_database

        # A database of its own: a downgrade below a merge revision leaves two
        # heads recorded, which would break every later user of a shared one.
        url = fresh_postgres_database("migration")
    else:
        url = f"sqlite:///{tmp_path / 'job-engine-upgrade.sqlite'}"
    engine = create_engine(normalize_database_url(url))
    config = _alembic_config(url)
    try:
        if request.param == "postgres":
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, BEFORE)
        _seed(engine)
        command.upgrade(config, "head")
        yield Upgraded(engine=engine, config=config)
    finally:
        engine.dispose()


class TestRenameBackgroundJobs:
    def test_keeps_every_job_row(self, upgraded: Upgraded) -> None:
        ids = upgraded.rows(
            "SELECT id FROM jobs WHERE id NOT LIKE 'legacy-%' ORDER BY id"
        )

        assert ids == [
            ("completed-job",),
            ("failed-job",),
            ("pending-job",),
            ("running-job",),
        ]

    @pytest.mark.parametrize(("legacy", "current"), sorted(LEGACY_KINDS.items()))
    def test_renames_each_legacy_kind_to_its_definition(
        self, upgraded: Upgraded, legacy: str, current: str
    ) -> None:
        # The kind column only admits current definitions; a legacy name
        # would make the whole upgrade fail on its CHECK constraint.
        rows = upgraded.rows(
            "SELECT kind FROM jobs WHERE id = :id", id=f"legacy-{legacy}"
        )

        assert rows == [(current,)]

    def test_drops_a_thumbnail_rebuild_that_has_no_successor(
        self, upgraded: Upgraded
    ) -> None:
        rows = upgraded.rows(
            "SELECT id FROM jobs WHERE id = 'legacy-thumbnail_rebuild'"
        )

        assert rows == []

    def test_keeps_a_pending_import_pointing_at_its_job(
        self, upgraded: Upgraded
    ) -> None:
        rows = upgraded.rows("SELECT job_id FROM inbox_items WHERE id = 1")

        assert rows == [("completed-job",)]

    def test_keeps_a_staging_lease_owned_by_its_job(self, upgraded: Upgraded) -> None:
        rows = upgraded.rows(
            "SELECT job_id FROM staging_leases WHERE id = 'pending-lease'"
        )

        assert rows == [("pending-job",)]

    @pytest.mark.parametrize("job_id", ["pending-job", "running-job"])
    def test_fails_a_job_that_was_still_in_flight(
        self, upgraded: Upgraded, job_id: str
    ) -> None:
        rows = upgraded.rows(
            "SELECT state, status_json FROM jobs WHERE id = :id", id=job_id
        )

        assert rows == [("failed", '{"error":"interrupted_by_upgrade"}')]

    def test_marks_an_interrupted_job_finished(self, upgraded: Upgraded) -> None:
        rows = upgraded.rows(
            "SELECT finished_at IS NOT NULL FROM jobs WHERE id = 'running-job'"
        )

        assert rows == [(True,)]

    def test_keeps_a_completed_job_completed(self, upgraded: Upgraded) -> None:
        rows = upgraded.rows(
            "SELECT state, status_json FROM jobs WHERE id = 'completed-job'"
        )

        assert rows == [("completed", "{}")]

    def test_replaces_a_failed_jobs_old_payload(self, upgraded: Upgraded) -> None:
        rows = upgraded.rows(
            "SELECT state, status_json FROM jobs WHERE id = 'failed-job'"
        )

        assert rows == [("failed", '{"error":"failed_before_upgrade"}')]

    def test_expires_the_staging_lease_of_an_interrupted_job(
        self, upgraded: Upgraded
    ) -> None:
        rows = upgraded.rows(
            "SELECT expires_at < :later FROM staging_leases WHERE id = 'pending-lease'",
            later=LATER,
        )

        assert rows == [(True,)]


class TestJobEngineSchema:
    def test_gives_a_legacy_job_its_own_subject(self, upgraded: Upgraded) -> None:
        rows = upgraded.rows(
            "SELECT subject_key, priority, resubmits FROM jobs WHERE id = 'failed-job'"
        )

        assert rows == [("legacy/failed-job", "interactive", 0)]

    def test_drops_the_thumbnail_coordination_tables(self, upgraded: Upgraded) -> None:
        tables = set(inspect(upgraded.engine).get_table_names())

        assert {"thumbnail_generations", "thumbnail_render_slots"} & tables == set()

    def test_carries_mesh_geometry_over_as_ready_metadata(
        self, upgraded: Upgraded
    ) -> None:
        rows = upgraded.rows(
            "SELECT kind, recipe_version, state FROM artifact_derivatives "
            "WHERE file_id = 1"
        )

        assert rows == [("metadata", 1, "ready")]

    def test_leaves_a_mesh_without_geometry_pending(self, upgraded: Upgraded) -> None:
        rows = upgraded.rows("SELECT kind FROM artifact_derivatives WHERE file_id = 2")

        assert rows == []

    def test_carries_gcode_metadata_over(self, upgraded: Upgraded) -> None:
        rows = upgraded.rows(
            "SELECT kind FROM artifact_derivatives "
            "WHERE file_id = 3 AND kind = 'metadata'"
        )

        assert rows == [("metadata",)]

    @pytest.mark.parametrize(
        ("file_id", "key"),
        [
            pytest.param(3, "thumbs/3.webp", id="embedded-gcode-thumbnail"),
            pytest.param(5, "thumbs/5.webp", id="rendered-generation"),
        ],
    )
    def test_carries_a_published_thumbnail_over_owning_its_object(
        self, upgraded: Upgraded, file_id: int, key: str
    ) -> None:
        rows = upgraded.rows(
            "SELECT state, storage_key FROM artifact_derivatives "
            "WHERE file_id = :id AND kind = 'thumbnail'",
            id=file_id,
        )

        assert rows == [("ready", key)]

    def test_carries_nothing_over_for_the_external_job_sentinel(
        self, upgraded: Upgraded
    ) -> None:
        rows = upgraded.rows("SELECT kind FROM artifact_derivatives WHERE file_id = 4")

        assert rows == []


class TestDowngrade:
    def test_restores_the_background_jobs_table(self, upgraded: Upgraded) -> None:
        command.downgrade(upgraded.config, BEFORE)

        tables = set(inspect(upgraded.engine).get_table_names())
        assert "background_jobs" in tables

    def test_restores_the_background_job_reference(self, upgraded: Upgraded) -> None:
        command.downgrade(upgraded.config, BEFORE)

        rows = upgraded.rows("SELECT background_job_id FROM inbox_items WHERE id = 1")
        assert rows == [("completed-job",)]
