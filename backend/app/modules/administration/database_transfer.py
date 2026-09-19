"""Verified SQLite → PostgreSQL transfer of durable rows, never inference.

The target must be empty. Copy and validation are one PostgreSQL transaction;
failure leaves it empty. Raw reflected columns preserve encrypted ciphertext and
native BLOBs byte-for-byte instead of running ORM encryption hooks a second time.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from alembic.script import ScriptDirectory
from sqlalchemy import Engine, MetaData, Table, inspect, select, text
from sqlmodel import Session, SQLModel

from app.db.derived_objects import managed_names
from app.db.models import IndexGeneration
from app.modules.search import lexical_index, vector_index


class DatabaseTransferError(ValueError):
    """Stable operator-facing code; never contains URLs or row contents."""


@dataclass(frozen=True)
class TableProof:
    name: str
    rows: int
    sha256: str


@dataclass(frozen=True)
class TransferReport:
    dry_run: bool
    tables: tuple[TableProof, ...]
    derived_indexes: int = 0


def _canonical(value):
    if isinstance(value, (bytes, memoryview)):
        return {"bytes": bytes(value).hex()}
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone.utc).isoformat()
            if value.tzinfo is None
            else value.astimezone(timezone.utc).isoformat()
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value.normalize())
    return value


def _rows(connection, table: Table, *, batch_size: int):
    columns = [column for column in table.c if column.computed is None]
    keys = list(table.primary_key.columns)
    if not keys:
        raise DatabaseTransferError("database_transfer_primary_key_required")
    result = connection.execute(
        select(*columns).order_by(*keys).execution_options(yield_per=batch_size)
    )
    yield from result.mappings().partitions(batch_size)


def _proof(connection, table: Table, *, batch_size: int) -> TableProof:
    digest = hashlib.sha256()
    count = 0
    for batch in _rows(connection, table, batch_size=batch_size):
        for row in batch:
            digest.update(
                json.dumps(
                    {key: _canonical(value) for key, value in row.items()},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            )
            digest.update(b"\n")
            count += 1
    return TableProof(table.name, count, digest.hexdigest())


def _empty_target(connection) -> None:
    inspector = inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    for name in inspector.get_table_names():
        if name == "alembic_version":
            continue
        if (
            connection.execute(text(f"SELECT 1 FROM {quote(name)} LIMIT 1")).first()
            is not None
        ):
            raise DatabaseTransferError("database_transfer_target_not_empty")
    unknown = (
        set(inspector.get_table_names())
        - set(SQLModel.metadata.tables)
        - {"alembic_version"}
    )
    if unknown:
        raise DatabaseTransferError("database_transfer_target_schema_unknown")


def _defer_foreign_keys(
    connection, tables: list[Table]
) -> list[tuple[str, str, bool, str | None]]:
    quote = connection.dialect.identifier_preparer.quote
    original = []
    for table in tables:
        for constraint in table.foreign_key_constraints:
            if not constraint.name:
                raise DatabaseTransferError("database_transfer_constraint_unnamed")
            original.append(
                (
                    table.name,
                    constraint.name,
                    bool(constraint.deferrable),
                    constraint.initially,
                )
            )
            connection.execute(
                text(
                    f"ALTER TABLE {quote(table.name)} ALTER CONSTRAINT {quote(constraint.name)} DEFERRABLE INITIALLY DEFERRED"
                )
            )
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return original


def _restore_foreign_keys(connection, original) -> None:
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    quote = connection.dialect.identifier_preparer.quote
    for table, name, deferrable, initially in original:
        mode = (
            (
                "DEFERRABLE INITIALLY DEFERRED"
                if initially == "DEFERRED"
                else "DEFERRABLE INITIALLY IMMEDIATE"
            )
            if deferrable
            else "NOT DEFERRABLE INITIALLY IMMEDIATE"
        )
        connection.execute(
            text(f"ALTER TABLE {quote(table)} ALTER CONSTRAINT {quote(name)} {mode}")
        )


def _transfer(
    source: Engine,
    target: Engine,
    *,
    dry_run: bool = True,
    batch_size: int = 256,
    replace_existing: bool = False,
    rebuild: bool = True,
) -> TransferReport:
    """Copy a current SQLite database into an empty PostgreSQL database.

    Source reads hold one SQLite snapshot; target DDL/data/proofs are atomic.
    Foreign keys are copied in dependency order and temporarily deferred for
    cycles (including Model → thumbnail Artifact → Model), then restored and
    validated before commit. No filesystem storage or model weights are copied.
    """
    if source.dialect.name != "sqlite" or target.dialect.name != "postgresql":
        raise DatabaseTransferError("database_transfer_dialects_invalid")
    if not 1 <= batch_size <= 1024:
        raise DatabaseTransferError("database_transfer_batch_invalid")
    from pathlib import Path

    head = ScriptDirectory(
        str(Path(__file__).resolve().parents[3] / "alembic")
    ).get_current_head()
    with source.connect() as src, target.begin() as dst:
        src.exec_driver_sql("PRAGMA query_only=ON")
        src.exec_driver_sql("BEGIN")
        try:
            dst.execute(text("SELECT pg_advisory_xact_lock(166166166)"))
            if not replace_existing:
                _empty_target(dst)
            if (
                src.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                != head
            ):
                raise DatabaseTransferError("database_transfer_source_upgrade_required")
            if src.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise DatabaseTransferError(
                    "database_transfer_source_foreign_keys_invalid"
                )
            expected = set(SQLModel.metadata.tables)
            actual = (
                set(inspect(src).get_table_names())
                - managed_names(src)
                - {"alembic_version"}
            )
            if actual != expected:
                raise DatabaseTransferError("database_transfer_source_schema_unknown")
            source_meta = MetaData()
            source_meta.reflect(src, only=sorted(expected))
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message="Cannot correctly sort tables.*"
                )
                tables = list(source_meta.sorted_tables)
            proofs = tuple(
                _proof(src, table, batch_size=batch_size) for table in tables
            )
            if dry_run:
                return TransferReport(True, proofs)
            SQLModel.metadata.create_all(dst)
            target_meta = MetaData()
            target_meta.reflect(dst, only=sorted(expected))
            if replace_existing:
                known_derived = managed_names(dst)
                unknown = (
                    set(inspect(dst).get_table_names())
                    - expected
                    - known_derived
                    - {"alembic_version"}
                )
                if unknown:
                    raise DatabaseTransferError(
                        "database_transfer_target_schema_unknown"
                    )
                quote = dst.dialect.identifier_preparer.quote
                locked = ",".join(quote(name) for name in sorted(expected))
                dst.execute(text("SET LOCAL lock_timeout='5s'"))
                dst.execute(text(f"LOCK TABLE {locked} IN ACCESS EXCLUSIVE MODE"))
                for name in sorted(known_derived):
                    if (
                        name.startswith("gen_vectors_") and not name.endswith("_hnsw")
                    ) or name.startswith("code_gen_"):
                        dst.execute(text(f"DROP TABLE {quote(name)}"))
            originals = _defer_foreign_keys(dst, list(target_meta.tables.values()))
            if replace_existing:
                for table in reversed(tables):
                    dst.execute(target_meta.tables[table.name].delete())
            for table in tables:
                destination = target_meta.tables[table.name]
                for batch in _rows(src, table, batch_size=batch_size):
                    dst.execute(destination.insert(), [dict(row) for row in batch])
                proof = _proof(dst, destination, batch_size=batch_size)
                if proof != next(item for item in proofs if item.name == table.name):
                    raise DatabaseTransferError("database_transfer_verification_failed")
            _restore_foreign_keys(dst, originals)
            for table in target_meta.tables.values():
                for column in table.primary_key.columns:
                    sequence = dst.execute(
                        text("SELECT pg_get_serial_sequence(:table,:column)"),
                        {"table": table.name, "column": column.name},
                    ).scalar()
                    if sequence:
                        maximum = dst.execute(
                            select(column).order_by(column.desc()).limit(1)
                        ).scalar()
                        dst.execute(
                            text(
                                "SELECT setval(CAST(:sequence AS regclass),:maximum,:called)"
                            ),
                            {
                                "sequence": sequence,
                                "maximum": max(1, maximum or 1),
                                "called": maximum is not None,
                            },
                        )
            dst.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            dst.execute(text("DELETE FROM alembic_version"))
            dst.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:head)"),
                {"head": head},
            )
            rebuilt = 0
            # Derivatives are rematerialized on the destination engine. The
            # portable store remains usable if native capabilities are absent.
            with Session(bind=dst) as session:
                lexical_index.rebuild_partition(session)
                for generation in session.scalars(select(IndexGeneration)).all():
                    # Native adapters are database-specific derivatives. Preserve
                    # the immutable Space/transform while selecting the target's
                    # corresponding native adapter (including portable int8).
                    if generation.index_backend == "sqlite_vec":
                        generation.index_backend = "pgvector"
                    generation.vector_table_name = None
                    generation.index_state = "absent"
                    generation.indexed_after_id = 0
                    if rebuild and vector_index.prepare(session, generation):
                        while vector_index.rebuild_partition(
                            session, generation, limit=batch_size
                        ):
                            pass
                        rebuilt += int(generation.index_state == "ready")
                session.flush()
            return TransferReport(False, proofs, rebuilt)
        finally:
            src.rollback()
            src.exec_driver_sql("PRAGMA query_only=OFF")


def transfer(
    source: Engine, target: Engine, *, dry_run: bool = True, batch_size: int = 256
) -> TransferReport:
    """Verify/copy SQLite into an empty PostgreSQL destination; never replace data."""
    return _transfer(source, target, dry_run=dry_run, batch_size=batch_size)


def restore_postgres(source: Engine, target: Engine) -> TransferReport:
    """Replace PostgreSQL from a verified portable snapshot, atomically.

    Called only by the backup owner's admitted restore operation, after its
    storage reconciliation and durable restore marker have been staged. Native
    rebuilds are left to the normal worker so the restore commits promptly.
    """
    return _transfer(
        source, target, dry_run=False, replace_existing=True, rebuild=False
    )


def snapshot_postgres(
    source: Engine, target: Engine, *, batch_size: int = 256
) -> TransferReport:
    """Export a repeatable-read PostgreSQL snapshot as portable durable SQLite.

    This is the existing backup archive's db.sqlite3 payload. Native vector
    tables and extensions are omitted; all Space/Generation mappings and native
    floats remain in the durable tables. The snapshot needs no model files.
    """
    if source.dialect.name != "postgresql" or target.dialect.name != "sqlite":
        raise DatabaseTransferError("database_transfer_dialects_invalid")
    if not 1 <= batch_size <= 1024:
        raise DatabaseTransferError("database_transfer_batch_invalid")
    with (
        source.connect().execution_options(isolation_level="REPEATABLE READ") as src,
        target.begin() as dst,
    ):
        src.execute(text("SET TRANSACTION READ ONLY"))
        if inspect(dst).get_table_names():
            raise DatabaseTransferError("database_transfer_target_not_empty")
        expected = set(SQLModel.metadata.tables)
        actual = (
            set(inspect(src).get_table_names())
            - managed_names(src)
            - {"alembic_version"}
        )
        if actual != expected:
            raise DatabaseTransferError("database_transfer_source_schema_unknown")
        source_meta, target_meta = MetaData(), MetaData()
        source_meta.reflect(src, only=sorted(expected))
        SQLModel.metadata.create_all(dst)
        target_meta.reflect(dst, only=sorted(expected))
        proofs = []
        for name in sorted(expected):
            table, destination = source_meta.tables[name], target_meta.tables[name]
            proof = _proof(src, table, batch_size=batch_size)
            for batch in _rows(src, table, batch_size=batch_size):
                dst.execute(destination.insert(), [dict(row) for row in batch])
            if _proof(dst, destination, batch_size=batch_size) != proof:
                raise DatabaseTransferError("database_transfer_verification_failed")
            proofs.append(proof)
        if dst.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise DatabaseTransferError("database_transfer_source_foreign_keys_invalid")
        head = src.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        dst.execute(
            text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        dst.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:head)"),
            {"head": head},
        )
        return TransferReport(False, tuple(proofs))
