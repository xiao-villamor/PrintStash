"""Upgrading a self-hoster's real database, which nobody can roll back.

A migration runs once, on data somebody cares about, on a machine we cannot see.
There is no undo — hard rule 1 exists because editing a merged migration breaks
the upgrade path for everyone still on an older release. So these tests run the
*real* runner against real data rather than asserting on the SQL.

Two classes of failure, both of which have bitten this project:

**Data-destroying migrations.** A migration that adds a unique constraint has to
decide what to do with rows that already violate it. Absorbing a duplicate pair
is correct; deleting one is somebody's print history gone. The Bambu identity
migration is asserted to group *without deleting*, and to refuse to group a fast
reprint or a transitive chain — over-grouping merges two genuinely different jobs
into one.

**Runner states nobody plans for.** A fresh database, one already at head, one
that predates Alembic entirely, one stamped at a revision that no longer exists.
The orphan-adoption case is the one that produced real "table already exists"
failures on upgrade: a database with tables but no version row has to be adopted,
not migrated from zero.

`SET NULL` migrations get their own rows because the alternative is a cascade:
dropping a job must not take its completed import history with it.
"""

from __future__ import annotations

import contextlib
import io
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

import app.db.models  # noqa: F401 — register all tables on SQLModel.metadata
from alembic import command
from app.db import migrate as migrate_mod
from app.db.session import _is_alembic_managed, init_db
from tests.factories import build_user
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


def _seeded_duplicate_defaults(tmp_path: Path) -> str:
    """A database at the revision before the fix, holding two default printers.

    `c7e4a1b9d2f6` is the last revision that allowed it, so seeding there is what
    reproduces the state real installations reached before the constraint existed.
    """
    url = _url(tmp_path, "duplicate-default-printers.sqlite")
    command.upgrade(migrate_mod._alembic_config(url), "c7e4a1b9d2f6")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for name in ("First", "Second"):
                connection.execute(
                    text(
                        """
                        INSERT INTO printers (
                            name, provider, moonraker_url, is_default,
                            drain_mode, status, created_at, updated_at
                        ) VALUES (
                            :name, 'MOONRAKER', '', 1, 0, 'UNKNOWN',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {"name": name},
                )
    finally:
        engine.dispose()
    return url


class TestDataMigrations:
    """The migrations that rewrite rows rather than schema, on real prior data."""

    def test_bambu_identity_migration_absorbs_duplicate_pair_without_delete(
        self,
        tmp_path: Path,
    ) -> None:
        """A project-only/task-only pair keeps the early row and rich evidence."""

        db_path = tmp_path / "bambu-repair.sqlite"
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "e7b4c1d9a6f2")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.begin() as connection:
                for job_id, created_at, project_id, task_id, evidence in (
                    (1, "2026-08-24 00:00:00", "project-1", None, "metadata_only"),
                    (2, "2026-08-24 00:00:10", None, "task-1", "gcode_archived"),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO print_jobs "
                            "(id, printer_id, file_id, model_id, remote_filename, state, "
                            "progress, source, external_project_id, external_task_id, "
                            "artifact_evidence, started_at, created_at, updated_at) "
                            "VALUES (:id, 1, 1, 1, 'plate.gcode', 'PRINTING', 0.5, "
                            "'external', :project_id, :task_id, :evidence, "
                            "'2026-08-24 00:00:00', :created_at, :created_at)"
                        ),
                        {
                            "id": job_id,
                            "project_id": project_id,
                            "task_id": task_id,
                            "evidence": evidence,
                            "created_at": created_at,
                        },
                    )
            command.upgrade(cfg, "head")
            with engine.connect() as connection:
                rows = (
                    connection.execute(
                        text(
                            "SELECT id, dedupe_absorbed_at, dedupe_survivor_id, "
                            "external_project_id, external_task_id, artifact_evidence "
                            "FROM print_jobs ORDER BY id"
                        )
                    )
                    .mappings()
                    .all()
                )
            assert rows[0]["dedupe_absorbed_at"] is None
            assert rows[0]["external_project_id"] == "project-1"
            assert rows[0]["external_task_id"] == "task-1"
            assert rows[0]["artifact_evidence"] == "gcode_archived"
            assert rows[1]["dedupe_absorbed_at"] is not None
            assert rows[1]["dedupe_survivor_id"] == 1
            with pytest.raises(
                RuntimeError, match="cannot downgrade Bambu identity repair"
            ):
                command.downgrade(cfg, "e7b4c1d9a6f2")
            assert {
                column["name"] for column in inspect(engine).get_columns("print_jobs")
            } >= {"dedupe_absorbed_at", "dedupe_survivor_id"}
        finally:
            engine.dispose()

    def test_bambu_identity_grouping_only_groups_one_real_job(
        self,
    ) -> None:
        migration_path = (
            ALEMBIC_DIR / "versions" / "f8a6c2d9e1b4_bambu_job_identity_repair.py"
        )
        spec = spec_from_file_location("bambu_identity_repair", migration_path)
        assert spec is not None and spec.loader is not None
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)

        def row(
            job_id: int,
            *,
            project: str | None = None,
            task: str | None = None,
            started: str = "2026-08-24 00:00:00",
            finished: str | None = None,
            created: str = "2026-08-24 00:00:00",
        ) -> dict[str, object]:
            return {
                "id": job_id,
                "printer_id": 1,
                "remote_filename": "plate.gcode",
                "external_project_id": project,
                "external_task_id": task,
                "external_subtask_id": None,
                "provider_job_id": None,
                "started_at": started,
                "finished_at": finished,
                "created_at": created,
            }

        # A completed print followed by a fast reprint has disjoint lifecycles,
        # despite sharing a filename and being only seconds apart.
        fast_reprints = [
            row(1, project="project-old", finished="2026-08-24 00:00:08"),
            row(
                2,
                task="task-new",
                started="2026-08-24 00:00:10",
                created="2026-08-24 00:00:10",
            ),
        ]
        assert migration._groups(fast_reprints) == []

        # A(project), B(project+task), C(task) must not collapse transitively.
        chain = [
            row(1, project="project-chain"),
            row(2, project="project-chain", task="task-chain"),
            row(3, task="task-chain"),
        ]
        groups = migration._groups(chain)
        assert [[entry["id"] for entry in group] for group in groups] == [[1, 2]]

        # A reused project id in a later, completed lifecycle is a reprint, not a
        # duplicate callback. Shared identity must be paired with time evidence.
        separated_reprints = [
            row(
                10,
                project="project-reused",
                started="2026-08-24 01:00:00",
                finished="2026-08-24 01:05:00",
            ),
            row(
                11,
                project="project-reused",
                started="2026-08-24 02:00:00",
                finished="2026-08-24 02:05:00",
                created="2026-08-24 02:00:00",
            ),
        ]
        assert migration._groups(separated_reprints) == []

        # The same token on separate printers is not evidence of one print.
        multi_printer = [
            row(20, project="shared-project", started="2026-08-24 03:00:00"),
            {
                **row(21, project="shared-project", started="2026-08-24 03:00:00"),
                "printer_id": 2,
            },
        ]
        assert migration._groups(multi_printer) == []

    def test_inbox_job_set_null_migration_preserves_completed_history(
        self,
        tmp_path: Path,
    ) -> None:
        """Upgrade changes only the FK action and keeps terminal inbox rows."""
        url = f"sqlite:///{tmp_path / 'inbox-job-fk.sqlite'}"
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "f9a7c3e5b1d2")

        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(username, hashed_password, is_superuser, is_active, "
                        "created_at, updated_at) VALUES "
                        "('history-owner', 'hash', 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
                owner_id = connection.execute(
                    text("SELECT id FROM users WHERE username = 'history-owner'")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO background_jobs "
                        "(id, visible, kind, state, status_json, replay_safe, "
                        "created_at, updated_at, finished_at) VALUES "
                        "('history-job', 1, 'pending_import', 'completed', '{}', 1, "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO inbox_items "
                        "(owner_user_id, source_kind, state, manifest_json, "
                        "requested_tags_json, background_job_id, retryable, "
                        "attempt_count, created_at, updated_at) VALUES "
                        "(:owner_id, 'BROWSER', 'COMPLETED', '{}', '[]', "
                        "'history-job', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"owner_id": owner_id},
                )
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")
        engine = create_engine(url)
        try:
            inbox_fks = {
                fk["constrained_columns"][0]: (fk.get("options") or {}).get("ondelete")
                for fk in inspect(engine).get_foreign_keys("inbox_items")
            }
            assert inbox_fks["background_job_id"] == "SET NULL"
            with engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.execute(
                    text("DELETE FROM background_jobs WHERE id = 'history-job'")
                )
                assert connection.execute(
                    text(
                        "SELECT state, background_job_id FROM inbox_items "
                        "WHERE owner_user_id = :owner_id"
                    ),
                    {"owner_id": owner_id},
                ).one() == ("COMPLETED", None)
        finally:
            engine.dispose()

    def test_revision_uniqueness_migration_repairs_existing_duplicates(
        self,
        tmp_path: Path,
    ) -> None:
        url = _url(tmp_path, "duplicate-revisions.sqlite")
        cfg = migrate_mod._alembic_config(url)
        command.upgrade(cfg, "a2f7c9d4e6b1")

        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO models (name, slug, hash, created_at, updated_at)
                        VALUES ('Duplicate', 'duplicate', :hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """
                    ),
                    {"hash": "d" * 64},
                )
                model_id = connection.execute(
                    text("SELECT id FROM models WHERE slug = 'duplicate'")
                ).scalar_one()
                for index in range(2):
                    connection.execute(
                        text(
                            """
                            INSERT INTO files (
                                model_id, path, original_filename, file_type, version,
                                size_bytes, sha256, uploaded_at, is_recommended, is_external
                            ) VALUES (
                                :model_id, :path, :name, 'GCODE', 1,
                                1, :sha, CURRENT_TIMESTAMP, 1, 0
                            )
                            """
                        ),
                        {
                            "model_id": model_id,
                            "path": f"/tmp/duplicate-{index}.gcode",
                            "name": f"duplicate-{index}.gcode",
                            "sha": str(index) * 64,
                        },
                    )
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                versions = (
                    connection.execute(
                        text("SELECT version FROM files ORDER BY version")
                    )
                    .scalars()
                    .all()
                )
                recommended = connection.execute(
                    text("SELECT COUNT(*) FROM files WHERE is_recommended = 1")
                ).scalar_one()
                next_version = connection.execute(
                    text("SELECT next_file_version FROM models WHERE id = :id"),
                    {"id": model_id},
                ).scalar_one()
            assert versions == [1, 2]
            assert recommended == 1
            assert next_version == 3
        finally:
            engine.dispose()

    def test_default_printer_migration_leaves_one_default_behind(
        self, tmp_path: Path
    ) -> None:
        url = _seeded_duplicate_defaults(tmp_path)

        command.upgrade(migrate_mod._alembic_config(url), "head")

        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                defaults = (
                    connection.execute(
                        text("SELECT id FROM printers WHERE is_default = 1 ORDER BY id")
                    )
                    .scalars()
                    .all()
                )
            assert defaults == [1]
        finally:
            engine.dispose()

    def test_default_printer_migration_makes_a_second_default_impossible(
        self, tmp_path: Path
    ) -> None:
        # Repairing the existing rows is only half of it: without the constraint the
        # next write puts the database straight back into the state the migration
        # just cleaned up.
        url = _seeded_duplicate_defaults(tmp_path)
        command.upgrade(migrate_mod._alembic_config(url), "head")

        engine = create_engine(url)
        try:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text("UPDATE printers SET is_default = 1 WHERE id = 2")
                    )
        finally:
            engine.dispose()

    def test_backfill_populates_existing_jobs(self, tmp_path: Path) -> None:
        """A job completed before the migration gets its cost resolved from its
        metadata/profile, exactly like a freshly-completed job would today."""
        db_path = tmp_path / "precost.sqlite"
        cfg = _upgrade_to(db_path, "b2d8f6a1c94e")  # the revision before this one

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO models (id, name, slug, hash, created_at, updated_at)"
                    " VALUES (1, 'M', 'm', 'h', '2026-01-01', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO files (id, model_id, path, original_filename,"
                    " file_type, version, size_bytes, sha256, is_recommended,"
                    " is_external, uploaded_at)"
                    " VALUES (1, 1, '/f', 'f.gcode', 'gcode', 1, 1, 'sha',"
                    " 0, 0, '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO metadata (id, file_id, material_type, material_brand,"
                    " created_at)"
                    " VALUES (1, 1, 'PLA', 'Hatchbox', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO filament_profiles (id, name, material_type,"
                    " material_brand, cost_per_kg, created_at, updated_at)"
                    " VALUES (1, 'Hatchbox PLA', 'PLA', 'Hatchbox', 20.0,"
                    " '2026-01-01', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO print_jobs (id, file_id, model_id, remote_filename,"
                    " state, progress, source, filament_used_g, created_at, updated_at)"
                    " VALUES (1, 1, 1, 'f.gcode', 'completed', 1.0, 'vault', 100.0,"
                    " '2026-01-01', '2026-01-01')"
                )
            )
        engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT cost, filament_g_effective FROM print_jobs WHERE id = 1")
            ).one()
            assert row.filament_g_effective == 100.0
            # 100g @ 20/kg => 2.00.
            assert row.cost == 2.0
        engine.dispose()


# --------------------------------------------------------------------------- #
# Strict coverage for the migration runner (app/db/migrate.py) and create_all
# gating — the entrypoint hardening for issue #29. Runs the real migration chain
# against temp SQLite *files* in every DB state the entrypoint must survive.
# --------------------------------------------------------------------------- #
def _url(tmp_path: Path, name: str = "runner.sqlite") -> str:
    return f"sqlite:///{tmp_path / name}"


def _head_revision() -> str:
    cfg = migrate_mod._alembic_config("sqlite://")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None
    return head


def _current(url: str) -> str | None:
    engine = create_engine(url)
    try:
        return migrate_mod._current_revision(engine)
    finally:
        engine.dispose()


def _table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


class TestRunMigrations:
    """Bringing any database this release might meet up to head, exactly once."""

    def test_runner_brings_a_fresh_database_to_head(self, tmp_path: Path) -> None:
        url = _url(tmp_path)
        migrate_mod.run_migrations(url)

        assert _current(url) == _head_revision()
        assert {"users", "models", "files", "alembic_version"} <= _table_names(url)

    def test_runner_is_idempotent_noop_at_head(self, tmp_path: Path) -> None:
        url = _url(tmp_path)
        migrate_mod.run_migrations(url)
        head = _current(url)
        migrate_mod.run_migrations(url)  # must not raise, must not move off head
        assert _current(url) == head == _head_revision()

    def test_managed_db_only_upgrades(self, tmp_path, monkeypatch) -> None:
        url = _url(tmp_path, "dispatch.sqlite")
        migrate_mod.run_migrations(url)  # make it managed (real run)

        spy = _Spy()
        monkeypatch.setattr(
            migrate_mod.command, "upgrade", lambda *a, **k: spy.upgrade.append(a)
        )
        monkeypatch.setattr(
            migrate_mod.command, "stamp", lambda *a, **k: spy.stamp.append(a)
        )
        monkeypatch.setattr(
            migrate_mod, "_create_all", lambda u: spy.create_all.append(u)
        )

        migrate_mod.run_migrations(url)
        assert spy.upgrade and not spy.stamp and not spy.create_all

    def test_runner_orphan_db_is_adopted_without_table_exists_error(
        self, tmp_path: Path
    ) -> None:
        # The issue #29 state: full schema built by create_all(), no alembic_version.
        url = _url(tmp_path)
        engine = create_engine(url)
        SQLModel.metadata.create_all(engine)
        engine.dispose()

        assert _current(url) is None
        assert "users" in _table_names(url) and "alembic_version" not in _table_names(
            url
        )

        # A naive `upgrade head` would hit "table already exists"; the runner must
        # stamp first and finish cleanly at head.
        migrate_mod.run_migrations(url)
        assert _current(url) == _head_revision()

    def test_orphan_db_stamps_then_upgrades(self, tmp_path, monkeypatch) -> None:
        # Real orphan: tables but no alembic_version.
        url = _url(tmp_path, "dispatch.sqlite")
        engine = create_engine(url)
        SQLModel.metadata.create_all(engine)
        engine.dispose()

        spy = _Spy()
        # Re-point spies at the SAME url (already has tables).
        monkeypatch.setattr(
            migrate_mod.command, "upgrade", lambda *a, **k: spy.upgrade.append(a)
        )
        monkeypatch.setattr(
            migrate_mod.command, "stamp", lambda *a, **k: spy.stamp.append(a)
        )
        monkeypatch.setattr(
            migrate_mod, "_create_all", lambda u: spy.create_all.append(u)
        )

        migrate_mod.run_migrations(url)
        assert len(spy.stamp) == 1 and len(spy.upgrade) == 1  # adopt then upgrade
        assert spy.create_all == []  # never rebuilds an existing schema

    def test_runner_orphan_rescue_preserves_existing_data(self, tmp_path: Path) -> None:
        url = _url(tmp_path)
        engine = create_engine(url)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            build_user(session, "keepme", superuser=True)
        engine.dispose()

        migrate_mod.run_migrations(url)

        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                names = [r[0] for r in conn.execute(text("SELECT username FROM users"))]
        finally:
            engine.dispose()
        assert "keepme" in names  # rescue never dropped or rebuilt the data

    def test_runner_rejects_incomplete_orphan_schema_without_stamping(
        self,
        tmp_path: Path,
    ) -> None:
        url = _url(tmp_path, "incomplete-orphan.sqlite")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("CREATE TABLE models (id INTEGER PRIMARY KEY, name TEXT)")
                )
        finally:
            engine.dispose()

        with pytest.raises(migrate_mod.OrphanSchemaError, match="does not match"):
            migrate_mod.run_migrations(url)

        assert _current(url) is None
        assert "alembic_version" not in _table_names(url)

    @pytest.mark.parametrize(
        ("table_name", "old", "new"),
        [
            ("users", "username VARCHAR(128) NOT NULL", "username INTEGER NOT NULL"),
            (
                "users",
                "hashed_password VARCHAR(255) NOT NULL",
                "hashed_password VARCHAR(255)",
            ),
            (
                "users",
                "oidc_managed BOOLEAN DEFAULT '0'",
                "oidc_managed BOOLEAN DEFAULT '1'",
            ),
            (
                "printer_permissions",
                "FOREIGN KEY(user_id) REFERENCES users (id)",
                "FOREIGN KEY(user_id) REFERENCES collections (id)",
            ),
        ],
    )
    def test_runner_rejects_structurally_divergent_orphan_schema(
        self,
        tmp_path: Path,
        table_name: str,
        old: str,
        new: str,
    ) -> None:
        url = _url(tmp_path, f"divergent-{table_name}-{abs(hash(old))}.sqlite")
        engine = create_engine(url)
        SQLModel.metadata.create_all(engine)
        engine.dispose()
        _rewrite_sqlite_table_definition(url, table_name, old, new)

        with pytest.raises(migrate_mod.OrphanSchemaError, match="does not match"):
            migrate_mod.run_migrations(url)

        assert _current(url) is None

    def test_runner_rejects_orphan_with_wrong_partial_index_predicate(
        self,
        tmp_path: Path,
    ) -> None:
        url = _url(tmp_path, "divergent-partial-index.sqlite")
        engine = create_engine(url)
        SQLModel.metadata.create_all(engine)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP INDEX uq_printers_live_default")
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX uq_printers_live_default "
                    "ON printers (is_default) WHERE is_default = 0"
                )
        finally:
            engine.dispose()

        with pytest.raises(migrate_mod.OrphanSchemaError, match="does not match"):
            migrate_mod.run_migrations(url)

        assert _current(url) is None

    def test_sqlite_pragma_enforces_foreign_keys(self, tmp_path: Path) -> None:
        """The pragma is what makes the repair worth doing: without it SQLite happily
        writes a child row pointing at a parent that was never there."""
        import pytest
        from sqlalchemy import event
        from sqlalchemy.exc import IntegrityError

        from app.db.session import _set_sqlite_pragmas

        db_path = tmp_path / "vault.sqlite"
        _upgrade_to(db_path, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        event.listen(engine, "connect", _set_sqlite_pragmas)
        try:
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO api_keys (user_id, name, prefix, key_hash,"
                            " created_at) VALUES (999, 'k', 'p', 'h', '2026-01-01')"
                        )
                    )
        finally:
            engine.dispose()


def _rewrite_sqlite_table_definition(
    url: str,
    table_name: str,
    old: str,
    new: str,
) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            statement = connection.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = :name"
                ),
                {"name": table_name},
            ).scalar_one()
            assert old in statement
            connection.exec_driver_sql("PRAGMA writable_schema=ON")
            connection.execute(
                text(
                    "UPDATE sqlite_master SET sql = :sql "
                    "WHERE type = 'table' AND name = :name"
                ),
                {"sql": statement.replace(old, new), "name": table_name},
            )
            schema_version = connection.exec_driver_sql(
                "PRAGMA schema_version"
            ).scalar_one()
            connection.exec_driver_sql(f"PRAGMA schema_version={schema_version + 1}")
            connection.exec_driver_sql("PRAGMA writable_schema=OFF")
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# State dispatch: a fresh DB must NOT replay the historical migration chain
# (its baseline is SQLite-only and fails on Postgres) — it bootstraps via
# create_all + stamp instead. This is what makes Postgres work; asserted here
# without needing a Postgres service in CI.
# --------------------------------------------------------------------------- #
class _Spy:
    def __init__(self) -> None:
        self.upgrade: list = []
        self.stamp: list = []
        self.create_all: list = []

    def install(self, monkeypatch, tmp_path: Path) -> str:
        url = _url(tmp_path, "dispatch.sqlite")
        monkeypatch.setattr(
            migrate_mod.command, "upgrade", lambda *a, **k: self.upgrade.append(a)
        )
        monkeypatch.setattr(
            migrate_mod.command, "stamp", lambda *a, **k: self.stamp.append(a)
        )
        monkeypatch.setattr(
            migrate_mod, "_create_all", lambda u: self.create_all.append(u)
        )
        return url


# --------------------------------------------------------------------------- #
# Upgrade-from-an-old-release guards. A self-hoster on an older version runs
# `upgrade head` at container start; if the chain has branched (two heads) or a
# revision file was deleted/renamed (down_revision can't resolve), that crashes
# the api container and takes the whole stack down. These catch both in CI.
# --------------------------------------------------------------------------- #

# Last released migration before the 0.8.0 line (present in the 0.7.2 tree) — a
# realistic point an existing install is upgrading *from*.
_PRE_0_8_0 = "f7a5b3c9d2e1"


class TestRevisionGraph:
    """The migration history itself: one head, every revision resolvable."""

    def test_the_revision_graph_has_exactly_one_head(self) -> None:
        script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))

        assert len(script.get_heads()) == 1, (
            "multiple alembic heads — `upgrade head` is ambiguous"
        )

    def test_every_revision_in_the_graph_resolves(self) -> None:
        script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))

        # Walking every revision resolves each down_revision; a deleted or renamed
        # file raises here — i.e. the "Can't locate revision X" startup crash, in CI.
        assert len(list(script.walk_revisions())) > 1


# ---------------------------------------------------------------------------
# Orphan-row repair before foreign key enforcement (b2d8f6a1c94e)
# ---------------------------------------------------------------------------


def _upgrade_to(db_path: Path, revision: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, revision)
    return cfg


class TestForeignKeyRepair:
    """Clearing references a pre-foreign-key database was allowed to leave dangling."""

    def test_fk_repair_clears_dangling_nullable_reference(self, tmp_path: Path) -> None:
        """A document whose creator was purged keeps the document, loses the attribution.

        ``documents.created_by`` is one of the columns the migration chain actually
        constrains — the ORM declares more foreign keys than the shipped schema has.
        """
        db_path = tmp_path / "dirty.sqlite"
        cfg = _upgrade_to(db_path, "a1c7e4f9b23d")  # the revision before the repair

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO documents (name, kind, created_by, created_at,"
                    " updated_at) VALUES ('orphaned', 'markdown', 999,"
                    " '2026-01-01', '2026-01-01')"
                )
            )
        engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT name, created_by FROM documents WHERE name = 'orphaned'")
            ).one()
            assert row.name == "orphaned", "the document itself must survive"
            assert row.created_by is None
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        engine.dispose()

    def test_fk_repair_deletes_row_with_dangling_required_reference(
        self,
        tmp_path: Path,
    ) -> None:
        """A permission grant for a purged user cannot be loaded — drop it."""
        db_path = tmp_path / "dirty.sqlite"
        cfg = _upgrade_to(db_path, "a1c7e4f9b23d")

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO collections (name, slug, path, created_at)"
                    " VALUES ('c', 'c', 'c', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO collection_permissions (user_id, collection_id, role,"
                    " created_at, updated_at)"
                    " VALUES (999, 1, 'view', '2026-01-01', '2026-01-01')"
                )
            )
        engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM collection_permissions")
            ).scalar()
            assert count == 0
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        engine.dispose()

    def test_fk_repair_leaves_clean_database_untouched(self, tmp_path: Path) -> None:
        db_path = tmp_path / "clean.sqlite"
        cfg = _upgrade_to(db_path, "a1c7e4f9b23d")

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO models (name, slug, hash, created_at, updated_at)"
                    " VALUES ('kept', 'kept', 'h1', '2026-01-01', '2026-01-01')"
                )
            )
        engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM models")).scalar() == 1
        engine.dispose()


# ---------------------------------------------------------------------------
# print_jobs.cost backfill (175be54ef975)
# ---------------------------------------------------------------------------


class TestInitDb:
    def test_init_db_builds_schema_on_unmanaged_db(self, tmp_path: Path) -> None:
        url = _url(tmp_path, "init.sqlite")
        engine = create_engine(url)
        try:
            assert "users" not in set(inspect(engine).get_table_names())
            init_db(engine)
            assert "users" in set(inspect(engine).get_table_names())
            assert (
                _is_alembic_managed(engine) is False
            )  # create_all leaves it un-stamped
        finally:
            engine.dispose()

    def test_init_db_is_strict_noop_on_alembic_managed_db(self, tmp_path: Path) -> None:
        url = _url(tmp_path, "managed.sqlite")
        migrate_mod.run_migrations(url)

        engine = create_engine(url)
        try:
            assert _is_alembic_managed(engine) is True
            before = set(inspect(engine).get_table_names())
            init_db(engine)  # must NOT call create_all
            assert set(inspect(engine).get_table_names()) == before
            assert migrate_mod._current_revision(engine) == _head_revision()
        finally:
            engine.dispose()


class TestUpgrade:
    def test_alembic_upgrade_creates_expected_schema(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        db_path = tmp_path / "vault.sqlite"
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        assert "alembic_version" in tables
        assert "models" in tables
        assert "files" in tables
        assert "print_jobs" in tables
        assert "refresh_tokens" in tables
        assert "printer_profiles" in tables
        assert "share_links" in tables
        assert {
            "model_provenance_sources",
            "model_provenance_fields",
            "provenance_captures",
            "artifact_provenance_links",
            "inbox_item_results",
        } <= tables
        print_job_columns = {
            col["name"]: col for col in inspector.get_columns("print_jobs")
        }
        assert "artifact_capture_error_code" in print_job_columns
        assert "artifact_capture_error_message" in print_job_columns
        assert "dedupe_absorbed_at" in print_job_columns
        assert "dedupe_survivor_id" in print_job_columns

        files_columns = {col["name"]: col for col in inspector.get_columns("files")}
        assert "revision_label" in files_columns
        assert "revision_status" in files_columns
        assert "revision_notes" in files_columns
        assert "is_recommended" in files_columns
        assert files_columns["is_recommended"]["nullable"] is False
        # No server default, deliberately. This asserted `is not None` until the
        # convergence migration, and it was asserting the *chain's* shape: the models
        # declare a Python-side `default=False`, so no installation built by
        # `create_all` has ever had a server default here. The convergence dropped it
        # on the migrated schema too, which makes the two agree rather than making
        # either worse — an insert that omits the column already failed on a fresh
        # install.
        assert files_columns["is_recommended"]["default"] is None
        model_columns = {col["name"]: col for col in inspector.get_columns("models")}
        assert "next_file_version" in model_columns
        file_indexes = {
            index["name"]: index for index in inspector.get_indexes("files")
        }
        assert bool(file_indexes["uq_files_model_version"]["unique"]) is True
        assert bool(file_indexes["uq_files_live_recommended_gcode"]["unique"]) is True
        assert file_indexes["ix_files_model_deleted_type"]["column_names"] == [
            "model_id",
            "deleted_at",
            "file_type",
        ]
        model_indexes = {
            index["name"]: index for index in inspector.get_indexes("models")
        }
        assert model_indexes["ix_models_deleted_updated_id"]["column_names"] == [
            "deleted_at",
            "updated_at",
            "id",
        ]
        background_job_indexes = {
            index["name"]: index for index in inspector.get_indexes("background_jobs")
        }
        assert background_job_indexes["ix_background_jobs_visible_state_owner_updated"][
            "column_names"
        ] == ["visible", "state", "owner_user_id", "updated_at"]
        printer_indexes = {
            index["name"]: index for index in inspector.get_indexes("printers")
        }
        assert bool(printer_indexes["uq_printers_live_default"]["unique"]) is True

        share_columns = {
            col["name"]: col for col in inspector.get_columns("share_links")
        }
        assert "model_id" in share_columns
        assert "token_hash" in share_columns
        assert "expires_at" in share_columns
        assert "allow_download" in share_columns
        assert "selected_file_ids_json" in share_columns

        user_columns = {col["name"]: col for col in inspector.get_columns("users")}
        assert "oidc_issuer" in user_columns
        assert "oidc_subject" in user_columns
        assert user_columns["oidc_managed"]["nullable"] is False
        config_columns = {
            col["name"]: col for col in inspector.get_columns("system_config")
        }
        assert "oidc_enabled" in config_columns
        assert "oidc_client_secret" in config_columns
        assert "oidc_admin_groups" in config_columns

        inbox_columns = {
            col["name"]: col for col in inspector.get_columns("inbox_items")
        }
        assert "completion" in inbox_columns
        inbox_fks = {
            fk["constrained_columns"][0]: (fk.get("options") or {}).get("ondelete")
            for fk in inspector.get_foreign_keys("inbox_items")
        }
        assert inbox_fks["background_job_id"] == "SET NULL"
        provenance_source_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("model_provenance_sources")
        }
        assert provenance_source_indexes["ix_provenance_source_provider_item"][
            "column_names"
        ] == [
            "provider",
            "source_item_id",
        ]
        provenance_link_columns = {
            col["name"]: col
            for col in inspector.get_columns("artifact_provenance_links")
        }
        assert "import_key" in provenance_link_columns
        provenance_link_fks = {
            fk["constrained_columns"][0]: (fk.get("options") or {}).get("ondelete")
            for fk in inspector.get_foreign_keys("artifact_provenance_links")
        }
        assert provenance_link_fks == {
            "file_id": "CASCADE",
            "provenance_source_id": "CASCADE",
            "capture_id": "SET NULL",
        }

    def test_upgrade_from_pre_0_8_0_release_preserves_data(
        self, tmp_path: Path
    ) -> None:
        url = _url(tmp_path, "old.sqlite")
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", url)

        # Stand up an old (~0.7.2) schema and seed representative rows.
        command.upgrade(cfg, _PRE_0_8_0)
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO collections (name, slug, path, created_at) "
                    "VALUES ('Functional','functional','functional','2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO models (name, slug, hash, collection_id, created_at, updated_at) "
                    "VALUES ('Bracket','bracket',:h,1,'2026-01-01','2026-01-01')"
                ),
                {"h": "a" * 64},
            )
            conn.commit()
        engine.dispose()

        # The upgrade an existing user actually runs.
        command.upgrade(cfg, "head")

        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            assert "documents" in inspector.get_table_names()  # new 0.8.0 table
            assert "printer_permissions" in inspector.get_table_names()
            assert "readme" in {c["name"] for c in inspector.get_columns("collections")}
            printer_columns = {c["name"] for c in inspector.get_columns("printers")}
            assert {
                "provider_variant",
                "prusalink_url",
                "prusalink_auth_mode",
                "prusalink_username",
                "prusalink_password",
                "prusalink_api_key",
                "elegoo_centauri_host",
                "elegoo_centauri_access_code",
                "elegoo_centauri_mainboard_id",
            } <= printer_columns
            with engine.connect() as conn:
                # Existing data survived the ALTER TABLE / CREATE TABLE migrations.
                assert (
                    conn.execute(text("SELECT count(*) FROM collections")).scalar() == 1
                )
                assert conn.execute(text("SELECT count(*) FROM models")).scalar() == 1
        finally:
            engine.dispose()
        assert _current(url) == _head_revision()


class TestCreateAll:
    def test_fresh_db_bootstraps_via_create_all_not_baseline(
        self, tmp_path, monkeypatch
    ) -> None:
        spy = _Spy()
        url = spy.install(monkeypatch, tmp_path)  # empty DB → fresh
        migrate_mod.run_migrations(url)
        assert spy.create_all == [url]  # schema built from models
        assert len(spy.stamp) == 1  # stamped head
        assert spy.upgrade == []  # baseline chain NOT replayed (Postgres-safe)


class TestHasApplicationTables:
    def test_has_application_tables_ignores_alembic_only(self, tmp_path: Path) -> None:
        url = _url(tmp_path)
        engine = create_engine(url)
        try:
            assert migrate_mod._has_application_tables(engine) is False
        finally:
            engine.dispose()


# The revision the two convergence migrations build on. Offline rendering starts here
# rather than at `base`, because the baseline is SQLite-authored and documented not to
# apply to PostgreSQL.
_POST_BASELINE_REVISION = "a7c9e1b5d3f2"

# Never connected to: `--sql` mode renders DDL without a database.
_POSTGRES_URL = "postgresql+psycopg://user:pass@localhost/db"


class TestWholeChain:
    """The chain runs forwards and backwards, end to end.

    Seven migrations have a test that downgrades them one step; the other sixty do not,
    so a `downgrade()` that was never run could sit in the chain indefinitely. It would
    surface at the worst time — an operator rolling back a bad release, which is the one
    moment a downgrade is load-bearing.

    Cheap enough to be unconditional: the round trip is under two seconds on SQLite.
    """

    def test_the_chain_downgrades_all_the_way_to_base(self, tmp_path: Path) -> None:
        url = _url(tmp_path, "round-trip.sqlite")
        cfg = migrate_mod._alembic_config(url)  # noqa: SLF001
        command.upgrade(cfg, "head")

        command.downgrade(cfg, "base")

        assert _current(url) is None
        remaining = _table_names(url) - {"alembic_version"}
        assert not remaining, (
            "downgrading to base left tables behind, so at least one `downgrade()` "
            f"does not undo its `upgrade()`: {sorted(remaining)}"
        )

    def test_the_chain_upgrades_again_after_a_full_downgrade(
        self, tmp_path: Path
    ) -> None:
        # Forwards, backwards, forwards. A `downgrade()` that leaves a stray index or
        # constraint behind passes the test above and fails here, on the re-upgrade.
        url = _url(tmp_path, "re-upgrade.sqlite")
        cfg = migrate_mod._alembic_config(url)  # noqa: SLF001
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")

        command.upgrade(cfg, "head")

        assert _current(url) == _head_revision()


class TestOfflineRendering:
    """Every migration renders without a database, and on both dialects.

    `alembic upgrade --sql` is how an operator reviews DDL before letting it near their
    data, and how anyone applies a migration through a change-control process. A
    migration that reaches for the connection — to read a pragma, to count rows — dies
    there with `MockConnection has no attribute exec_driver_sql`, which is how both new
    migrations were written before this test existed.

    The PostgreSQL render matters for a second reason. A Postgres installation is built
    by `create_all` and stamped at head, because the chain's baseline cannot bootstrap
    Postgres — but it then runs every *later* migration incrementally, and nothing else
    in this suite exercises that. Rendering for the Postgres dialect is the cheap half of
    that check: it catches SQLite-only DDL without needing a live server.
    """

    def test_the_migrations_after_the_baseline_render_offline_for_postgres(
        self,
    ) -> None:
        buffer = io.StringIO()

        with contextlib.redirect_stdout(buffer):
            command.upgrade(
                migrate_mod._alembic_config(_POSTGRES_URL),  # noqa: SLF001
                f"{_POST_BASELINE_REVISION}:head",
                sql=True,
            )

        assert buffer.getvalue().strip()

    def test_offline_sqlite_is_unavailable_while_batch_mode_reflects(self) -> None:
        """A limitation, pinned so it is a known cost rather than a surprise.

        Batch mode rebuilds a table from what it reflects, and reflection needs a live
        connection — so `--sql` on SQLite refuses unless every `batch_alter_table` was
        given `copy_from`. That is 38 literal `Table` definitions across the two
        convergence migrations, each one a chance to omit a column and have the rebuild
        drop it silently, to buy offline rendering on the dialect where an operator is
        least likely to want it.

        Not paid, deliberately. The PostgreSQL render above is the one that matters:
        Postgres is where DDL review through a change-control process actually happens,
        and where migrations run as plain `ALTER TABLE` anyway.

        If a future migration does pass `copy_from` throughout, this test fails and says
        so — at which point the limitation is gone and this goes with it.
        """
        with pytest.raises(CommandError, match="cannot proceed in --sql mode"):
            with contextlib.redirect_stdout(io.StringIO()):
                command.upgrade(
                    migrate_mod._alembic_config("sqlite://"),  # noqa: SLF001
                    f"{_POST_BASELINE_REVISION}:head",
                    sql=True,
                )

    def test_the_postgres_render_uses_no_sqlite_table_rebuild(self) -> None:
        # `batch_alter_table` rebuilds only where the dialect cannot ALTER. On Postgres
        # it must emit plain `ALTER TABLE`; a `_alembic_tmp_` table in this output would
        # mean a migration hard-coded the SQLite path.
        buffer = io.StringIO()

        with contextlib.redirect_stdout(buffer):
            command.upgrade(
                migrate_mod._alembic_config(
                    "postgresql+psycopg://user:pass@localhost/db"
                ),  # noqa: SLF001
                f"{_POST_BASELINE_REVISION}:head",
                sql=True,
            )

        assert "_alembic_tmp" not in buffer.getvalue()
