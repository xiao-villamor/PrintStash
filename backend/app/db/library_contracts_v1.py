"""Immutable v1 browse/edit trigger DDL, shared by migration and fresh bootstrap.

This module is a historical database contract. Add a new version instead of
changing installed v1 DDL after release. Triggers cover direct SQL, ORM and
background writers; their increments commit or roll back with the source row.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import DDL, Connection, event, inspect
from sqlalchemy.engine.mock import MockConnection
from sqlmodel import SQLModel

BROWSE_TABLES = (
    "models",
    "multipart_models",
    "multipart_parts",
    "multipart_model_choices",
    "collections",
    "tags",
    "model_tags",
    "collection_tags",
    "file_tags",
    "multipart_model_tags",
    "model_stars",
    "multipart_model_stars",
    "files",
    "metadata",
    "print_jobs",
    "printer_files",
    "printers",
    "documents",
    "collection_permissions",
    "users",
    "model_provenance_sources",
    "model_provenance_fields",
    "model_source_covers",
    "similarity_candidates",
    "similarity_review_decisions",
    "search_passages",
    "search_lexical_postings",
    "search_lexical_terms",
    "search_lexical_state",
    "index_generations",
    "user_search_preferences",
    "vault_generations",
)
AUTHORIZATION_TABLES = ("users", "collection_permissions", "collections")
AUTHORIZATION_FIELDS = {
    "models": ("collection_id", "deleted_at"),
    "multipart_models": ("collection_id",),
    "documents": ("collection_id", "deleted_at"),
}

EDIT_FIELDS = {
    "models": ("name", "description", "source_url", "collection_id"),
    "multipart_models": (
        "name",
        "description",
        "collection_id",
        "cover_model_id",
        "cover_image_url",
        "cover_filename",
    ),
    "documents": ("name", "body", "collection_id", "multipart_model_id"),
}
# Related aggregate fields are part of the same user-editable metadata.
EDIT_CHILDREN = {
    "model_tags": ("models", "model_id"),
    "multipart_model_tags": ("multipart_models", "multipart_model_id"),
    "multipart_parts": ("multipart_models", "multipart_model_id"),
    "multipart_model_choices": ("multipart_models", "multipart_model_id"),
}


def _execute_ddl(connection: Connection | MockConnection, statement: str) -> None:
    """Use the SQLAlchemy DDL interface shared by live and offline connections."""
    connection.execute(DDL(statement))


def install(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("library_contract_dialect_unsupported")
    # The row can have been cleared by a test/transfer; bootstrap is idempotent.
    epoch = uuid4().hex
    _execute_ddl(
        connection,
        f"INSERT INTO library_revision (id, epoch, revision) VALUES (1, '{epoch}', 0) ON CONFLICT (id) DO NOTHING",
    )
    # Offline rendering has no database to inspect. Its MockConnection emits
    # the same DDL; real installations still require every dependency table.
    tables = (
        set(BROWSE_TABLES)
        if isinstance(connection, MockConnection)
        else set(inspect(connection).get_table_names())
    )
    for table in BROWSE_TABLES:
        if table not in tables:
            raise RuntimeError(f"library_revision_dependency_missing:{table}")
        if dialect == "postgresql":
            _execute_ddl(
                connection,
                f"""CREATE OR REPLACE FUNCTION ps_browse_{table}_v1() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO library_revision (id,epoch,revision) VALUES (1, md5(random()::text || clock_timestamp()::text), 1)
                ON CONFLICT (id) DO UPDATE SET revision = library_revision.revision + 1;
                RETURN NULL;
            END $$""",
            )
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS ps_browse_v1 ON {table}")
            _execute_ddl(
                connection,
                f"CREATE TRIGGER ps_browse_v1 AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION ps_browse_{table}_v1()",
            )
        else:
            for action in ("INSERT", "UPDATE", "DELETE"):
                _execute_ddl(
                    connection,
                    f"""CREATE TRIGGER IF NOT EXISTS ps_browse_{table}_{action.lower()}_v1 AFTER {action} ON {table}
                BEGIN
                    INSERT INTO library_revision (id,epoch,revision) VALUES (1,lower(hex(randomblob(16))),1)
                    ON CONFLICT (id) DO UPDATE SET revision = revision + 1;
                END""",
                )
    for table in AUTHORIZATION_TABLES:
        if dialect == "postgresql":
            _execute_ddl(
                connection,
                f"CREATE OR REPLACE FUNCTION ps_auth_{table}_v1() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN UPDATE library_revision SET authorization_revision = authorization_revision + 1 WHERE id=1; RETURN NULL; END $$",
            )
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS ps_auth_v1 ON {table}")
            _execute_ddl(
                connection,
                f"CREATE TRIGGER ps_auth_v1 AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION ps_auth_{table}_v1()",
            )
        else:
            for action in ("INSERT", "UPDATE", "DELETE"):
                _execute_ddl(
                    connection,
                    f"CREATE TRIGGER IF NOT EXISTS ps_auth_{table}_{action.lower()}_v1 AFTER {action} ON {table} BEGIN UPDATE library_revision SET authorization_revision = authorization_revision + 1 WHERE id=1; END",
                )
    for table, fields in AUTHORIZATION_FIELDS.items():
        if dialect == "postgresql":
            changed = " OR ".join(
                f"NEW.{field} IS DISTINCT FROM OLD.{field}" for field in fields
            )
            _execute_ddl(
                connection,
                f"CREATE OR REPLACE FUNCTION ps_auth_{table}_v1() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {changed} THEN UPDATE library_revision SET authorization_revision = authorization_revision + 1 WHERE id=1; END IF; RETURN NULL; END $$",
            )
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS ps_auth_v1 ON {table}")
            _execute_ddl(
                connection,
                f"CREATE TRIGGER ps_auth_v1 AFTER UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION ps_auth_{table}_v1()",
            )
        else:
            changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in fields)
            _execute_ddl(
                connection,
                f"CREATE TRIGGER IF NOT EXISTS ps_auth_{table}_update_v1 AFTER UPDATE ON {table} WHEN {changed} BEGIN UPDATE library_revision SET authorization_revision = authorization_revision + 1 WHERE id=1; END",
            )
    for table, fields in EDIT_FIELDS.items():
        if dialect == "postgresql":
            changed = " OR ".join(
                f"NEW.{field} IS DISTINCT FROM OLD.{field}" for field in fields
            )
            _execute_ddl(
                connection,
                f"""CREATE OR REPLACE FUNCTION ps_edit_{table}_v1() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.edit_version = OLD.edit_version AND ({changed}) THEN
                    NEW.edit_version := OLD.edit_version + 1;
                END IF;
                RETURN NEW;
            END $$""",
            )
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS ps_edit_v1 ON {table}")
            _execute_ddl(
                connection,
                f"CREATE TRIGGER ps_edit_v1 BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION ps_edit_{table}_v1()",
            )
        else:
            changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in fields)
            _execute_ddl(
                connection,
                f"""CREATE TRIGGER IF NOT EXISTS ps_edit_{table}_v1 AFTER UPDATE ON {table}
            WHEN NEW.edit_version = OLD.edit_version AND ({changed})
            BEGIN UPDATE {table} SET edit_version = edit_version + 1 WHERE id=NEW.id; END""",
            )
    for table, (parent, key) in EDIT_CHILDREN.items():
        for action in ("INSERT", "UPDATE", "DELETE"):
            references = ["OLD"] if action == "DELETE" else ["NEW"]
            if action == "UPDATE":
                references.append("OLD")
            ids = ", ".join(f"{reference}.{key}" for reference in references)
            sql = f"UPDATE {parent} SET edit_version = edit_version + 1 WHERE id IN ({ids});"
            name = f"ps_edit_{table}_{action.lower()}_v1"
            if dialect == "postgresql":
                _execute_ddl(
                    connection,
                    f"CREATE OR REPLACE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {sql} RETURN NULL; END $$",
                )
                _execute_ddl(connection, f"DROP TRIGGER IF EXISTS {name} ON {table}")
                _execute_ddl(
                    connection,
                    f"CREATE TRIGGER {name} AFTER {action} ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()",
                )
            else:
                _execute_ddl(
                    connection,
                    f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {action} ON {table} BEGIN {sql} END",
                )

    for action in ("INSERT", "UPDATE", "DELETE"):
        reference = "OLD" if action == "DELETE" else "NEW"
        sources = f"{reference}.provenance_source_id"
        changed = (
            f"{reference}.user_override_set OR {reference}.user_value_json IS NOT NULL"
        )
        if action == "UPDATE":
            sources += ", OLD.provenance_source_id"
            operator = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
            changed = " OR ".join(
                f"NEW.{field} {operator} OLD.{field}"
                for field in ("user_value_json", "user_override_set")
            )
        sql = f"UPDATE models SET edit_version=edit_version+1 WHERE id IN (SELECT model_id FROM model_provenance_sources WHERE id IN ({sources}));"
        name = f"ps_edit_provenance_fields_{action.lower()}_v1"
        if dialect == "postgresql":
            _execute_ddl(
                connection,
                f"CREATE OR REPLACE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {changed} THEN {sql} END IF; RETURN NULL; END $$",
            )
            _execute_ddl(
                connection, f"DROP TRIGGER IF EXISTS {name} ON model_provenance_fields"
            )
            _execute_ddl(
                connection,
                f"CREATE TRIGGER {name} AFTER {action} ON model_provenance_fields FOR EACH ROW EXECUTE FUNCTION {name}()",
            )
        else:
            _execute_ddl(
                connection,
                f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {action} ON model_provenance_fields WHEN {changed} BEGIN {sql} END",
            )


def uninstall(connection: Connection | MockConnection) -> None:
    dialect = connection.dialect.name
    for table in BROWSE_TABLES:
        if dialect == "postgresql":
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS ps_browse_v1 ON {table}")
            _execute_ddl(connection, f"DROP FUNCTION IF EXISTS ps_browse_{table}_v1()")
        else:
            for action in ("insert", "update", "delete"):
                _execute_ddl(
                    connection, f"DROP TRIGGER IF EXISTS ps_browse_{table}_{action}_v1"
                )
    for table in (*AUTHORIZATION_TABLES, *AUTHORIZATION_FIELDS):
        if dialect == "postgresql":
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS ps_auth_v1 ON {table}")
            _execute_ddl(connection, f"DROP FUNCTION IF EXISTS ps_auth_{table}_v1()")
        else:
            for action in ("insert", "update", "delete"):
                _execute_ddl(
                    connection, f"DROP TRIGGER IF EXISTS ps_auth_{table}_{action}_v1"
                )
    for table in EDIT_FIELDS:
        if dialect == "postgresql":
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS ps_edit_v1 ON {table}")
            _execute_ddl(connection, f"DROP FUNCTION IF EXISTS ps_edit_{table}_v1()")
        else:
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS ps_edit_{table}_v1")
    for table in EDIT_CHILDREN:
        for action in ("insert", "update", "delete"):
            name = f"ps_edit_{table}_{action}_v1"
            if dialect == "postgresql":
                _execute_ddl(connection, f"DROP TRIGGER IF EXISTS {name} ON {table}")
                _execute_ddl(connection, f"DROP FUNCTION IF EXISTS {name}()")
            else:
                _execute_ddl(connection, f"DROP TRIGGER IF EXISTS {name}")

    for action in ("insert", "update", "delete"):
        name = f"ps_edit_provenance_fields_{action}_v1"
        if dialect == "postgresql":
            _execute_ddl(
                connection, f"DROP TRIGGER IF EXISTS {name} ON model_provenance_fields"
            )
            _execute_ddl(connection, f"DROP FUNCTION IF EXISTS {name}()")
        else:
            _execute_ddl(connection, f"DROP TRIGGER IF EXISTS {name}")


def _fresh_contracts(metadata, connection: Connection, **_kwargs) -> None:
    if (
        isinstance(connection, Connection)
        and "library_revision" in inspect(connection).get_table_names()
    ):
        install(connection)


event.listen(SQLModel.metadata, "after_create", _fresh_contracts)
