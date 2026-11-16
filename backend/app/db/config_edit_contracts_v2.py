"""Immutable vault-configuration edit trigger, version 2, for upgrade and fresh schema.

This version covers fields edited by PUT /config, including legacy/provider
projections. Operational timestamps, initialization markers and independently
owned settings deliberately do not invalidate that form's editing base.
Add a new contract version to change this installed field set after release.
Version 2 adds ``derivatives_previews_enabled`` and replaces version 1.
"""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

from app.db import config_edit_contracts_v1

# v1 plus the independent multi-angle preview control.
EDIT_FIELDS = (*config_edit_contracts_v1.EDIT_FIELDS, "derivatives_previews_enabled")


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
            CREATE OR REPLACE FUNCTION ps_vault_config_edit_v2() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.vault_edit_version = OLD.vault_edit_version AND ({changed}) THEN
                    NEW.vault_edit_version := OLD.vault_edit_version + 1;
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v2 ON system_config")
        )
        connection.execute(
            DDL("""
            CREATE TRIGGER ps_vault_config_edit_v2 BEFORE UPDATE ON system_config
            FOR EACH ROW EXECUTE FUNCTION ps_vault_config_edit_v2()
        """)
        )
    else:
        connection.execute(
            DDL(f"""
            CREATE TRIGGER IF NOT EXISTS ps_vault_config_edit_v2 AFTER UPDATE ON system_config
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
            DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v2 ON system_config")
        )
        connection.execute(DDL("DROP FUNCTION IF EXISTS ps_vault_config_edit_v2()"))
    elif dialect == "sqlite":
        connection.execute(DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v2"))
    else:
        raise ValueError("config_edit_contract_dialect_unsupported")


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    if (
        isinstance(connection, Connection)
        and "system_config" in inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contract)
