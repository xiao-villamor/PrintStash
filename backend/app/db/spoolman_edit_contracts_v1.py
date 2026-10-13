"""Immutable Spoolman editing trigger, independent of operational probe results."""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

EDIT_FIELDS = (
    "spoolman_enabled",
    "spoolman_base_url",
    "spoolman_api_key",
    "spoolman_write_enabled",
    "spoolman_write_force",
)


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("spoolman_edit_contract_dialect_unsupported")
    operator = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    changed = " OR ".join(
        f"NEW.{field} {operator} OLD.{field}" for field in EDIT_FIELDS
    )
    if dialect == "postgresql":
        connection.execute(
            DDL(f"""
            CREATE OR REPLACE FUNCTION ps_spoolman_edit_v1() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.spoolman_edit_version = OLD.spoolman_edit_version AND ({changed}) THEN
                    NEW.spoolman_edit_version := OLD.spoolman_edit_version + 1;
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            DDL("DROP TRIGGER IF EXISTS ps_spoolman_edit_v1 ON system_config")
        )
        connection.execute(
            DDL("""
            CREATE TRIGGER ps_spoolman_edit_v1 BEFORE UPDATE ON system_config
            FOR EACH ROW EXECUTE FUNCTION ps_spoolman_edit_v1()
        """)
        )
    else:
        connection.execute(
            DDL(f"""
            CREATE TRIGGER IF NOT EXISTS ps_spoolman_edit_v1 AFTER UPDATE ON system_config
            WHEN NEW.spoolman_edit_version = OLD.spoolman_edit_version AND ({changed})
            BEGIN
                UPDATE system_config SET spoolman_edit_version = spoolman_edit_version + 1 WHERE id = NEW.id;
            END
        """)
        )


def uninstall(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.execute(
            DDL("DROP TRIGGER IF EXISTS ps_spoolman_edit_v1 ON system_config")
        )
        connection.execute(DDL("DROP FUNCTION IF EXISTS ps_spoolman_edit_v1()"))
    elif dialect == "sqlite":
        connection.execute(DDL("DROP TRIGGER IF EXISTS ps_spoolman_edit_v1"))
    else:
        raise ValueError("spoolman_edit_contract_dialect_unsupported")


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    if (
        isinstance(connection, Connection)
        and "system_config" in inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contract)
