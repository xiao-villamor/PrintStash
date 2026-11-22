"""Immutable vault-configuration edit trigger, version 4, for upgrade and fresh schema.

This version covers fields edited by PUT /config, including legacy/provider
projections. Operational timestamps, initialization markers and independently
owned settings deliberately do not invalidate that form's editing base.
Add a new contract version to change this installed field set after release.
Version 4 adds ``prefer_rendered_3mf_preview`` and replaces version 3.
"""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

from app.db import config_edit_contracts_v3

# v3 plus the embedded cover preference.
EDIT_FIELDS = (
    *config_edit_contracts_v3.EDIT_FIELDS,
    "prefer_rendered_3mf_preview",
)


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("config_edit_contract_dialect_unsupported")
    operator = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    changed = " OR ".join(
        f"NEW.{field} {operator} OLD.{field}" for field in EDIT_FIELDS
    )
    if dialect == "postgresql":
        connection.execute(
            DDL(f"""
            CREATE OR REPLACE FUNCTION ps_vault_config_edit_v4() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.vault_edit_version = OLD.vault_edit_version AND ({changed}) THEN
                    NEW.vault_edit_version := OLD.vault_edit_version + 1;
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v4 ON system_config")
        )
        connection.execute(
            DDL("""
            CREATE TRIGGER ps_vault_config_edit_v4 BEFORE UPDATE ON system_config
            FOR EACH ROW EXECUTE FUNCTION ps_vault_config_edit_v4()
        """)
        )
    else:
        connection.execute(
            DDL(f"""
            CREATE TRIGGER IF NOT EXISTS ps_vault_config_edit_v4 AFTER UPDATE ON system_config
            WHEN NEW.vault_edit_version = OLD.vault_edit_version AND ({changed})
            BEGIN
                UPDATE system_config SET vault_edit_version = vault_edit_version + 1 WHERE id = NEW.id;
            END
        """)
        )


def uninstall(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.execute(
            DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v4 ON system_config")
        )
        connection.execute(DDL("DROP FUNCTION IF EXISTS ps_vault_config_edit_v4()"))
    elif dialect == "sqlite":
        connection.execute(DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v4"))
    else:
        raise ValueError("config_edit_contract_dialect_unsupported")


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    if (
        isinstance(connection, Connection)
        and "system_config" in inspect(connection).get_table_names()
    ):
        install(connection)


event.remove(
    SQLModel.metadata, "after_create", config_edit_contracts_v3._fresh_contract
)
event.listen(SQLModel.metadata, "after_create", _fresh_contract)
