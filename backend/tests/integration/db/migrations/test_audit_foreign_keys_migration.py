"""`eb8435c9400e` gives an upgraded SQLite database the foreign keys it never had.

Eighteen constraints the models have always declared and which no SQLite installation
that upgraded through the chain has, because `69b6a6d8a1d1` guarded
`op.create_foreign_key` with `if not is_sqlite:` — SQLite has no
`ALTER TABLE … ADD CONSTRAINT`.

Three things have to hold, and only the first is about the constraints:

* They exist afterwards, and the two supported schemas agree.
* **Rows survive.** The only way to add a constraint on SQLite is to rebuild the
  table, and a rebuild that loses rows is a data-loss bug wearing a schema change.
* **A database with orphan references still upgrades.** Adding a constraint to rows
  that already violate it leaves a database that cannot be written to; the migration
  nulls those references first, and every column it touches is nullable precisely so
  that repair costs nothing.

The downgrade is here for the same reason the upgrade is: it also rebuilds, so it can
also lose rows.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
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
    event,
    inspect,
    text,
)

from alembic import command
from app.db import migrate as migrate_mod

PREVIOUS = "a7c9e1b5d3f2"
REVISION = "eb8435c9400e"

# One per table the migration rebuilds, so every rebuild is covered by a row that has
# to come out the other side.
SEEDED_TABLES = ("collections", "files", "models", "print_jobs", "printers", "tags")


def _seed(conn, table: str, **values: object) -> None:
    """Insert one row into *table*, at whatever shape this revision has it.

    Reflected rather than written out. A literal `INSERT` naming columns would be a
    guess about a historical schema — `collections` had no `updated_at` at this
    revision — and it would break again the next time an older migration changed.
    This supplies a value for every column the table actually requires and lets
    *values* override the ones the test cares about.
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


def _placeholder(column) -> object:
    """A value this column will accept, chosen by type rather than by name."""
    if isinstance(column.type, Integer):
        return 0
    if isinstance(column.type, Boolean):
        return False
    if isinstance(column.type, (DateTime, Date)):
        return datetime(2026, 1, 1)
    if isinstance(column.type, (Float, Numeric)):
        return 0.0
    return column.name


@pytest.fixture
def seeded_chain(tmp_path: Path) -> str:
    """A database at the revision before this one, holding a row in every rebuilt table.

    The rows reference each other the way real ones do — a file belongs to a model, a
    job to a printer and a file — so a rebuild that dropped or reordered anything
    shows up as a missing row rather than as a passing test.
    """
    url = f"sqlite:///{tmp_path / 'audit-fks.sqlite'}"
    command.upgrade(migrate_mod._alembic_config(url), PREVIOUS)  # noqa: SLF001

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            _seed(conn, "users", id=1, username="owner")
            _seed(
                conn,
                "collections",
                id=1,
                name="Brackets",
                slug="brackets",
                created_by=1,
            )
            _seed(conn, "tags", id=1, name="fun", slug="fun")
            _seed(
                conn,
                "models",
                id=1,
                name="Widget",
                slug="widget",
                hash="h",
                collection_id=1,
                created_by=1,
                updated_by=1,
            )
            _seed(conn, "files", id=1, model_id=1, path="/v/w.stl")
            _seed(conn, "printers", id=1, name="Ender", provider="MOONRAKER")
            _seed(conn, "print_jobs", id=1, printer_id=1, file_id=1, model_id=1)
    finally:
        engine.dispose()
    return url


def _migration_module():
    """The revision loaded as a module, so its guards can be called directly.

    Alembic's own guard is only reachable through a migration run, and a run cannot be
    given an enforcing connection without also giving it a schema to rebuild.
    """
    path = (
        Path(__file__).resolve().parents[4]
        / "alembic"
        / "versions"
        / f"{REVISION}_add_missing_audit_foreign_keys.py"
    )
    spec = importlib.util.spec_from_file_location(f"migration_{REVISION}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _foreign_keys(url: str, table: str) -> set[tuple[str, ...]]:
    engine = create_engine(url)
    try:
        return {
            tuple(key["constrained_columns"])
            for key in inspect(engine).get_foreign_keys(table)
        }
    finally:
        engine.dispose()


def _count(url: str, table: str) -> int:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608
    finally:
        engine.dispose()


class TestUpgrade:
    @pytest.mark.parametrize(
        ("table", "column"),
        [
            ("collections", "created_by"),
            ("collections", "deleted_by"),
            ("collections", "updated_by"),
            ("files", "deleted_by"),
            ("files", "external_library_id"),
            ("models", "created_by"),
            ("models", "deleted_by"),
            ("models", "updated_by"),
            ("print_jobs", "created_by"),
            ("print_jobs", "deleted_by"),
            ("print_jobs", "updated_by"),
            ("printers", "created_by"),
            ("printers", "deleted_by"),
            ("printers", "updated_by"),
            ("tags", "created_by"),
            ("tags", "deleted_by"),
            ("tags", "updated_by"),
            ("users", "deleted_by"),
        ],
        ids=lambda value: value,
    )
    def test_creates_the_foreign_key(
        self, seeded_chain: str, table: str, column: str
    ) -> None:
        assert (column,) not in _foreign_keys(seeded_chain, table)

        command.upgrade(migrate_mod._alembic_config(seeded_chain), REVISION)  # noqa: SLF001

        assert (column,) in _foreign_keys(seeded_chain, table)

    @pytest.mark.parametrize("table", SEEDED_TABLES)
    def test_the_rebuild_keeps_every_row(self, seeded_chain: str, table: str) -> None:
        before = _count(seeded_chain, table)

        command.upgrade(migrate_mod._alembic_config(seeded_chain), REVISION)  # noqa: SLF001

        assert _count(seeded_chain, table) == before

    def test_an_orphaned_audit_reference_is_detached_rather_than_fatal(
        self, seeded_chain: str
    ) -> None:
        # The state a real installation can be in: `created_by` pointing at a user id
        # that is not there. Adding the constraint on top of it would leave a row that
        # cannot be updated, so the migration nulls it — the column is nullable and an
        # audit pointer to nobody carries nothing worth keeping.
        engine = create_engine(seeded_chain)
        try:
            with engine.begin() as conn:
                conn.execute(text("UPDATE models SET created_by = 4242 WHERE id = 1"))
        finally:
            engine.dispose()

        command.upgrade(migrate_mod._alembic_config(seeded_chain), REVISION)  # noqa: SLF001

        engine = create_engine(seeded_chain)
        try:
            with engine.connect() as conn:
                created_by = conn.execute(
                    text("SELECT created_by FROM models WHERE id = 1")
                ).scalar_one()
                surviving = conn.execute(
                    text("SELECT COUNT(*) FROM models")
                ).scalar_one()
        finally:
            engine.dispose()
        assert created_by is None
        assert surviving == 1

    def test_refuses_to_rebuild_while_foreign_keys_are_enforced(
        self, seeded_chain: str
    ) -> None:
        # The rebuild drops each table, which a live constraint from another table
        # refuses with `IntegrityError: FOREIGN KEY constraint failed` — from inside a
        # half-finished rebuild, where it is least useful. Alembic's engine leaves
        # enforcement off, and the migration asserts that rather than trusting it, so a
        # future change that hands Alembic a pragma-configured engine fails with a
        # sentence instead.
        module = _migration_module()
        engine = create_engine(seeded_chain)
        event.listen(
            engine, "connect", lambda conn, _r: conn.execute("PRAGMA foreign_keys=ON")
        )
        try:
            with engine.begin() as conn:
                context = MigrationContext.configure(conn)
                with Operations.context(context):
                    with pytest.raises(RuntimeError, match="foreign keys are enforced"):
                        module._assert_safe_to_rebuild()  # noqa: SLF001
        finally:
            engine.dispose()


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

    def test_the_downgrade_removes_the_foreign_keys_it_added(
        self, seeded_chain: str
    ) -> None:
        cfg = migrate_mod._alembic_config(seeded_chain)  # noqa: SLF001
        command.upgrade(cfg, REVISION)

        command.downgrade(cfg, PREVIOUS)

        assert ("created_by",) not in _foreign_keys(seeded_chain, "models")
