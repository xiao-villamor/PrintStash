"""The convergence chain makes an upgraded database identical to a fresh one.

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

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    Table,
    create_engine,
    text,
)

from alembic import command
from app.db import migrate as migrate_mod

PREVIOUS = "eb8435c9400e"
REVISION = "5c777075c95b"

# Tables the migration rebuilds that hold rows a user would notice losing.
SEEDED_TABLES = ("collections", "files", "models", "printers", "tags")


def _placeholder(column) -> object:
    if isinstance(column.type, Integer):
        return 0
    if isinstance(column.type, Boolean):
        return False
    if isinstance(column.type, (DateTime, Date)):
        return datetime(2026, 1, 1)
    if isinstance(column.type, (Float, Numeric)):
        return 0.0
    return column.name


def _seed(conn, table: str, **values: object) -> None:
    """Insert one row into *table* at whatever shape this revision has it.

    Reflected rather than written out: a literal `INSERT` naming columns is a guess
    about a historical schema, and this file exists precisely because those two things
    disagree.
    """
    table_obj = Table(table, MetaData(), autoload_with=conn)
    row: dict[str, object] = {}
    for column in table_obj.columns:
        if column.name in values:
            row[column.name] = values[column.name]
        elif column.nullable or column.default is not None or column.server_default:
            continue
        elif column.primary_key and isinstance(column.type, Integer):
            continue
        else:
            row[column.name] = _placeholder(column)
    conn.execute(table_obj.insert().values(**row))


@pytest.fixture
def seeded_chain(tmp_path: Path) -> str:
    """A database one revision short of convergence, with rows in the rebuilt tables.

    `files.file_type` and `files.revision_status` carry real enum members rather than
    placeholders, because the converged column is a `VARCHAR` with a `CHECK` and the
    rebuild has to carry the existing value through it.
    """
    url = f"sqlite:///{tmp_path / 'converge.sqlite'}"
    command.upgrade(migrate_mod._alembic_config(url), PREVIOUS)  # noqa: SLF001

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            _seed(conn, "users", id=1, username="owner")
            _seed(conn, "collections", id=1, name="Brackets", slug="brackets")
            _seed(conn, "tags", id=1, name="fun", slug="fun")
            _seed(
                conn,
                "models",
                id=1,
                name="Widget",
                slug="widget",
                hash="h",
                collection_id=1,
            )
            _seed(
                conn,
                "files",
                id=1,
                model_id=1,
                path="/v/w.stl",
                file_type="STL",
                revision_status="KNOWN_GOOD",
            )
            _seed(conn, "printers", id=1, name="Ender", provider="MOONRAKER")
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
        self, seeded_chain: str
    ) -> None:
        command.upgrade(migrate_mod._alembic_config(seeded_chain), REVISION)  # noqa: SLF001

        engine = create_engine(seeded_chain)
        try:
            issues = migrate_mod._orphan_schema_issues(engine)  # noqa: SLF001
        finally:
            engine.dispose()

        assert issues == [], (
            "an upgraded database still differs from the models, so the two supported "
            f"installations are not the same product: {issues}"
        )

    @pytest.mark.parametrize("table", SEEDED_TABLES)
    def test_the_rebuild_keeps_every_row(self, seeded_chain: str, table: str) -> None:
        before = _count(seeded_chain, table)

        command.upgrade(migrate_mod._alembic_config(seeded_chain), REVISION)  # noqa: SLF001

        assert _count(seeded_chain, table) == before

    def test_an_existing_enum_value_survives_the_converged_check_constraint(
        self, seeded_chain: str
    ) -> None:
        # The models declare `file_type` as an enum; the chain stored it as plain text.
        # A converged enum column on SQLite is a VARCHAR with a CHECK, so the rebuild
        # re-inserts every existing value through that constraint.
        command.upgrade(migrate_mod._alembic_config(seeded_chain), REVISION)  # noqa: SLF001

        engine = create_engine(seeded_chain)
        try:
            with engine.connect() as conn:
                stored = conn.execute(
                    text("SELECT file_type, revision_status FROM files WHERE id = 1")
                ).one()
        finally:
            engine.dispose()
        assert stored == ("STL", "KNOWN_GOOD")

    def test_the_orphan_rescue_path_can_now_adopt_an_upgraded_database(
        self, seeded_chain: str
    ) -> None:
        """The consequence worth having: `run_migrations` can adopt this schema.

        Its orphan branch stamps head only when an unversioned database matches the
        models exactly. Before convergence that was true of a `create_all` database and
        of no upgraded one, which made the rescue path useless for the installations
        most likely to need it.
        """
        command.upgrade(migrate_mod._alembic_config(seeded_chain), REVISION)  # noqa: SLF001
        engine = create_engine(seeded_chain)
        try:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE alembic_version"))
        finally:
            engine.dispose()

        migrate_mod.run_migrations(seeded_chain)

        engine = create_engine(seeded_chain)
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
        self, seeded_chain: str, table: str
    ) -> None:
        cfg = migrate_mod._alembic_config(seeded_chain)  # noqa: SLF001
        command.upgrade(cfg, REVISION)
        before = _count(seeded_chain, table)

        command.downgrade(cfg, PREVIOUS)

        assert _count(seeded_chain, table) == before


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
