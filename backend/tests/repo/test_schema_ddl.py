"""Two DDL invariants the models must hold before any migration is generated.

**Every declared foreign key is rendered on the dialect we ship by default.**
`app/db/models.py` has a foreign-key cycle — `files.model_id -> models.id` and
`models.thumbnail_file_id -> files.id` — and SQLAlchemy resolves it per dialect.
When `create_all` targets a dialect that can `ALTER TABLE ... ADD CONSTRAINT` it
lifts the cycle-breaking constraints out of `CREATE TABLE` and sets
`ForeignKeyConstraint._create_rule` so they are not rendered inline. That attribute
lives on the shared `MetaData`, so the decision is process-wide and permanent: a
later `create_all` against SQLite, which cannot ALTER, silently omits them.

The default installation is SQLite and its schema is built by `create_all`
(`app/db/migrate.run_migrations`, fresh path). A run that lost those constraints
would produce a database with no referential integrity between models and files —
and `foreign_keys=ON` is a production pragma, so the app relies on them.

This is what caught it, on a suite that had been green: see
`tests/integration/postgres/conftest.py` for the leak, the failure it produced two
runs in five, and the fixture that undoes it. This test is the tripwire, so a future
leak fails here with the reason attached rather than as an `OrphanSchemaError` in an
unrelated backup test.

**Every constraint carries a name, and the name says what kind it is.** A
migration drops a constraint *by name*, and an unnamed one is named by whichever
dialect created it — so the same `op.drop_constraint` succeeds on PostgreSQL and
fails on an upgraded SQLite installation, or vice versa. That is not a
theoretical hazard here: SQLite cannot `ALTER` a constraint at all, so the drop
runs inside `batch_alter_table`, which reflects the existing table and needs the
name to match what is actually in the file. `SQLModel.metadata.naming_convention`
makes the name deterministic for anything declared from now on; these tests are
what keep it that way, and what catch a model that predates it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.schema import ColumnCollectionConstraint, Constraint, CreateTable
from sqlmodel import SQLModel

import app.db.models  # noqa: F401 - registers every table on SQLModel.metadata

# The two tables in the cycle, and therefore the only two whose constraints
# SQLAlchemy ever lifts out. Naming them rather than sweeping every table keeps the
# failure message pointed at the actual mechanism.
CYCLE_TABLES = ("files", "models")

# The prefix each kind of constraint must carry, matching
# `SQLModel.metadata.naming_convention`.
CONSTRAINT_PREFIXES = {
    PrimaryKeyConstraint: "pk_",
    ForeignKeyConstraint: "fk_",
    UniqueConstraint: "uq_",
    CheckConstraint: "ck_",
}

# Indexes take either prefix. SQLAlchemy's convention has one `ix` key and applies
# it to every index, unique or not, so `Field(unique=True, index=True)` produces
# `ix_<table>_<column>`. The handful of hand-declared partial unique indexes — the
# only way to say "unique among live rows" on both dialects — are named `uq_` for
# what they enforce. Both are stable and droppable by name, which is what matters;
# what is banned is a name from neither scheme.
INDEX_PREFIXES = ("ix_", "uq_")

EXPECTED_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _prefix_for(constraint: Constraint) -> str | None:
    """The prefix this constraint's name must start with, or None if unconstrained."""
    for kind, prefix in CONSTRAINT_PREFIXES.items():
        if isinstance(constraint, kind):
            return prefix
    return None


def _columns_of(constraint: Constraint) -> str:
    """The constraint's columns, for a failure message. A `CheckConstraint` has none."""
    if not isinstance(constraint, ColumnCollectionConstraint):
        return ""
    return f"({', '.join(constraint.columns.keys())})"


@pytest.fixture(scope="module")
def sqlite_dialect():
    """A throwaway SQLite engine, used only to compile DDL — nothing is executed."""
    engine = create_engine("sqlite://")
    try:
        yield engine
    finally:
        engine.dispose()


class TestCreateTable:
    @pytest.mark.parametrize("table_name", CYCLE_TABLES)
    def test_renders_every_declared_foreign_key_inline_for_sqlite(
        self, sqlite_dialect, table_name: str
    ) -> None:
        table = SQLModel.metadata.tables[table_name]
        declared = len(table.foreign_key_constraints)

        ddl = str(CreateTable(table).compile(sqlite_dialect))

        assert ddl.count("FOREIGN KEY") == declared, (
            f"`{table_name}` declares {declared} foreign keys but its SQLite "
            f"CREATE TABLE renders {ddl.count('FOREIGN KEY')}. Something in this "
            "process ran `create_all` against an ALTER-capable dialect and left "
            "`_create_rule` set on the shared metadata — see this module's "
            "docstring and tests/integration/postgres/conftest.py."
        )

    @pytest.mark.parametrize("table_name", CYCLE_TABLES)
    def test_no_constraint_is_marked_for_alter_only_emission(
        self, table_name: str
    ) -> None:
        # The same invariant one level down, so the failure names the attribute
        # rather than a count. `_create_rule` is what suppresses inline rendering.
        table = SQLModel.metadata.tables[table_name]

        suppressed = [
            constraint.name or tuple(constraint.column_keys)
            for constraint in table.foreign_key_constraints
            if constraint._create_rule is not None  # noqa: SLF001
        ]

        assert not suppressed, (
            f"these `{table_name}` foreign keys are marked for ALTER-only emission "
            f"and will not appear in a SQLite CREATE TABLE: {suppressed}"
        )


class TestConstraintNaming:
    def test_metadata_carries_the_naming_convention(self) -> None:
        # Everything below is downstream of this. Drop the convention and the next
        # model declared gets dialect-generated names again, silently.
        assert dict(SQLModel.metadata.naming_convention) == EXPECTED_CONVENTION

    def test_every_constraint_has_a_name(self) -> None:
        unnamed = [
            f"{table.name}.{type(constraint).__name__}{_columns_of(constraint)}"
            for table in SQLModel.metadata.tables.values()
            for constraint in table.constraints
            if not constraint.name
        ]

        assert not unnamed, (
            "these constraints have no name, so no migration can drop them by one "
            f"and the dialect decides what they are called: {unnamed}"
        )

    def test_every_index_has_a_name(self) -> None:
        unnamed = [
            f"{table.name}({', '.join(column.name for column in index.columns)})"
            for table in SQLModel.metadata.tables.values()
            for index in table.indexes
            if not index.name
        ]

        assert not unnamed, f"these indexes have no name: {unnamed}"

    def test_every_constraint_name_says_what_kind_it_is(self) -> None:
        misnamed = [
            (table.name, type(constraint).__name__, constraint.name)
            for table in SQLModel.metadata.tables.values()
            for constraint in table.constraints
            if (prefix := _prefix_for(constraint))
            and not str(constraint.name).startswith(prefix)
        ]

        assert not misnamed, (
            "these constraint names do not carry the prefix their kind requires, so "
            f"a reader cannot tell from a migration what is being dropped: {misnamed}"
        )

    def test_every_index_name_carries_a_schema_prefix(self) -> None:
        misnamed = [
            (table.name, index.name)
            for table in SQLModel.metadata.tables.values()
            for index in table.indexes
            if not str(index.name).startswith(INDEX_PREFIXES)
        ]

        assert not misnamed, (
            "these index names carry neither schema prefix, so they read as "
            f"dialect-generated and cannot be dropped predictably: {misnamed}"
        )
