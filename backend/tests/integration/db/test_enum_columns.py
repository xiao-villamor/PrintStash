"""Enum columns are TEXT, their values checked, and Alembic keeps the check current.

The contract: a Python enum member is what a column holds and what a read
returns; the database refuses any other value on its own (a raw ``INSERT``
included); and when an enum gains or loses a member, autogenerate produces the
migration that re-creates its CHECK constraint, rather than nothing.
"""

from __future__ import annotations

from enum import Enum

import pytest
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import ops
from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    Table,
    create_engine,
    text,
)
from sqlalchemy.exc import IntegrityError, StatementError
from sqlmodel import Session

from app.db.enum_columns import EnumText, enum_check
from app.db.models import Job, JobKind, JobState


class Colour(str, Enum):
    RED = "red"
    GREEN = "green"


def _table(metadata: MetaData, enum: type[Enum]) -> Table:
    return Table(
        "paints",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("colour", EnumText(enum), nullable=False),
        enum_check("colour", enum),
    )


class TestEnumText:
    def test_reads_back_the_member_it_stored(self, db_session: Session) -> None:
        job = Job(id="enum-read", kind=JobKind.SOURCES_SCAN, subject_key="library/1")
        db_session.add(job)
        db_session.commit()
        db_session.expire_all()

        stored = db_session.get(Job, "enum-read")

        assert stored is not None
        assert stored.kind is JobKind.SOURCES_SCAN
        assert stored.state is JobState.QUEUED

    def test_refuses_to_bind_a_value_outside_the_enum(
        self, db_session: Session
    ) -> None:
        db_session.add(Job(id="enum-bind", kind="no.such.kind", subject_key="x/1"))  # type: ignore[arg-type]

        with pytest.raises(StatementError, match="not a valid JobKind"):
            db_session.commit()


class TestCheckConstraint:
    def test_the_database_refuses_a_value_outside_the_enum(
        self, db_session: Session
    ) -> None:
        # Below the ORM: what a hand-written statement or another client writes.
        with pytest.raises(IntegrityError, match="ck_jobs_kind_values"):
            db_session.execute(
                text(
                    "INSERT INTO jobs (id, kind, subject_key, priority, state,"
                    " status_json, attempts, resubmits, execution_epoch, created_at, updated_at)"
                    " VALUES ('raw', 'no.such.kind', 'x/1', 'interactive', 'queued',"
                    " '{}', 0, 0, 'enum-check-epoch', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

    def test_escapes_a_quote_inside_a_value(self) -> None:
        class Quoted(str, Enum):
            ODD = "it's"

        assert str(enum_check("colour", Quoted).sqltext) == "colour IN ('it''s')"


class TestAutogenerate:
    def _changes(self, database: type[Enum], models: type[Enum]) -> list:
        engine = create_engine("sqlite://")
        existing = MetaData(
            naming_convention={"ck": "ck_%(table_name)s_%(constraint_name)s"}
        )
        _table(existing, database)
        existing.create_all(engine)
        wanted = MetaData(
            naming_convention={"ck": "ck_%(table_name)s_%(constraint_name)s"}
        )
        _table(wanted, models)
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            script = produce_migrations(context, wanted)
        return [
            operation
            for table_ops in script.upgrade_ops.ops
            if isinstance(table_ops, ops.ModifyTableOps)
            for operation in table_ops.ops
            if isinstance(
                operation, (ops.DropConstraintOp, ops.CreateCheckConstraintOp)
            )
        ]

    def test_a_matching_check_needs_no_migration(self) -> None:
        assert self._changes(Colour, Colour) == []

    def test_a_new_member_recreates_the_check(self) -> None:
        class Wider(str, Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        drop, create = self._changes(Colour, Wider)

        assert isinstance(drop, ops.DropConstraintOp)
        assert drop.constraint_name == "ck_paints_colour_values"
        assert isinstance(create, ops.CreateCheckConstraintOp)
        assert "'blue'" in str(create.condition)

    def test_a_removed_member_recreates_the_check(self) -> None:
        class Narrower(str, Enum):
            RED = "red"

        _, create = self._changes(Colour, Narrower)

        assert "'green'" not in str(create.condition)


class TestPlainChecks:
    """A named CHECK the model adds to an existing table is migrated too."""

    def _changes(self, *, in_database: bool) -> list:
        def table(metadata: MetaData, *, checked: bool) -> Table:
            extra = (
                [CheckConstraint("(a IS NULL) = (b IS NULL)", name="a_with_b")]
                if checked
                else []
            )
            return Table(
                "pairs",
                metadata,
                Column("id", Integer, primary_key=True),
                Column("a", Integer),
                Column("b", Integer),
                *extra,
            )

        convention = {"ck": "ck_%(table_name)s_%(constraint_name)s"}
        engine = create_engine("sqlite://")
        existing = MetaData(naming_convention=convention)
        table(existing, checked=in_database)
        existing.create_all(engine)
        wanted = MetaData(naming_convention=convention)
        table(wanted, checked=True)
        with engine.connect() as connection:
            script = produce_migrations(MigrationContext.configure(connection), wanted)
        return [
            operation
            for table_ops in script.upgrade_ops.ops
            if isinstance(table_ops, ops.ModifyTableOps)
            for operation in table_ops.ops
            if isinstance(operation, ops.CreateCheckConstraintOp)
        ]

    def test_a_missing_check_is_created(self) -> None:
        (create,) = self._changes(in_database=False)

        assert create.constraint_name == "ck_pairs_a_with_b"

    def test_a_present_check_is_left_alone(self) -> None:
        # Its SQL text is dialect-rewritten, so only presence is compared.
        assert self._changes(in_database=True) == []
