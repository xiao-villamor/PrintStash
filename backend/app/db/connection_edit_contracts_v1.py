"""Immutable connection edit triggers for fresh and upgraded databases.

All editable settings, including encrypted credentials and backup flags, advance
one independent connection version. Operational timestamps are not edit state.
"""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

EDIT_FIELDS = {
    "storage_connections": (
        "edit_version",
        (
            "name",
            "kind",
            "purpose",
            "config_json",
            "secret_json",
            "enabled",
            "manual_backup_enabled",
            "automatic_backup_enabled",
            "edit_identity",
        ),
    ),
}


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("connection_edit_contract_dialect_unsupported")
    operator = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    for table, (version, fields) in EDIT_FIELDS.items():
        name = f"ps_connection_{table}_edit_v1"
        changed = " OR ".join(
            f'NEW."{field}" {operator} OLD."{field}"' for field in fields
        )
        if dialect == "postgresql":
            connection.execute(
                DDL(f"""
                CREATE OR REPLACE FUNCTION {name}() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    IF NEW.{version} = OLD.{version} AND ({changed}) THEN
                        NEW.{version} := OLD.{version} + 1;
                    END IF;
                    RETURN NEW;
                END $$
            """)
            )
            connection.execute(DDL(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
            connection.execute(
                DDL(
                    f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()"
                )
            )
        else:
            connection.execute(
                DDL(f"""
                CREATE TRIGGER IF NOT EXISTS {name} AFTER UPDATE ON {table}
                WHEN NEW.{version} = OLD.{version} AND ({changed})
                BEGIN
                    UPDATE {table} SET {version} = {version} + 1 WHERE id = NEW.id;
                END
            """)
            )


def uninstall(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("connection_edit_contract_dialect_unsupported")
    for table in EDIT_FIELDS:
        name = f"ps_connection_{table}_edit_v1"
        if dialect == "postgresql":
            connection.execute(DDL(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
            connection.execute(DDL(f"DROP FUNCTION IF EXISTS {name}()"))
        else:
            connection.execute(DDL(f"DROP TRIGGER IF EXISTS {name}"))


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    if isinstance(connection, Connection) and set(EDIT_FIELDS) <= set(
        inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contract)
