"""Immutable vault-configuration edit trigger shared by upgrade and fresh schema.

This version covers fields edited by PUT /config, including legacy/provider
projections. Operational timestamps, initialization markers and independently
owned settings deliberately do not invalidate that form's editing base.
Add a new contract version to change this installed field set after release.
"""

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

EDIT_FIELDS = (
    "currency",
    "data_dir",
    "thumb_dir",
    "storage_backend",
    "storage_provider",
    "storage_provider_config_json",
    "storage_provider_secret_json",
    "s3_root",
    "s3_bucket",
    "s3_endpoint_url",
    "s3_region",
    "s3_access_key",
    "s3_secret_key",
    "backup_s3_bucket",
    "backup_s3_endpoint_url",
    "backup_s3_region",
    "backup_s3_access_key",
    "backup_s3_secret_key",
    "oidc_enabled",
    "oidc_issuer_url",
    "oidc_client_id",
    "oidc_client_secret",
    "oidc_scopes",
    "oidc_username_claim",
    "oidc_groups_claim",
    "oidc_admin_groups",
    "oidc_display_name",
    "oidc_redirect_uri",
    "oidc_allow_insecure_http",
    "auto_mark_known_good",
    "external_libraries_enabled",
    "derivatives_mesh_enabled",
    "derivatives_gcode_enabled",
    "derivatives_toolpath_enabled",
    "automatic_backups_enabled",
    "automatic_backup_time_utc",
    "manual_local_backup_enabled",
    "automatic_local_backup_enabled",
    "backup_retention_days",
    "trash_retention_days",
    "storage_min_free_bytes",
    "model_thumbnail_width",
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
            CREATE OR REPLACE FUNCTION ps_vault_config_edit_v1() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.vault_edit_version = OLD.vault_edit_version AND ({changed}) THEN
                    NEW.vault_edit_version := OLD.vault_edit_version + 1;
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v1 ON system_config")
        )
        connection.execute(
            DDL("""
            CREATE TRIGGER ps_vault_config_edit_v1 BEFORE UPDATE ON system_config
            FOR EACH ROW EXECUTE FUNCTION ps_vault_config_edit_v1()
        """)
        )
    else:
        connection.execute(
            DDL(f"""
            CREATE TRIGGER IF NOT EXISTS ps_vault_config_edit_v1 AFTER UPDATE ON system_config
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
            DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v1 ON system_config")
        )
        connection.execute(DDL("DROP FUNCTION IF EXISTS ps_vault_config_edit_v1()"))
    elif dialect == "sqlite":
        connection.execute(DDL("DROP TRIGGER IF EXISTS ps_vault_config_edit_v1"))
    else:
        raise ValueError("config_edit_contract_dialect_unsupported")


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    if (
        isinstance(connection, Connection)
        and "system_config" in inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contract)
