"""What a fresh install gets and what an upgraded one gets, compared row by row.

`run_migrations` has two paths to head and they do not produce the same schema:

* **fresh** — no tables at all, so `create_all` builds from the models and stamps
  head. This is what a new installation gets.
* **upgraded** — an existing `alembic_version`, so the chain runs from wherever the
  installation is. This is what a self-hoster who upgraded has.

The difference *was* eighteen foreign keys — 108 against 90 — and the cause was not
an oversight. SQLite has no `ALTER TABLE ADD CONSTRAINT`, so the migrations that added
the audit columns guarded their `op.create_foreign_key` calls with
`if not is_sqlite` (see `69b6a6d8a1d1_phase_4c_4d_lifecycle_audit.py`). The column
lands, the constraint does not. `batch_alter_table` — which Alembic implements on
SQLite by rebuilding the table — is the way to do it, and this repo already uses it
elsewhere; it was not used here.

Two shapes therefore exist in the wild, both SQLite:

* Installed before v0.7.2, when a fresh database still replayed the chain: the pure
  chain shape, missing all eighteen.
* Installed v0.7.2 or later, when `create_all` became the fresh path: every
  constraint the models declared *at install time*, minus any a later migration
  added without one.

PostgreSQL is not affected. The chain's baseline cannot bootstrap a Postgres
database at all — which is exactly why the fresh path became `create_all` — so every
Postgres installation is a `create_all` installation and has all 108.

`eb8435c9400e_add_missing_audit_foreign_keys` closed it, by rebuilding those seven
tables the way SQLite requires. The foreign keys now match exactly, and this test
pins that: `KNOWN_MISSING_IN_CHAIN` is empty and may not gain an entry.

131 differences remain, all in categories that do not change behaviour — enum columns
stored as text, Python-side defaults never written as server defaults, and indexes
one path creates and the other does not. Those are counted rather than enumerated,
because 131 lines of `unexpected index …` is not something anyone reads, and counted
rather than ignored because "does not change behaviour" is a judgement the comparator
does not make.
"""

from __future__ import annotations

import warnings
from collections import Counter
from pathlib import Path

import pytest
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import app.db.models  # noqa: F401 - registers every table on SQLModel.metadata
from alembic import command
from app.db import migrate as migrate_mod

# Foreign keys the models declare and the migration chain never creates, as
# (table, column). Two-sided: a new entry means fresh and upgraded installs drifted
# further apart, and a removed one means the gap was closed and this list should
# shrink with it.
# Empty, and that is the point. It held eighteen entries until
# `eb8435c9400e_add_missing_audit_foreign_keys` rebuilt the seven tables that were
# missing them, and one more until `vault_audit_findings.run_id` gained the
# `ondelete="CASCADE"` the chain had all along and the models did not. The two
# schemas now agree on every foreign key.
#
# Two-sided, so it stays that way: a new entry here means a migration changed the
# schema in a way `create_all` does not, which is how the gap opened the first time.
KNOWN_MISSING_IN_CHAIN: set[tuple[str, str]] = set()


def _foreign_keys(url: str) -> set[tuple[str, str]]:
    """Every (table, column) foreign key in the database.

    Every table, not a chosen few: the first version of this compared `files` and
    `models` alone, because that is where the flake surfaced, and it therefore
    reported five of the eighteen. A divergence list that only looks where you
    already know to look is not a divergence list.
    """
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        return {
            (table, column)
            for table in inspector.get_table_names()
            for key in inspector.get_foreign_keys(table)
            for column in key["constrained_columns"]
        }
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def fresh_install_keys(
    tmp_path_factory: pytest.TempPathFactory,
) -> set[tuple[str, str]]:
    """The foreign keys `create_all` builds — what a new installation gets."""
    path: Path = tmp_path_factory.mktemp("fresh") / "fresh.sqlite"
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    return _foreign_keys(url)


@pytest.fixture(scope="module")
def upgraded_install_keys(
    tmp_path_factory: pytest.TempPathFactory,
) -> set[tuple[str, str]]:
    """The foreign keys the chain builds — what an upgraded installation has."""
    path: Path = tmp_path_factory.mktemp("upgraded") / "upgraded.sqlite"
    url = f"sqlite:///{path}"
    command.upgrade(migrate_mod._alembic_config(url), "head")  # noqa: SLF001
    return _foreign_keys(url)


class TestForeignKeyParity:
    def test_the_chain_is_missing_exactly_the_known_set(
        self,
        fresh_install_keys: set[tuple[str, str]],
        upgraded_install_keys: set[tuple[str, str]],
    ) -> None:
        missing = fresh_install_keys - upgraded_install_keys

        undocumented = sorted(missing - KNOWN_MISSING_IN_CHAIN)
        assert not undocumented, (
            "these foreign keys are new divergences between a fresh install and an "
            "upgraded one — the models declare them and no migration creates them: "
            f"{undocumented}. Add the migration rather than adding them here."
        )

        closed = sorted(KNOWN_MISSING_IN_CHAIN - missing)
        assert not closed, (
            "the chain now creates these, so the gap closed: "
            f"{closed}. Delete them from KNOWN_MISSING_IN_CHAIN."
        )

    def test_the_chain_creates_nothing_the_models_do_not_declare(
        self,
        fresh_install_keys: set[tuple[str, str]],
        upgraded_install_keys: set[tuple[str, str]],
    ) -> None:
        # The other direction, which has no known exceptions: a constraint the chain
        # creates but the models do not declare would be enforced only for upgraded
        # installs, and would never appear in a fresh one.
        extra = sorted(upgraded_install_keys - fresh_install_keys)

        assert not extra, (
            "the migration chain creates foreign keys the models do not declare, so "
            f"only upgraded installs enforce them: {extra}"
        )


# The migrated schema and the models are structurally identical: zero differences by
# `_orphan_schema_issues`, the app's own comparator. `eb8435c9400e` closed the
# foreign keys and `6acea2a5e555` closed the rest — 212 operations over 31 tables,
# covering column types, server defaults, indexes and unique constraints.
#
# Kept as a mapping rather than a bare `assert not issues` so that a category which
# reappears says which one it is, and so that recording a *deliberate* future
# divergence has an obvious home. It should stay empty.
STRUCTURAL_DIFFERENCE_COUNTS: dict[str, int] = {}


def _category(issue: str) -> str:
    """The kind of difference, with the object it is about stripped off."""
    if issue.startswith("structural difference"):
        return issue
    return " ".join(issue.split()[:2])


class TestStructuralParity:
    """The whole schema, not just its foreign keys.

    Running the entire suite a second time against a chain-built database would
    cover this, slowly. `_orphan_schema_issues` covers it in three seconds and with
    more authority, because it is the comparison the app itself makes when deciding
    whether a database is current: dialect-normalised columns, types, nullability
    and server defaults from Alembic autogenerate, plus explicit checks on primary
    and foreign keys, unique and check constraints, and partial-index predicates.

    Its verdict is now that the two are the same, which is what makes the
    orphan-rescue path in `run_migrations` usable at all: it adopts an unversioned
    database only when the schema matches the models exactly, and until the
    convergence migrations that was true of a `create_all` database and of no
    upgraded one.
    """

    def test_the_migrated_schema_differs_from_the_models_only_as_recorded(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        path = tmp_path_factory.mktemp("structural") / "chain.sqlite"
        url = f"sqlite:///{path}"
        command.upgrade(migrate_mod._alembic_config(url), "head")

        engine = create_engine(url)
        try:
            issues = migrate_mod._orphan_schema_issues(engine)  # noqa: SLF001
        finally:
            engine.dispose()

        counts = Counter(_category(issue) for issue in issues)

        grown = {
            category: (count, STRUCTURAL_DIFFERENCE_COUNTS.get(category, 0))
            for category, count in counts.items()
            if count > STRUCTURAL_DIFFERENCE_COUNTS.get(category, 0)
        }
        assert not grown, (
            "the migration chain drifted further from the models: "
            + ", ".join(
                f"{category} {now} (was {before})"
                for category, (now, before) in sorted(grown.items())
            )
            + ". A migration has to change the schema the same way `create_all` "
            "would, which on SQLite means `op.batch_alter_table` — never a DDL "
            "operation guarded by `if not is_sqlite`."
        )

        shrunk = {
            category: (counts.get(category, 0), expected)
            for category, expected in STRUCTURAL_DIFFERENCE_COUNTS.items()
            if counts.get(category, 0) < expected
        }
        assert not shrunk, (
            "the schemas converged, so these counts are stale: "
            + ", ".join(
                f"{category} {now} (recorded {before})"
                for category, (now, before) in sorted(shrunk.items())
            )
            + ". Lower them here."
        )


class TestAutogenerateIsEmpty:
    """Alembic itself has nothing left to say about a chain-built database.

    The strongest form of the invariant, and the most direct: rather than comparing
    schemas ourselves, ask the tool that writes migrations whether it would write one.
    On a converged database it produces `pass`.

    This is also the reassuring half of the workflow the database skill describes.
    "Read the generated migration and delete what is not your change" sounds alarming
    until you know that a synchronised repo generates *only* your change — the 890
    lines and 212 operations that had to be trimmed once were the accumulated drift of
    three months, not a standing review burden. Keeping this green is what keeps the
    next `--autogenerate` small enough to read in one sitting.

    Goes red when a model changes without a migration, or a migration changes the
    schema in a way `create_all` does not.
    """

    def test_a_chain_built_database_needs_no_further_migration(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        path = tmp_path_factory.mktemp("autogen") / "chain.sqlite"
        url = f"sqlite:///{path}"
        command.upgrade(migrate_mod._alembic_config(url), "head")

        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                with warnings.catch_warnings():
                    # The deliberate files <-> models cycle; see _all_table_names.
                    warnings.filterwarnings(
                        "ignore", message="Cannot correctly sort tables.*"
                    )
                    context = MigrationContext.configure(
                        connection,
                        opts={
                            "compare_type": True,
                            "compare_server_default": True,
                            "target_metadata": SQLModel.metadata,
                        },
                    )
                    script = produce_migrations(context, SQLModel.metadata)
        finally:
            engine.dispose()

        rendered = [
            str(op)
            for op in script.upgrade_ops.as_diffs()  # type: ignore[union-attr]
        ]
        assert not rendered, (
            "`alembic revision --autogenerate` would still emit operations against a "
            "database built by the migration chain, so the two supported "
            f"installations do not have the same schema: {rendered[:10]}"
        )
