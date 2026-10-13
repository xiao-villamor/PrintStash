"""Immutable browser editing contract; activity timestamps are not edits."""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

_NAME = "ps_browser_devices_edit_v1"
_FIELDS = ("name", "user_id", "credential_hash", "revoked_at")


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("browser_edit_contract_dialect_unsupported")
    operator = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    changed = " OR ".join(
        f'NEW."{field}" {operator} OLD."{field}"' for field in _FIELDS
    )
    if dialect == "postgresql":
        connection.execute(
            DDL(f"""
            CREATE OR REPLACE FUNCTION {_NAME}() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.edit_version = OLD.edit_version AND ({changed}) THEN
                    NEW.edit_version := OLD.edit_version + 1;
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(DDL(f"DROP TRIGGER IF EXISTS {_NAME} ON browser_devices"))
        connection.execute(
            DDL(
                f"CREATE TRIGGER {_NAME} BEFORE UPDATE ON browser_devices FOR EACH ROW EXECUTE FUNCTION {_NAME}()"
            )
        )
    else:
        connection.execute(
            DDL(f"""
            CREATE TRIGGER IF NOT EXISTS {_NAME} AFTER UPDATE ON browser_devices
            WHEN NEW.edit_version = OLD.edit_version AND ({changed})
            BEGIN
                UPDATE browser_devices SET edit_version = edit_version + 1 WHERE id = NEW.id;
            END
        """)
        )


def uninstall(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.execute(DDL(f"DROP TRIGGER IF EXISTS {_NAME} ON browser_devices"))
        connection.execute(DDL(f"DROP FUNCTION IF EXISTS {_NAME}()"))
    elif dialect == "sqlite":
        connection.execute(DDL(f"DROP TRIGGER IF EXISTS {_NAME}"))
    else:
        raise ValueError("browser_edit_contract_dialect_unsupported")


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    if (
        isinstance(connection, Connection)
        and "browser_devices" in inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contract)
