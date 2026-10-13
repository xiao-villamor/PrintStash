"""Immutable printer-settings edit contract for fresh and upgraded databases.

Telemetry, drain/default commands and provider-derived labels do not invalidate
settings drafts. Legacy/direct settings writes do; a new field set needs a new
contract revision after release.
"""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

EDIT_FIELDS = (
    "name",
    "provider",
    "moonraker_url",
    "api_key",
    "provider_variant",
    "bambu_host",
    "bambu_serial",
    "bambu_access_code",
    "prusalink_url",
    "prusalink_auth_mode",
    "prusalink_username",
    "prusalink_password",
    "prusalink_api_key",
    "elegoo_centauri_host",
    "elegoo_centauri_access_code",
    "elegoo_centauri_mainboard_id",
    "octoprint_url",
    "octoprint_api_key",
    "model_name",
    "notes",
    "group",
    "provider_material_sync_enabled",
    "operator_release_required",
    "deleted_at",
)


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("printer_edit_contract_dialect_unsupported")
    operator = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    changed = " OR ".join(
        f'NEW."{field}" {operator} OLD."{field}"' for field in EDIT_FIELDS
    )
    if dialect == "postgresql":
        connection.execute(
            DDL(f"""
            CREATE OR REPLACE FUNCTION ps_printer_edit_v1() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.edit_version = OLD.edit_version AND ({changed}) THEN
                    NEW.edit_version := OLD.edit_version + 1;
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(DDL("DROP TRIGGER IF EXISTS ps_printer_edit_v1 ON printers"))
        connection.execute(
            DDL("""
            CREATE TRIGGER ps_printer_edit_v1 BEFORE UPDATE ON printers
            FOR EACH ROW EXECUTE FUNCTION ps_printer_edit_v1()
        """)
        )
    else:
        connection.execute(
            DDL(f"""
            CREATE TRIGGER IF NOT EXISTS ps_printer_edit_v1 AFTER UPDATE ON printers
            WHEN NEW.edit_version = OLD.edit_version AND ({changed})
            BEGIN
                UPDATE printers SET edit_version = edit_version + 1 WHERE id = NEW.id;
            END
        """)
        )


def uninstall(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.execute(DDL("DROP TRIGGER IF EXISTS ps_printer_edit_v1 ON printers"))
        connection.execute(DDL("DROP FUNCTION IF EXISTS ps_printer_edit_v1()"))
    elif dialect == "sqlite":
        connection.execute(DDL("DROP TRIGGER IF EXISTS ps_printer_edit_v1"))
    else:
        raise ValueError("printer_edit_contract_dialect_unsupported")


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    if (
        isinstance(connection, Connection)
        and "printers" in inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contract)
