"""Immutable preset edit triggers for fresh and upgraded databases.

Background sync changes editable fields and invalidates local drafts; timestamps
and derived usage counts do not. Revise this contract when its field set changes.
"""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

EDIT_FIELDS = {
    "filament_profiles": (
        "name",
        "material_type",
        "material_brand",
        "cost_per_kg",
        "notes",
        "spoolman_filament_id",
        "density_g_cm3",
        "diameter_mm",
        "edit_identity",
    ),
    "printer_profiles": (
        "name",
        "printer_model",
        "slicer_name",
        "nozzle_diameter_mm",
        "notes",
        "edit_identity",
    ),
}


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("profile_edit_contract_dialect_unsupported")
    operator = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    for table, fields in EDIT_FIELDS.items():
        name = f"ps_{table}_edit_v1"
        changed = " OR ".join(
            f'NEW."{field}" {operator} OLD."{field}"' for field in fields
        )
        if dialect == "postgresql":
            connection.execute(
                DDL(f"""
                CREATE OR REPLACE FUNCTION {name}() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    IF NEW.edit_version = OLD.edit_version AND ({changed}) THEN
                        NEW.edit_version := OLD.edit_version + 1;
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
                WHEN NEW.edit_version = OLD.edit_version AND ({changed})
                BEGIN
                    UPDATE {table} SET edit_version = edit_version + 1 WHERE id = NEW.id;
                END
            """)
            )


def uninstall(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("profile_edit_contract_dialect_unsupported")
    for table in EDIT_FIELDS:
        name = f"ps_{table}_edit_v1"
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
