"""Keep index-worker bookkeeping from invalidating library pages, contract v3.

Generation leases and progress counters change throughout indexing but do not
change the library's lexical query or card sequence. Preserve invalidation for
generation identity, configuration and activation, plus inserts and deletes.
This is a frozen database contract; future changes need another version.
"""

from sqlalchemy import DDL, Connection, event, inspect, text
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

from app.db import library_browse_v2  # noqa: F401

IGNORED_FIELDS = (
    "phase",
    "indexed_after_id",
    "lease_token",
    "lease_expires_at",
    "passage_after_id",
    "reconcile_kind",
    "processed",
    "copied",
    "truncated_count",
    "verified_count",
    "verified_at",
    "last_activity_at",
    "error_code",
    "job_id",
    "reservation_id",
    "estimated_bytes",
)
WATCHED_FIELDS = (
    "id",
    "space_id",
    "active_profile_key",
    "building_profile_key",
    "state",
    "index_dimension",
    "quantization",
    "index_backend",
    "transform_json",
    "vector_table_name",
    "index_state",
    "index_error",
    "version_token",
    "actor_id",
    "replaces_generation_id",
    "cancel_requested",
    "auto_activate",
    "activated_at",
    "retired_at",
    "retain_until",
    "created_at",
)
MARKER = "ps_browse_index_generations_update_v3"
PG_MARKER = "ps_browse_index_generations_v3"


def _execute(connection: Connection | MockConnection, statement: str) -> None:
    connection.execute(DDL(statement))


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        changed = " OR ".join(
            f"NEW.{field} IS NOT OLD.{field}" for field in WATCHED_FIELDS
        )
        _execute(
            connection, "DROP TRIGGER IF EXISTS ps_browse_index_generations_update_v1"
        )
        _execute(
            connection,
            f"CREATE TRIGGER IF NOT EXISTS {MARKER} AFTER UPDATE ON index_generations "
            f"WHEN {changed} BEGIN UPDATE library_revision SET revision=revision+1 WHERE id=1; END",
        )
    elif dialect == "postgresql":
        changed = " OR ".join(
            f"NEW.{field} IS DISTINCT FROM OLD.{field}" for field in WATCHED_FIELDS
        )
        _execute(connection, "DROP TRIGGER IF EXISTS ps_browse_v1 ON index_generations")
        _execute(
            connection,
            "CREATE TRIGGER ps_browse_v1 AFTER INSERT OR DELETE ON index_generations "
            "FOR EACH STATEMENT EXECUTE FUNCTION ps_browse_index_generations_v1()",
        )
        _execute(
            connection,
            f"CREATE OR REPLACE FUNCTION {PG_MARKER}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN UPDATE library_revision SET revision=revision+1 WHERE id=1; RETURN NULL; END $$",
        )
        _execute(connection, "DROP TRIGGER IF EXISTS ps_browse_v3 ON index_generations")
        _execute(
            connection,
            "CREATE TRIGGER ps_browse_v3 AFTER UPDATE ON index_generations "
            f"FOR EACH ROW WHEN ({changed}) EXECUTE FUNCTION {PG_MARKER}()",
        )
    else:
        raise ValueError("library_contract_dialect_unsupported")


def uninstall(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        _execute(connection, f"DROP TRIGGER IF EXISTS {MARKER}")
        _execute(
            connection,
            "CREATE TRIGGER IF NOT EXISTS ps_browse_index_generations_update_v1 "
            "AFTER UPDATE ON index_generations BEGIN "
            "INSERT INTO library_revision (id,epoch,revision) VALUES (1,lower(hex(randomblob(16))),1) "
            "ON CONFLICT (id) DO UPDATE SET revision=revision+1; END",
        )
    elif dialect == "postgresql":
        _execute(connection, "DROP TRIGGER IF EXISTS ps_browse_v3 ON index_generations")
        _execute(connection, f"DROP FUNCTION IF EXISTS {PG_MARKER}()")
        _execute(connection, "DROP TRIGGER IF EXISTS ps_browse_v1 ON index_generations")
        _execute(
            connection,
            "CREATE TRIGGER ps_browse_v1 AFTER INSERT OR UPDATE OR DELETE ON index_generations "
            "FOR EACH STATEMENT EXECUTE FUNCTION ps_browse_index_generations_v1()",
        )
    else:
        raise ValueError("library_contract_dialect_unsupported")


def installed(connection: Connection) -> bool:
    if connection.dialect.name == "sqlite":
        statement = "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=:name"
        name = MARKER
    elif connection.dialect.name == "postgresql":
        statement = "SELECT 1 FROM pg_proc WHERE proname=:name"
        name = PG_MARKER
    else:
        raise ValueError("library_contract_dialect_unsupported")
    return connection.execute(text(statement), {"name": name}).first() is not None


def _fresh_contract(metadata, connection: Connection, **_kwargs) -> None:
    if (
        isinstance(connection, Connection)
        and "library_revision" in inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contract)
