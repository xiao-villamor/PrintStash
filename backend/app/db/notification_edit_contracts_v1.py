"""Immutable notification edit triggers for fresh and upgraded databases.

Channel auto-disable changes editable state; delivery telemetry does not.
The master switch is independent of vault and Search settings.
"""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

EDIT_FIELDS = {
    "notification_channels": (
        "edit_version",
        (
            "name",
            "target",
            "enabled",
            "config_json",
            "events_json",
            "printer_ids_json",
            "edit_identity",
        ),
    ),
    "system_config": ("notification_edit_version", ("notifications_enabled",)),
}


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("notification_edit_contract_dialect_unsupported")
    operator = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    for table, (version, fields) in EDIT_FIELDS.items():
        name = f"ps_notification_{table}_edit_v1"
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
        raise ValueError("notification_edit_contract_dialect_unsupported")
    for table in EDIT_FIELDS:
        name = f"ps_notification_{table}_edit_v1"
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
