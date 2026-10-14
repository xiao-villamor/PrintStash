"""Authorization revision triggers for users and collections, contract v2.

Library contract v1 advances ``authorization_revision`` on every write to
``users`` and ``collections``. An open library view reads that revision as
"what this session may see could have changed": it discards everything private
it holds and loads again. Creating a Collection, or editing a user's email,
changes nobody's access, yet an import that creates folders reset every open
library once per folder.

v2 replaces only those two tables' v1 triggers. The revision advances when a
user's access can have changed (role, activation, session revocation,
deletion) or when a Collection moves, is renamed in the tree, is trashed or is
deleted, since inherited roles follow the tree. Every other v1 trigger is
untouched. Like v1, this module is a historical contract: add a new version
instead of changing installed DDL after release.
"""

from __future__ import annotations

from sqlalchemy import DDL, Connection, event, inspect, text
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

# Imported for its after_create listener, which must run before this one's.
from app.db import library_contracts_v1  # noqa: F401

# The columns whose change can alter what a caller may see.
ACCESS_FIELDS = {
    "collections": ("parent_id", "path", "deleted_at"),
    "users": ("is_superuser", "is_active", "auth_version", "deleted_at"),
}
_BUMP = (
    "UPDATE library_revision SET authorization_revision = "
    "authorization_revision + 1 WHERE id=1;"
)
# One trigger (SQLite) and one function (PostgreSQL) only v2 creates.
MARKER = "ps_auth_collections_delete_v2"
PG_MARKER = "ps_auth_collections_v2"


def _execute(connection: Connection | MockConnection, statement: str) -> None:
    connection.execute(DDL(statement))


def _dialect(connection: Connection | MockConnection) -> str:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("library_contract_dialect_unsupported")
    return dialect


def _drop_v1(connection: Connection | MockConnection, table: str) -> None:
    if _dialect(connection) == "postgresql":
        _execute(connection, f"DROP TRIGGER IF EXISTS ps_auth_v1 ON {table}")
        _execute(connection, f"DROP FUNCTION IF EXISTS ps_auth_{table}_v1()")
    else:
        for action in ("insert", "update", "delete"):
            _execute(connection, f"DROP TRIGGER IF EXISTS ps_auth_{table}_{action}_v1")


def _restore_v1(connection: Connection | MockConnection, table: str) -> None:
    """The exact v1 DDL for one table, for a downgrade."""
    if _dialect(connection) == "postgresql":
        _execute(
            connection,
            f"CREATE OR REPLACE FUNCTION ps_auth_{table}_v1() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {_BUMP} RETURN NULL; END $$",
        )
        _execute(connection, f"DROP TRIGGER IF EXISTS ps_auth_v1 ON {table}")
        _execute(
            connection,
            f"CREATE TRIGGER ps_auth_v1 AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION ps_auth_{table}_v1()",
        )
    else:
        for action in ("INSERT", "UPDATE", "DELETE"):
            _execute(
                connection,
                f"CREATE TRIGGER IF NOT EXISTS ps_auth_{table}_{action.lower()}_v1 AFTER {action} ON {table} BEGIN {_BUMP} END",
            )


def install(connection: Connection | MockConnection) -> None:
    """Replace v1's users/collections triggers with access-only ones."""
    dialect = _dialect(connection)
    for table, fields in ACCESS_FIELDS.items():
        _drop_v1(connection, table)
        if dialect == "postgresql":
            changed = " OR ".join(
                f"NEW.{field} IS DISTINCT FROM OLD.{field}" for field in fields
            )
            _execute(
                connection,
                f"""CREATE OR REPLACE FUNCTION ps_auth_{table}_v2() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' OR ({changed}) THEN {_BUMP} END IF;
                RETURN NULL;
            END $$""",
            )
            _execute(connection, f"DROP TRIGGER IF EXISTS ps_auth_v2 ON {table}")
            _execute(
                connection,
                f"CREATE TRIGGER ps_auth_v2 AFTER UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ps_auth_{table}_v2()",
            )
        else:
            changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in fields)
            _execute(
                connection,
                f"CREATE TRIGGER IF NOT EXISTS ps_auth_{table}_update_v2 AFTER UPDATE ON {table} WHEN {changed} BEGIN {_BUMP} END",
            )
            _execute(
                connection,
                f"CREATE TRIGGER IF NOT EXISTS ps_auth_{table}_delete_v2 AFTER DELETE ON {table} BEGIN {_BUMP} END",
            )


def uninstall(connection: Connection | MockConnection) -> None:
    """Put v1's users/collections triggers back."""
    dialect = _dialect(connection)
    for table in ACCESS_FIELDS:
        if dialect == "postgresql":
            _execute(connection, f"DROP TRIGGER IF EXISTS ps_auth_v2 ON {table}")
            _execute(connection, f"DROP FUNCTION IF EXISTS ps_auth_{table}_v2()")
        else:
            for action in ("update", "delete"):
                _execute(
                    connection, f"DROP TRIGGER IF EXISTS ps_auth_{table}_{action}_v2"
                )
        _restore_v1(connection, table)


def installed(connection: Connection) -> bool:
    if _dialect(connection) == "postgresql":
        return (
            connection.execute(
                text("SELECT 1 FROM pg_proc WHERE proname = :name"), {"name": PG_MARKER}
            ).first()
            is not None
        )
    return (
        connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=:name"),
            {"name": MARKER},
        ).first()
        is not None
    )


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    # Registered after v1's listener (this module imports v1), so v1's
    # triggers exist to be replaced.
    if (
        isinstance(connection, Connection)
        and "library_revision" in inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contract)
