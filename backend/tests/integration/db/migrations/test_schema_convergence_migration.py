"""A released v0.12.1 database upgrades to the same schema as a fresh install.

Two paths reach `head` and they had drifted 136 differences apart: `create_all` from
the models for a fresh installation, the chain for an upgraded one. `eb8435c9400e`
closed the eighteen foreign keys; this one closes everything else — column types,
server defaults, indexes and unique constraints, 212 operations over 31 tables.

What has to hold:

* **Zero differences afterwards**, by the app's own comparator. That is the whole
  point, and it is what makes `run_migrations`' orphan-rescue path usable on a
  database that was upgraded rather than freshly created.
* **Rows survive.** Converging a column type or adding a unique constraint rebuilds
  the table on SQLite, and a rebuild that drops rows is data loss wearing a schema
  change.
* **Existing values still satisfy the converged types.** The models declare enums
  where the chain stored plain text, and an enum on SQLite is a `VARCHAR` with a
  `CHECK`. A row holding a value outside the enum would fail the rebuild, so this
  asserts real stored values survive it.
* **The generated file names no ORM internals.** Autogenerate reaches for
  `sqlmodel.sql.sqltypes.AutoString` and does not import it, so the first attempt at
  this migration died on `NameError: name 'sqlmodel' is not defined`. `alembic/env.py`
  now renders those as `sa.String`, which is what they are.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from alembic import command
from app.db import migrate as migrate_mod
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    seed_released_v0121_rows,
)

PREVIOUS = RELEASED_V0121_REVISION
REVISION = "head"

# Tables the migration rebuilds that hold rows a user would notice losing.
SEEDED_TABLES = (
    "collections",
    "files",
    "metadata",
    "models",
    "owned_storage_objects",
    "storage_delete_intents",
    "tags",
)


@pytest.fixture
def released_sqlite(tmp_path: Path) -> str:
    """A real released-chain v0.12.1 SQLite database with representative rows."""
    url = f"sqlite:///{tmp_path / 'released-v0.12.1.sqlite'}"
    command.upgrade(migrate_mod._alembic_config(url), PREVIOUS)  # noqa: SLF001

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            seed_released_v0121_rows(conn)
    finally:
        engine.dispose()
    return url


def _count(url: str, table: str) -> int:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608
    finally:
        engine.dispose()


class TestUpgrade:
    def test_leaves_no_structural_difference_from_the_models(
        self, released_sqlite: str
    ) -> None:
        command.upgrade(migrate_mod._alembic_config(released_sqlite), REVISION)  # noqa: SLF001

        engine = create_engine(released_sqlite)
        try:
            issues = migrate_mod._orphan_schema_issues(engine)  # noqa: SLF001
        finally:
            engine.dispose()

        assert issues == [], (
            "an upgraded database still differs from the models, so the two supported "
            f"installations are not the same product: {issues}"
        )

    @pytest.mark.parametrize("table", SEEDED_TABLES)
    def test_the_rebuild_keeps_every_row(
        self, released_sqlite: str, table: str
    ) -> None:
        before = _count(released_sqlite, table)

        command.upgrade(migrate_mod._alembic_config(released_sqlite), REVISION)  # noqa: SLF001

        assert _count(released_sqlite, table) == before

    def test_an_existing_enum_value_survives_the_converged_check_constraint(
        self, released_sqlite: str
    ) -> None:
        # The models declare `file_type` as an enum; the chain stored it as plain text.
        # A converged enum column on SQLite is a VARCHAR with a CHECK, so the rebuild
        # re-inserts every existing value through that constraint.
        command.upgrade(migrate_mod._alembic_config(released_sqlite), REVISION)  # noqa: SLF001

        engine = create_engine(released_sqlite)
        try:
            with engine.connect() as conn:
                stored = conn.execute(
                    text("SELECT file_type, revision_status FROM files WHERE id = 2")
                ).one()
        finally:
            engine.dispose()
        assert stored == ("GCODE", "KNOWN_GOOD")

    def test_released_content_metadata_survives(self, released_sqlite: str) -> None:
        command.upgrade(migrate_mod._alembic_config(released_sqlite), REVISION)  # noqa: SLF001

        engine = create_engine(released_sqlite)
        try:
            with engine.connect() as conn:
                stored = conn.execute(
                    text(
                        "SELECT slicer_name, slicer_version, layer_height_mm, "
                        "estimated_time_s, filament_weight_g FROM metadata WHERE id = 1"
                    )
                ).one()
        finally:
            engine.dispose()

        assert stored == ("PrusaSlicer", "2.8.1", 0.2, 3600, 12.5)

    def test_records_the_current_head(self, released_sqlite: str) -> None:
        command.upgrade(migrate_mod._alembic_config(released_sqlite), REVISION)  # noqa: SLF001

        engine = create_engine(released_sqlite)
        try:
            with engine.connect() as conn:
                revision = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
        finally:
            engine.dispose()

        assert (
            revision
            == ScriptDirectory.from_config(
                migrate_mod._alembic_config(released_sqlite)  # noqa: SLF001
            ).get_current_head()
        )

    def test_the_orphan_rescue_path_can_now_adopt_an_upgraded_database(
        self, released_sqlite: str
    ) -> None:
        """The consequence worth having: `run_migrations` can adopt this schema.

        Its orphan branch stamps head only when an unversioned database matches the
        models exactly. Before convergence that was true of a `create_all` database and
        of no upgraded one, which made the rescue path useless for the installations
        most likely to need it.
        """
        command.upgrade(migrate_mod._alembic_config(released_sqlite), REVISION)  # noqa: SLF001
        engine = create_engine(released_sqlite)
        try:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE alembic_version"))
        finally:
            engine.dispose()

        migrate_mod.run_migrations(released_sqlite)

        engine = create_engine(released_sqlite)
        try:
            with engine.connect() as conn:
                stamped = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
        finally:
            engine.dispose()
        assert stamped is not None


class TestDowngrade:
    @pytest.mark.parametrize("table", SEEDED_TABLES)
    def test_the_downgrade_rebuild_keeps_every_row(
        self, released_sqlite: str, table: str
    ) -> None:
        cfg = migrate_mod._alembic_config(released_sqlite)  # noqa: SLF001
        command.upgrade(cfg, REVISION)
        before = _count(released_sqlite, table)

        command.downgrade(cfg, PREVIOUS)

        assert _count(released_sqlite, table) == before


class TestGeneratedMigrations:
    def test_no_migration_references_an_orm_internal_type(self) -> None:
        """Autogenerate must not put `sqlmodel.…` into a migration file.

        It reaches for the type object on the model, which for a `str` field is
        `sqlmodel.sql.sqltypes.AutoString`, and does not import it — so the script
        fails at `NameError`. `alembic/env.py`'s `render_item` renders those as
        `sa.String`, which is what they are, and keeps a historical record free of the
        ORM layer's internals.
        """
        versions = Path(__file__).resolve().parents[4] / "alembic" / "versions"

        offenders = sorted(
            path.name
            for path in versions.glob("*.py")
            if "sqlmodel.sql.sqltypes" in path.read_text(encoding="utf-8")
            and "import sqlmodel" not in path.read_text(encoding="utf-8")
        )

        assert not offenders, (
            "these migrations name a sqlmodel type without importing it, so they raise "
            f"NameError when run: {offenders}"
        )
