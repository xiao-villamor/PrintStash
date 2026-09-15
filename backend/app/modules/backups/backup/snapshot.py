"""Consistent database snapshots and their temporary engine lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlmodel import Session, create_engine

import app.modules.backups.backup.archive_format as _archive_format_module
import app.modules.backups.backup.contracts as _contracts_module
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_engine, get_session_factory
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_utils import ownership_snapshot

logger = get_logger(__name__)


def database_backup_capability() -> _contracts_module.DatabaseBackupCapability:
    """Describe the integrated database snapshot contract without exposing its URL."""
    backend = make_url(settings.db_url).get_backend_name()
    supported = backend == "postgresql" or (
        backend == "sqlite" and _db_path() is not None
    )
    return _contracts_module.DatabaseBackupCapability(
        database_backend=backend,
        create_supported=supported,
        restore_supported=supported,
    )


def _require_database_backup_support(*, restore: bool = False) -> None:
    capability = database_backup_capability()
    supported = capability.restore_supported if restore else capability.create_supported
    if not supported:
        raise _contracts_module.DatabaseBackupNotSupportedError(
            "database_backup_not_supported"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path() -> Path | None:
    from app.core.config import _sqlite_db_path as resolve_db

    return resolve_db(settings.db_url)


def _database_snapshot_size() -> int:
    """Conservative durable DB size for admission before building a snapshot."""
    db_path = _db_path()
    if db_path is not None:
        wal = Path(str(db_path) + "-wal")
        return db_path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
    if make_url(settings.db_url).get_backend_name() == "postgresql":
        with get_session_factory().scoped_session() as session:
            size = session.execute(
                text(
                    "SELECT COALESCE(SUM(pg_total_relation_size(quote_ident(schemaname)||'.'||quote_ident(tablename))),0) FROM pg_tables WHERE schemaname=current_schema()"
                )
            ).scalar_one()
        return int(size) * 2
    return 0


def _validate_sqlite_snapshot(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise RuntimeError("sqlite_snapshot_integrity_check_failed")


@contextmanager
def _sqlite_snapshot_file() -> Iterator[Path]:
    """Yield a portable SQLite snapshot from either supported database engine."""
    db_path = _db_path()
    postgres = make_url(settings.db_url).get_backend_name() == "postgresql"
    if db_path is None and not postgres:
        raise RuntimeError("database is not a file-based SQLite database")
    if db_path is not None and not db_path.is_file():
        raise FileNotFoundError(db_path)

    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    fd, raw_name = tempfile.mkstemp(
        prefix=".printstash-db-snapshot-",
        suffix=".sqlite3",
        dir=settings.backup_dir,
    )
    os.close(fd)
    snapshot_path = Path(raw_name)
    try:
        if postgres:
            from app.modules.administration.database_transfer import snapshot_postgres

            destination = create_engine(
                f"sqlite:///{snapshot_path}", hide_parameters=True
            )
            try:
                with get_session_factory().scoped_session() as session:
                    source = session.get_bind()
                snapshot_postgres(source, destination)
            finally:
                destination.dispose()
            _validate_sqlite_snapshot(snapshot_path)
            yield snapshot_path
            return
        with (
            sqlite3.connect(db_path, timeout=30) as source,
            sqlite3.connect(snapshot_path, timeout=30) as destination,
        ):
            source.execute("PRAGMA query_only=ON")
            source.backup(destination)
        _validate_sqlite_snapshot(snapshot_path)
        yield snapshot_path
    finally:
        snapshot_path.unlink(missing_ok=True)


def _backup_sqlite_copy() -> bytes:
    """Compatibility helper; production backup streams the snapshot file."""
    with _sqlite_snapshot_file() as snapshot_path:
        return snapshot_path.read_bytes()


def _find_blobs(session: Session | None = None) -> list[tuple[str, int]]:
    """Return ``(key, size_bytes)`` for every irreplaceable vault-owned blob.

    One ``stat_size`` per key doubles as the existence check (it raises when the
    key is gone), and surfacing the size lets ``create_backup`` build the
    manifest *before* streaming the file bodies. Linked external Artifacts are
    indexed by the vault but user-owned, so their paths must never be read into
    a backup archive.
    """
    if session is None:
        with get_session_factory().session() as owned_session:
            return _find_blobs(owned_session)

    snapshot = ownership_snapshot(session, discover=False)
    keys = sorted({blob.key for blob in [*snapshot.primary, *snapshot.embedded]})
    backend = get_backend()
    out: list[tuple[str, int]] = []
    for key in keys:
        _archive_format_module._validate_restore_key(key)
        # A backup cannot be called complete if a DB-owned blob is absent or
        # unreadable. Surface failure instead of silently shrinking archive.
        out.append((key, backend.stat_size(key)))
    return out


def _find_snapshot_blobs(snapshot_path: Path) -> list[tuple[str, int]]:
    """Read blob ownership from the same DB snapshot archived in the backup."""
    engine = create_engine(f"sqlite:///{snapshot_path}")
    try:
        with Session(engine) as session:
            return _find_blobs(session)
    finally:
        engine.dispose()


def _manifest_blobs(snapshot_path: Path) -> list[dict[str, str | int]]:
    """Build v3 evidence from the same DB snapshot archived in the backup."""
    engine = create_engine(f"sqlite:///{snapshot_path}")
    try:
        with Session(engine) as session:
            snapshot = ownership_snapshot(session, discover=False)
            external_keys = {blob.key for blob in snapshot.external}
            required_keys = {
                blob.key for blob in [*snapshot.primary, *snapshot.embedded]
            }
            rebuildable_keys = {
                blob.key for blob in snapshot.derived if blob.key not in required_keys
            }
            blobs = [
                *[blob for blob in snapshot.primary if blob.key not in external_keys],
                *snapshot.derived,
                *snapshot.embedded,
            ]
            backend = get_backend()
            provider_id = str(getattr(backend, "provider_id", backend.backend_name))
            transport = str(getattr(backend, "transport", backend.backend_name))
            entries: list[dict[str, str | int]] = []
            seen: set[str] = set()
            for blob in blobs:
                if blob.key in seen:
                    continue
                seen.add(blob.key)
                _archive_format_module._validate_restore_key(blob.key)
                try:
                    size = backend.stat_size(blob.key)
                except FileNotFoundError:
                    # Every entry from ``snapshot.derived`` is a rebuildable
                    # projection, including immutable thumbnail generations
                    # and the superseded compatibility address. Classify by
                    # ownership group instead of maintaining a second list of
                    # resource-type strings that drifts when a derivative is
                    # added. A key also claimed by a primary/embedded resource
                    # remains mandatory.
                    if blob.key not in rebuildable_keys:
                        raise
                    continue
                digest = hashlib.sha256()
                for chunk in backend.stream_chunks(blob.key):
                    digest.update(chunk)
                namespace = backend.namespace_for(blob.key)
                member = f"files/{len(entries):08d}-{Path(blob.key).name}"
                entries.append(
                    {
                        "member": member,
                        "arc": member,
                        "key": blob.key,
                        # ``provider`` is retained for v2 readers.  The
                        # explicit provider/transport pair is the v3 identity
                        # and prevents a generic backend name from hiding a
                        # changed remote configuration.
                        "provider": backend.backend_name,
                        "provider_id": provider_id,
                        "transport": transport,
                        "namespace": namespace,
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )
            return entries
    finally:
        engine.dispose()


def _add_file_to_tar(tar: tarfile.TarFile, key: str, arcname: str) -> int:
    # local_path() yields the real file locally, or a self-cleaning temp
    # download for remote backends — no branching on backend type.
    with get_backend().local_path(key) as path:
        tar.add(str(path), arcname=arcname)
        return path.stat().st_size


def _validate_created_archive_payload(archive_path: Path) -> None:
    """Prove the completed archive contains the exact v2 manifest bytes."""
    with tarfile.open(archive_path, mode="r:gz") as archive:
        manifests = archive.getmembers()
        manifest_members = [
            member
            for member in manifests
            if member.name == "manifest.json" and member.isfile()
        ]
        if len(manifest_members) != 1:
            raise RuntimeError("backup_manifest_invalid")
        stream = archive.extractfile(manifest_members[0])
        if stream is None:
            raise RuntimeError("backup_manifest_invalid")
        manifest = json.loads(stream.read().decode("utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("version") != _contracts_module.MANIFEST_VERSION
        ):
            raise RuntimeError("backup_manifest_invalid")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise RuntimeError("backup_manifest_invalid")
        by_name: dict[str, list[tarfile.TarInfo]] = {}
        for member in manifests:
            by_name.setdefault(member.name, []).append(member)
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("backup_manifest_invalid")
            member_name = entry.get("member")
            expected_size = entry.get("size")
            expected_sha256 = entry.get("sha256")
            if (
                not isinstance(member_name, str)
                or not isinstance(expected_size, int)
                or not isinstance(expected_sha256, str)
            ):
                raise RuntimeError("backup_manifest_invalid")
            members = by_name.get(member_name, [])
            if len(members) != 1 or not members[0].isfile():
                raise RuntimeError("backup_manifest_invalid")
            if members[0].size != expected_size:
                raise RuntimeError("backup_blob_size_changed")
            source = archive.extractfile(members[0])
            if source is None:
                raise RuntimeError("backup_manifest_invalid")
            digest = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
            if digest.hexdigest() != expected_sha256:
                raise RuntimeError("backup_blob_hash_changed")


def _dispose_session_engine() -> None:
    factory = get_session_factory()
    dispose = getattr(factory, "dispose", None)
    if callable(dispose):
        dispose()
    else:  # Compatibility for third-party SessionFactory implementations.
        get_engine().dispose()


def _restore_database_from_path(source_path: Path) -> None:
    db_path = _db_path()
    postgres = make_url(settings.db_url).get_backend_name() == "postgresql"
    if db_path is None and not postgres:
        raise RuntimeError("cannot restore to non-file database")
    _validate_sqlite_snapshot(source_path)

    # Close idle pooled connections before and after the online copy. Checked
    # out read connections remain safe: SQLite coordinates them with the backup
    # transaction instead of replaying a stale sidecar over a raw file swap.
    _dispose_session_engine()
    try:
        if postgres:
            from app.modules.administration.database_transfer import restore_postgres

            source = create_engine(f"sqlite:///{source_path}", hide_parameters=True)
            try:
                with get_session_factory().scoped_session() as session:
                    target = session.get_bind()
                restore_postgres(source, target)
            finally:
                source.dispose()
            return
        with (
            sqlite3.connect(source_path, timeout=30) as source,
            sqlite3.connect(db_path, timeout=30) as destination,
        ):
            source.execute("PRAGMA query_only=ON")
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("restored_database_integrity_check_failed")
    finally:
        _dispose_session_engine()


def _restore_database(db_data: bytes) -> None:
    """Compatibility wrapper for callers/tests that still provide bytes."""
    if (
        _db_path() is None
        and make_url(settings.db_url).get_backend_name() != "postgresql"
    ):
        raise RuntimeError("cannot restore to non-file database")
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    fd, raw_name = tempfile.mkstemp(
        prefix=".printstash-restore-db-",
        suffix=".sqlite3",
        dir=settings.backup_dir,
    )
    os.close(fd)
    source_path = Path(raw_name)
    try:
        source_path.write_bytes(db_data)
        _restore_database_from_path(source_path)
    finally:
        source_path.unlink(missing_ok=True)
