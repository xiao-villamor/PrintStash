"""What `--autogenerate` refuses to write, and why it cannot just get it right.

Autogenerate compares two *states*: the database's schema and the models' metadata. It
is very good at that and structurally blind to intent, and one blind spot destroys data.

Renaming `things.old_name` to `new_name` and dropping `old_name` while adding an
unrelated `new_name` produce **byte-identical diffs** — verified in
`test_a_rename_is_indistinguishable_from_a_drop` below. The distinguishing fact is what
the author meant, which was never written anywhere Alembic can read, so no setting can
separate them. Django asks interactively; Alembic is non-interactive by design.

What `alembic/env.py` can do, and now does, is refuse to write the file quietly. The
drop looks like an ordinary migration in review, and shipping it empties the column on
every installation.

Goes red when: the guard stops firing, or starts firing on a migration that is
genuinely adding one column and dropping another.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine

from app.db.migration_guards import (
    acknowledged_drops,
    dropped_and_added_columns,
    refuse_possible_renames,
)

BEFORE_DDL = (
    "CREATE TABLE things (id INTEGER NOT NULL PRIMARY KEY, old_name VARCHAR(64))"
)


def _diffs(after: MetaData) -> list[str]:
    """The operations autogenerate would emit to turn BEFORE_DDL into *after*."""
    path = Path(tempfile.mkdtemp()) / "t.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(BEFORE_DDL)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"target_metadata": after})
            return [
                str(diff)
                for diff in produce_migrations(context, after).upgrade_ops.as_diffs()
            ]
    finally:
        engine.dispose()


def _renamed_metadata() -> MetaData:
    """The models after renaming `old_name` to `new_name`."""
    metadata = MetaData()
    sa.Table(
        "things",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("new_name", sa.String(64)),
    )
    return metadata


class _FakeColumn:
    """The one attribute `dropped_and_added_columns` reads off a column."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeUpgradeOps:
    """Stands in for Alembic's `UpgradeOps`, whose `as_diffs()` is all that is read."""

    def __init__(self, diffs: list[object]) -> None:
        self._diffs = diffs

    def as_diffs(self) -> list[object]:
        return self._diffs


class TestAutogenerate:
    def test_a_rename_is_indistinguishable_from_a_drop(self) -> None:
        """The reason the guard exists, rather than a configuration fix.

        Both intentions leave the database in the same place, so the differ sees the
        same thing. This is not an Alembic defect; it is what a state comparison can
        know.
        """
        rename_diffs = _diffs(_renamed_metadata())

        drop_and_add_diffs = _diffs(_renamed_metadata())

        assert rename_diffs == drop_and_add_diffs
        assert any("add_column" in diff for diff in rename_diffs)
        assert any("remove_column" in diff for diff in rename_diffs)


class TestProcessRevisionDirectives:
    """The `env.py` hook, exercised through its own helpers.

    Driving it through `alembic revision --autogenerate` would need a mutated copy of
    the real models; the hook's decision is a pure function of the operations it is
    handed, so it is tested as one.
    """

    def test_refuses_a_migration_that_swaps_a_column_in_one_table(self) -> None:
        with pytest.raises(RuntimeError, match="looks like to a schema differ"):
            refuse_possible_renames(
                {"things": (["old_name"], ["new_name"])}, allowed=set()
            )

    def test_allows_it_once_the_drop_is_acknowledged(self) -> None:
        refuse_possible_renames(
            {"things": (["old_name"], ["new_name"])}, allowed={"things.old_name"}
        )

    def test_allows_a_drop_with_no_matching_add(self) -> None:
        # Dropping a column and adding nothing cannot be a rename, so it passes
        # untouched — the guard must not become a tax on ordinary deletions.
        refuse_possible_renames({"things": (["old_name"], [])}, allowed=set())

    def test_allows_an_add_with_no_matching_drop(self) -> None:
        refuse_possible_renames({"things": ([], ["new_name"])}, allowed=set())

    def test_names_both_columns_with_the_fix(self) -> None:
        with pytest.raises(RuntimeError) as failure:
            refuse_possible_renames(
                {"things": (["old_name"], ["new_name"])}, allowed=set()
            )

        message = str(failure.value)
        assert "things.old_name" in message
        assert "things.new_name" in message or 'new_column_name="new_name"' in message
        assert "allow_column_drop=things.old_name" in message


class TestDroppedAndAddedColumns:
    """Reading the operation list autogenerate produced.

    Reached from `env.py` in real use, which the suite never runs through
    `--autogenerate`, so it is exercised directly. The shapes matter: Alembic mixes
    flat tuples for column operations with *nested lists* for `alter_column` groups,
    and a reader that does not skip the latter raises on the first type change it meets.
    """

    def test_groups_the_column_changes_by_table(self) -> None:
        upgrade_ops = _FakeUpgradeOps(
            [
                ("add_column", None, "things", _FakeColumn("new_name")),
                ("remove_column", None, "things", _FakeColumn("old_name")),
                ("add_column", None, "others", _FakeColumn("extra")),
            ]
        )

        changes = dropped_and_added_columns(upgrade_ops)

        assert changes == {
            "things": (["old_name"], ["new_name"]),
            "others": ([], ["extra"]),
        }

    def test_ignores_the_nested_groups_alter_column_produces(self) -> None:
        # An `alter_column` arrives as a list of tuples rather than a tuple. Treating it
        # as one unpacks the wrong number of values, which is why this is asserted
        # rather than assumed.
        upgrade_ops = _FakeUpgradeOps(
            [
                [("modify_nullable", None, "things", "id", {}, True, False)],
                ("add_column", None, "things", _FakeColumn("new_name")),
            ]
        )

        changes = dropped_and_added_columns(upgrade_ops)

        assert changes == {"things": ([], ["new_name"])}

    def test_reports_nothing_for_a_migration_that_touches_no_columns(self) -> None:
        changes = dropped_and_added_columns(
            _FakeUpgradeOps([("create_index", None, "things", ["name"])])
        )

        assert changes == {}


class TestAcknowledgedDrops:
    """What `-x allow_column_drop=…` leaves behind in the migration.

    The escape hatch has to exist — sometimes a table really does lose one column and
    gain an unrelated one — but an acknowledgement that lives only in the shell history
    of whoever typed it is no better than no acknowledgement. A reviewer meeting
    `add_column` next to `remove_column` cannot tell a vetted drop from the rename the
    guard exists to catch, which is the same failure one step later.

    `env.py` appends these to the migration's message, so they reach the docstring, the
    filename and `alembic history`.
    """

    def test_reports_a_drop_the_author_confirmed(self) -> None:
        vetted = acknowledged_drops(
            {"things": (["old_name"], ["new_name"])}, allowed={"things.old_name"}
        )

        assert vetted == ["things.old_name"]

    def test_reports_nothing_when_the_drop_has_no_matching_add(self) -> None:
        # No add means the guard never fired, so there was nothing to acknowledge and
        # nothing worth recording.
        vetted = acknowledged_drops(
            {"things": (["old_name"], [])}, allowed={"things.old_name"}
        )

        assert vetted == []

    def test_reports_nothing_for_an_unacknowledged_drop(self) -> None:
        vetted = acknowledged_drops(
            {"things": (["old_name"], ["new_name"])}, allowed=set()
        )

        assert vetted == []
