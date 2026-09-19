"""Disposable databases and read-only inspection for the import benchmark.

PostgreSQL defaults to the contract suite's test-container owner. A host-managed
isolated service may instead supply its maintenance database URL. An application
database URL is rejected: each run creates and removes only its own fresh database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.engine import Connection, Engine, make_url

from app.db.url import normalize_database_url


@contextmanager
def disposable_database(
    root: Path, dialect: str, *, postgres_admin_url: str | None = None
) -> Iterator[Engine]:
    if postgres_admin_url is not None and dialect != "postgres":
        raise ValueError("A PostgreSQL test server requires --database postgres")
    if dialect == "sqlite":
        path = root / "vault.sqlite"
        path.touch(exist_ok=False)
        engine = create_engine(f"sqlite:///{path}")
        try:
            yield engine
        finally:
            engine.dispose()
        return
    if dialect != "postgres":
        raise ValueError(f"Unsupported benchmark database: {dialect}")

    if postgres_admin_url is None:
        # Lazy import keeps the default SQLite benchmark independent of Docker.
        from tests.containers import postgres_url

        postgres_admin_url = postgres_url()
    else:
        configured = make_url(postgres_admin_url)
        if (
            configured.get_backend_name() != "postgresql"
            or configured.database != "postgres"
        ):
            raise ValueError(
                "The isolated test server must target PostgreSQL's postgres maintenance database"
            )
        if configured.query:
            raise ValueError(
                "PostgreSQL test server URL query overrides are not supported"
            )
    base_url = make_url(normalize_database_url(postgres_admin_url))
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    name = "import_bench_" + uuid4().hex
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        try:
            engine = create_engine(base_url.set(database=name))
            try:
                yield engine
            finally:
                engine.dispose()
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
    finally:
        admin.dispose()


def database_record(connection: Connection) -> dict:
    """Identify comparable database versions without publishing connection URLs."""
    return {
        "backend": connection.dialect.name,
        "server_version": list(connection.dialect.server_version_info or ()),
    }


def read_rows(connection: Connection, statement: str) -> list[tuple]:
    """Return portable, JSON-serializable rows from benchmark-owned queries."""
    return [tuple(row) for row in connection.exec_driver_sql(statement)]


def pending_enrichment(
    connection: Connection, *, similarity: bool = False
) -> tuple[int, dict[str, int]]:
    schema = inspect(connection)
    tables = set(schema.get_table_names())
    pending = 0
    failed = {}
    for table in (
        "artifact_analysis_generations",
        "thumbnail_generations",
        "search_projection_requests",
        "similarity_runs",
    ):
        if table not in tables or (table == "similarity_runs" and not similarity):
            continue
        columns = {column["name"] for column in schema.get_columns(table)}
        policy = (
            "processing_policy = 'background' AND "
            if "processing_policy" in columns
            else ""
        )
        condition = (
            policy + "state IN ('pending','queued','running','cancelling')"
            if "state" in columns
            else "1=1"
        )
        pending += connection.exec_driver_sql(
            f"SELECT COUNT(*) FROM {table} WHERE {condition}"
        ).scalar_one()
        if "state" in columns:
            applicable = ""
            if table == "thumbnail_generations":
                # The public view calls this not_applicable: G-code does not
                # promise an embedded preview. Retain every other failure,
                # including an unknown reason or a stale source identity.
                applicable = (
                    " AND NOT EXISTS (SELECT 1 FROM files f "
                    "WHERE f.id = thumbnail_generations.file_id "
                    "AND f.sha256 = thumbnail_generations.source_sha256 "
                    "AND f.file_type = 'GCODE' "
                    "AND thumbnail_generations.failure_reason = 'no_embedded_thumbnail')"
                )
            failed[table] = connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table} WHERE {policy}state = 'failed'{applicable}"
            ).scalar_one()
    return pending, failed


def metadata_catalog(connection: Connection) -> dict[str, list[dict]]:
    """Compare parsed facts by source digest, excluding run-specific identities."""
    schema = MetaData()
    files = Table("files", schema, autoload_with=connection)
    catalog = {}
    for name in ("metadata", "artifact_material_requirements"):
        table = Table(name, schema, autoload_with=connection)
        facts = [
            column
            for column in table.columns
            if column.name not in {"id", "file_id", "created_at", "updated_at"}
        ]
        rows = connection.execute(
            select(files.c.sha256.label("source_sha256"), *facts)
            .join_from(files, table, table.c.file_id == files.c.id)
            .order_by(files.c.sha256, *facts)
        )
        catalog[name] = [dict(row) for row in rows.mappings()]
    return catalog
