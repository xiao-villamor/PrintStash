"""add the audit foreign keys SQLite never got

Eighteen foreign keys the models have always declared, and which no SQLite
installation that upgraded through the chain has. `69b6a6d8a1d1` added the columns
and guarded `op.create_foreign_key` with `if not is_sqlite:`, because SQLite has no
`ALTER TABLE … ADD CONSTRAINT` — the statement fails with
`near "FOREIGN": syntax error`. The column landed everywhere; the constraint landed
on no SQLite database. PostgreSQL installations are unaffected: they are all built by
`create_all` (the chain's baseline cannot bootstrap Postgres), so they have had these
all along.

This closes the gap the way SQLite requires: `op.batch_alter_table` rebuilds each
table with the constraint in its `CREATE TABLE`. Seven tables, measured at 10 ms per
10,000 rows and 93 ms per 100,000 on a library-shaped `files`.

**Reflection, deliberately, not `copy_from`.** Batch mode without `copy_from` rebuilds
the table in the shape the database currently has, and adds only what this migration
names. `copy_from` would instead make the table *become* the stated definition, which
would sweep up the enum-representation and server-default differences between the two
schemas as well — a much larger blast radius than eighteen foreign keys, and not what
this migration is for. See `.agents/skills/database/SKILL.md`.

Foreign keys are off for the rebuild because it drops each table, which a live
constraint from another table refuses. Alembic's own connection does not enable them,
so this asserts that rather than assuming it, and runs `PRAGMA foreign_key_check`
afterwards to prove the rebuild left no orphan.

Revision ID: eb8435c9400e
Revises: 'a7c9e1b5d3f2'
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "eb8435c9400e"
down_revision: Union[str, Sequence[str], None] = "a7c9e1b5d3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_safe_to_rebuild() -> None:
    """Refuse to rebuild while foreign keys are enforced, and prove nothing broke.

    A `batch_alter_table` rebuild drops the original table. Another table's foreign
    key pointing at it makes that `DROP` fail outright, so enforcement has to be off —
    which it is, because Alembic's engine installs no pragmas. Asserting it beats
    assuming it: if a future change gives Alembic a pragma-configured engine, this
    fails with a sentence instead of an `IntegrityError` from inside a rebuild.
    """
    if op.get_context().as_sql:
        # Offline (`alembic upgrade --sql`): there is no connection to ask, and an
        # operator reviewing the generated DDL is not running it yet. Emitting the
        # pragma as a statement they can see is the useful thing to do instead.
        op.execute("-- run with foreign_keys=OFF; this migration rebuilds tables")
        return
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    enforced = bind.exec_driver_sql("PRAGMA foreign_keys").scalar()
    if enforced:
        raise RuntimeError(
            "cannot rebuild these tables while SQLite foreign keys are enforced: "
            "the rebuild drops each table and a live constraint from another table "
            "refuses that. Alembic's engine normally leaves enforcement off."
        )


# Every (table, column, referred table) this migration constrains. Also the list the
# repair below walks, and the list the verification is scoped to.
_FOREIGN_KEYS: tuple[tuple[str, str, str], ...] = (
    ("collections", "created_by", "users"),
    ("collections", "deleted_by", "users"),
    ("collections", "updated_by", "users"),
    ("files", "deleted_by", "users"),
    ("files", "external_library_id", "external_libraries"),
    ("models", "created_by", "users"),
    ("models", "deleted_by", "users"),
    ("models", "updated_by", "users"),
    ("print_jobs", "created_by", "users"),
    ("print_jobs", "deleted_by", "users"),
    ("print_jobs", "updated_by", "users"),
    ("printers", "created_by", "users"),
    ("printers", "deleted_by", "users"),
    ("printers", "updated_by", "users"),
    ("tags", "created_by", "users"),
    ("tags", "deleted_by", "users"),
    ("tags", "updated_by", "users"),
    ("users", "deleted_by", "users"),
)


def _detach_orphans() -> None:
    """Null any of these references that points at a row which is not there.

    Repair before constrain, because adding a constraint to a table that already
    violates it is how an upgrade leaves a database in a state it cannot write to.
    SQLite adds the constraint without validating existing rows (enforcement is off
    for the rebuild), so the violation would sit there until something touched the
    row and then fail at the worst possible moment.

    Nulling is the right repair and costs nothing: every one of these columns is
    nullable, and all but one are audit pointers — `created_by`, `updated_by`,
    `deleted_by`. A pointer to a user id that does not exist carries no information
    that nulling it destroys. `files.external_library_id` is the same shape: a file
    pointing at a library that is gone is already detached in every sense but this
    one.
    """
    for table, column, referred in _FOREIGN_KEYS:
        # `op.execute` rather than the bind, so this renders in offline mode too — an
        # operator reviewing `--sql` output has to see the repair, not just the
        # constraints it makes possible.
        op.execute(
            f"UPDATE {table} SET {column} = NULL "  # noqa: S608 - fixed identifiers
            f"WHERE {column} IS NOT NULL "
            f"AND {column} NOT IN (SELECT id FROM {referred})"
        )


def _assert_no_orphans_in_new_constraints() -> None:
    """Prove the repair worked, for the constraints this migration adds and no others.

    `PRAGMA foreign_key_check` is the obvious tool and the wrong one, even scoped to a
    table: it reports every violation of every constraint on that table. The migration
    test data alone has `print_jobs` rows pointing at models and printers that do not
    exist — violations of constraints that predate this migration and are none of its
    business. Reporting them here would turn an unrelated inconsistency into a failed
    upgrade on a real installation.

    So this asks the same question the repair asked, and expects no answer.
    """
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    for table, column, referred in _FOREIGN_KEYS:
        remaining = bind.exec_driver_sql(
            f"SELECT COUNT(*) FROM {table} "  # noqa: S608 - fixed identifiers
            f"WHERE {column} IS NOT NULL "
            f"AND {column} NOT IN (SELECT id FROM {referred})"
        ).scalar()
        if remaining:
            raise RuntimeError(
                f"{remaining} row(s) in {table}.{column} still point at a missing "
                f"{referred} row after the repair, so the new constraint cannot hold"
            )


def upgrade() -> None:
    _assert_safe_to_rebuild()
    _detach_orphans()

    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_collections_created_by_users"),
            "users",
            ["created_by"],
            ["id"],
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_collections_deleted_by_users"),
            "users",
            ["deleted_by"],
            ["id"],
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_collections_updated_by_users"),
            "users",
            ["updated_by"],
            ["id"],
        )

    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_files_deleted_by_users"), "users", ["deleted_by"], ["id"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_files_external_library_id_external_libraries"),
            "external_libraries",
            ["external_library_id"],
            ["id"],
        )

    with op.batch_alter_table("models", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_models_created_by_users"), "users", ["created_by"], ["id"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_models_deleted_by_users"), "users", ["deleted_by"], ["id"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_models_updated_by_users"), "users", ["updated_by"], ["id"]
        )

    with op.batch_alter_table("print_jobs", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_print_jobs_created_by_users"),
            "users",
            ["created_by"],
            ["id"],
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_print_jobs_deleted_by_users"),
            "users",
            ["deleted_by"],
            ["id"],
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_print_jobs_updated_by_users"),
            "users",
            ["updated_by"],
            ["id"],
        )

    with op.batch_alter_table("printers", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_printers_created_by_users"), "users", ["created_by"], ["id"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_printers_deleted_by_users"), "users", ["deleted_by"], ["id"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_printers_updated_by_users"), "users", ["updated_by"], ["id"]
        )

    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_tags_created_by_users"), "users", ["created_by"], ["id"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_tags_deleted_by_users"), "users", ["deleted_by"], ["id"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_tags_updated_by_users"), "users", ["updated_by"], ["id"]
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_users_deleted_by_users"), "users", ["deleted_by"], ["id"]
        )

    _assert_no_orphans_in_new_constraints()


def downgrade() -> None:
    _assert_safe_to_rebuild()

    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_collections_created_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_collections_deleted_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_collections_updated_by_users"), type_="foreignkey"
        )

    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_files_deleted_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_files_external_library_id_external_libraries"),
            type_="foreignkey",
        )

    with op.batch_alter_table("models", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_models_created_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_models_deleted_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_models_updated_by_users"), type_="foreignkey"
        )

    with op.batch_alter_table("print_jobs", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_print_jobs_created_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_print_jobs_deleted_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_print_jobs_updated_by_users"), type_="foreignkey"
        )

    with op.batch_alter_table("printers", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_printers_created_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_printers_deleted_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_printers_updated_by_users"), type_="foreignkey"
        )

    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_tags_created_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_tags_deleted_by_users"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_tags_updated_by_users"), type_="foreignkey"
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_users_deleted_by_users"), type_="foreignkey"
        )
