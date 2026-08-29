"""Vault backup & restore service.

Creates tar.gz snapshots of the SQLite database and all stored files.
Backups are always written locally first, then optionally uploaded to a
separate S3/R2 bucket for off-site durability.

The backup S3 destination is independent from vault S3 storage — this
allows a "local vault + cloud backup" split architecture.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, ParamSpec, TypeVar

from sqlalchemy.engine import URL
from sqlalchemy.engine.url import make_url
from sqlmodel import Session, create_engine, delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.migrate import run_migrations
from app.db.models import (
    OwnedStorageObject,
    RestoreMarker,
    StagingLease,
    StorageObjectState,
    User,
)
from app.db.session import get_engine, get_session_factory
from app.services import audit
from app.services.jobs import registry
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    get_backend,
)
from app.services.storage_ownership import (
    complete_publication,
    delete_owned_key,
    publish_file,
    record_creation,
    require_owned_key,
)
from app.services.storage_utils import ownership_snapshot

logger = get_logger(__name__)

# ponytail: process-wide gate, single-process/single-worker only. A
# multi-worker deployment needs a DB-backed lock instead of this in-memory
# Event — not built here.
_restore_gate = threading.Event()
_RESTORE_GRACE_PERIOD_S = 2.0
_RESTORE_DRAIN_TIMEOUT_S = 30.0
_backup_restore_lock = threading.RLock()
_mutation_condition = threading.Condition()
_active_mutations = 0
_P = ParamSpec("_P")
_R = TypeVar("_R")


class RestoreConflictError(Exception):
    """Raised when a restore is refused because ingestion work is in flight."""


class DatabaseBackupNotSupportedError(RuntimeError):
    """Raised when the configured database has no integrated snapshot adapter."""


class BackupOwnershipError(RuntimeError):
    """A backup target lacks current operation-level ownership proof."""


@dataclass(frozen=True)
class DatabaseBackupCapability:
    database_backend: str
    create_supported: bool
    restore_supported: bool


def database_backup_capability() -> DatabaseBackupCapability:
    """Describe the integrated database snapshot contract without exposing its URL."""
    backend = make_url(settings.db_url).get_backend_name()
    supported = backend == "sqlite" and _db_path() is not None
    return DatabaseBackupCapability(
        database_backend=backend,
        create_supported=supported,
        restore_supported=supported,
    )


def _require_database_backup_support(*, restore: bool = False) -> None:
    capability = database_backup_capability()
    supported = capability.restore_supported if restore else capability.create_supported
    if not supported:
        raise DatabaseBackupNotSupportedError("database_backup_not_supported")


def restore_in_progress() -> bool:
    return _restore_gate.is_set()


def inspect_restore_recovery() -> bool:
    """Put the process in restore maintenance when a restore was interrupted.

    A sidecar journal is deliberately treated as evidence of an unfinished
    operation across process restarts.  In particular, a database-swap intent
    cannot be safely inferred to be pre- or post-swap without querying the
    marker in the active database.  Keeping the gate set makes reads and
    operator recovery available while preventing background mutation work.
    """
    try:
        journals = sorted(settings.backup_dir.glob(".restore-*.journal"))
    except OSError:
        journals = []
    if not journals:
        return False

    # Even a pre-PONR journal represents staged ownership and an operation
    # whose cleanup has not been proven. Gate mutations until the restore is
    # explicitly resumed or the journal is resolved.
    unresolved = bool(journals)
    for path in journals:
        try:
            state = _load_restore_journal(path)
        except Exception:
            unresolved = True
            continue
        if state.database_swap_intent or state.database_active:
            unresolved = True
            # A marker query failure is intentionally indistinguishable from
            # an active marker here: both require administrator recovery.
            if state.database_swap_intent:
                _active_restore_marker(
                    str(state.started.get("backup_id", "")),
                    operation_nonce=state.started.get("operation_nonce"),
                    archive_sha256=state.started.get("archive_sha256"),
                )
    if unresolved:
        with _mutation_condition:
            _restore_gate.set()
    return unresolved


def unresolved_restore_backup_id() -> str | None:
    """Return the only journaled backup allowed to resume recovery.

    ``None`` is also returned for an invalid/ambiguous journal.  Callers must
    fail closed in that case rather than allowing a new restore to bypass the
    unresolved operation.
    """
    try:
        journals = sorted(settings.backup_dir.glob(".restore-*.journal"))
    except OSError:
        return None
    if len(journals) != 1:
        return None
    path = journals[0]
    # The filename is the operation's durable routing identity.  A journal can
    # be corrupt, or can contain a tampered ``backup_id``; either case must be
    # routed to the operation named by the file so its normal parser returns a
    # precise invalid/mismatch conflict rather than the generic recovery gate.
    prefix, suffix = ".restore-", ".journal"
    name = path.name
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    backup_id = name[len(prefix) : -len(suffix)]
    return backup_id or None


def _restore_journal_pending() -> bool:
    """Return whether any restore evidence still requires maintenance.

    The sidecar is the durable source of truth for the process gate.  A local
    boolean is not enough: a retry can fail before resolving an older journal,
    and clearing the gate in that case would let unrelated writes race the
    unresolved restore on the next request.
    """
    try:
        return any(settings.backup_dir.glob(".restore-*.journal"))
    except OSError:
        # An unreadable backup directory cannot prove resolution. Fail closed.
        return True


def begin_mutating_operation() -> bool:
    """Register a write-capable operation unless restore maintenance is active."""
    global _active_mutations
    with _mutation_condition:
        if _restore_gate.is_set():
            return False
        _active_mutations += 1
        return True


def end_mutating_operation() -> None:
    global _active_mutations
    with _mutation_condition:
        if _active_mutations <= 0:
            raise RuntimeError("unbalanced_mutating_operation")
        _active_mutations -= 1
        if _active_mutations == 0:
            _mutation_condition.notify_all()


def _begin_restore_maintenance() -> None:
    """Block new mutations and wait for already-admitted ones to drain."""
    deadline = time.monotonic() + _RESTORE_DRAIN_TIMEOUT_S
    with _mutation_condition:
        _restore_gate.set()
        while _active_mutations:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _restore_gate.clear()
                _mutation_condition.notify_all()
                raise RestoreConflictError(
                    f"{_active_mutations} write operation(s) still active; retry later"
                )
            _mutation_condition.wait(timeout=remaining)


def _end_restore_maintenance() -> None:
    with _mutation_condition:
        _restore_gate.clear()
        _mutation_condition.notify_all()


def _exclusive_backup_operation(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Prevent overlapping backup/restore operations in this process."""

    @wraps(func)
    def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _backup_restore_lock:
            return func(*args, **kwargs)

    return serialized


MANIFEST_VERSION = "3"
_LEGACY_MANIFEST_V2 = "2"
_SUPPORTED_MANIFEST_VERSIONS = {"1", _LEGACY_MANIFEST_V2, MANIFEST_VERSION}
_RESTORE_JOURNAL_VERSION = 2
_BACKUP_S3_PREFIX = "printstash-backups/"
_LEGACY_BACKUP_S3_PREFIX = "nexus3d-backups/"
_BACKUP_NAME_PREFIX = "printstash-backup-"
_LEGACY_BACKUP_NAME_PREFIX = "nexus3d-backup-"


@dataclass
class BackupMeta:
    id: str
    created_at: str
    size_bytes: int
    storage_backend: str
    file_count: int
    app_version: str
    path: str  # local path to the tar.gz, or S3 key if cloud-only
    location: str = "local"  # "local" | "s3"


@dataclass
class BackupVerification:
    backup_id: str
    valid: bool
    app_compatible: bool
    manifest_version: str | None
    checked_members: int
    findings: list[dict[str, str | int]]


# ---------------------------------------------------------------------------
# S3 client for backup operations (independent from vault S3)
# ---------------------------------------------------------------------------

_backup_s3: object | None = None


def _get_backup_s3():
    """Return a boto3 S3 client for the backup bucket, or None if not configured."""
    global _backup_s3
    if _backup_s3 is not None:
        return _backup_s3

    if not settings.backup_s3_bucket:
        _backup_s3 = False  # sentinel: configured but not available
        return None

    try:
        import boto3
        from botocore.config import Config as BotoConfig

        kwargs: dict = {
            "service_name": "s3",
            "region_name": settings.backup_s3_region or "auto",
            "aws_access_key_id": settings.backup_s3_access_key or None,
            "aws_secret_access_key": settings.backup_s3_secret_key or None,
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        }
        if settings.backup_s3_endpoint_url:
            kwargs["endpoint_url"] = settings.backup_s3_endpoint_url

        _backup_s3 = boto3.client(**kwargs)
        logger.info(
            "backup: S3 client initialised for bucket %s", settings.backup_s3_bucket
        )
        return _backup_s3
    except Exception:
        logger.warning("backup: failed to initialise S3 client", exc_info=True)
        _backup_s3 = False
        return None


def _backup_s3_key(archive_name: str) -> str:
    return f"{_BACKUP_S3_PREFIX}{archive_name}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path() -> Path | None:
    from app.core.config import _sqlite_db_path as resolve_db

    return resolve_db(settings.db_url)


def _validate_sqlite_snapshot(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise RuntimeError("sqlite_snapshot_integrity_check_failed")


@contextmanager
def _sqlite_snapshot_file() -> Iterator[Path]:
    """Yield a self-cleaning, transactionally consistent SQLite snapshot."""
    db_path = _db_path()
    if db_path is None:
        raise RuntimeError("database is not a file-based SQLite database")
    if not db_path.is_file():
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
        _validate_restore_key(key)
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
                _validate_restore_key(blob.key)
                try:
                    size = backend.stat_size(blob.key)
                except FileNotFoundError:
                    # Thumbnails and caches are rebuildable projections; an
                    # absent one must not make an otherwise complete backup
                    # impossible. Primary/source-cover bytes remain mandatory.
                    if blob.resource_type not in {
                        "thumbnail",
                        "legacy_thumbnail",
                        "stl_cache",
                    }:
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
            or manifest.get("version") != MANIFEST_VERSION
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


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@_exclusive_backup_operation
def create_backup() -> BackupMeta:
    """Create a full vault backup: DB + all stored files as a tar.gz.

    Always writes locally first. If ``backup_s3_bucket`` is configured,
    the archive is also uploaded to the S3 bucket.
    """
    _require_database_backup_support()
    backup_id = uuid.uuid4().hex[:12]
    timestamp = utcnow()
    ts = timestamp.isoformat()

    archive_name = (
        f"{_BACKUP_NAME_PREFIX}{timestamp.strftime('%Y%m%d-%H%M%S')}-{backup_id}.tar.gz"
    )
    archive_path = settings.backup_dir / archive_name
    backend_name = settings.storage_backend

    written_files = 0
    with _sqlite_snapshot_file() as db_snapshot:
        censused_sizes = dict(_find_snapshot_blobs(db_snapshot))
        file_entries = _manifest_blobs(db_snapshot)
        for entry in file_entries:
            key = str(entry["key"])
            if key in censused_sizes and int(entry["size"]) != censused_sizes[key]:
                logger.error("backup %s failed while streaming owned blobs", backup_id)
                raise RuntimeError("backup_blob_size_changed")
        total_size = db_snapshot.stat().st_size + sum(
            int(entry["size"]) for entry in file_entries
        )
        manifest = {
            "version": MANIFEST_VERSION,
            "created_at": ts,
            "app_version": settings.app_version,
            "storage_backend": backend_name,
            "provider_id": str(
                getattr(get_backend(), "provider_id", get_backend().backend_name)
            ),
            "transport": str(
                getattr(get_backend(), "transport", get_backend().backend_name)
            ),
            "namespace": (str(file_entries[0]["namespace"]) if file_entries else None),
            "namespaces": sorted({str(entry["namespace"]) for entry in file_entries}),
            "file_count": len(file_entries),
            "total_size_bytes": total_size,
            "files": file_entries,
        }
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_archive_temp = tempfile.mkstemp(
            prefix=".printstash-backup-build-", dir=settings.backup_dir
        )
        archive_temp = Path(raw_archive_temp)
        try:
            with os.fdopen(fd, "wb") as archive_file:
                with gzip.GzipFile(fileobj=archive_file, mode="wb") as gz:
                    with tarfile.open(fileobj=gz, mode="w|") as tar:
                        man_info = tarfile.TarInfo(name="manifest.json")
                        man_info.size = len(manifest_bytes)
                        tar.addfile(man_info, io.BytesIO(manifest_bytes))

                        # tarfile streams this file; the database is never loaded
                        # as one in-memory bytes object.
                        tar.add(db_snapshot, arcname="db.sqlite3", recursive=False)

                        for entry in file_entries:
                            key = str(entry["key"])
                            arc = str(entry["member"])
                            written = _add_file_to_tar(tar, key, arc)
                            expected = int(entry["size"])
                            if written != expected:
                                raise RuntimeError("backup_blob_size_changed")
                            written_files += 1
            _validate_created_archive_payload(archive_temp)
            # Reserve and commit the local archive through the ownership ledger
            # before it becomes listable. A crash leaves a pending reservation
            # for reconciliation instead of a phantom backup.
            with get_session_factory().session() as publish_session:
                local_receipt = publish_file(
                    publish_session,
                    LocalStorageBackend(),
                    str(archive_path),
                    archive_temp,
                    object_kind="backup",
                    move=True,
                )
                publish_session.commit()
        except Exception:
            archive_temp.unlink(missing_ok=True)
            logger.exception("backup %s failed while streaming owned blobs", backup_id)
            raise

    final_size = local_receipt.size
    archive_sha256 = _sha256_path(archive_path)

    logger.info(
        "backup %s created locally: %d files, %.1f MiB",
        backup_id,
        written_files,
        final_size / (1024 * 1024),
    )

    # Upload to S3 if configured
    s3 = _get_backup_s3()
    if s3:
        try:
            s3_key = _backup_s3_key(archive_name)
            namespace = f"{settings.backup_s3_bucket}/{_BACKUP_S3_PREFIX}"
            token = uuid.uuid4().hex
            with get_session_factory().session() as reservation_session:
                reservation = OwnedStorageObject(
                    backend="backup-s3",
                    namespace=namespace,
                    key=s3_key,
                    object_kind="backup",
                    state=StorageObjectState.PENDING,
                    size_bytes=final_size,
                    sha256=archive_sha256,
                    token=token,
                )
                reservation_session.add(reservation)
                reservation_session.commit()
            with archive_path.open("rb") as source:
                response = s3.put_object(
                    Bucket=settings.backup_s3_bucket,
                    Key=s3_key,
                    Body=source,
                    IfNoneMatch="*",
                    Metadata={"printstash-create-token": token},
                )
            s3_receipt = CreationReceipt(
                key=s3_key,
                size=final_size,
                token=token,
                backend="backup-s3",
                namespace=namespace,
                etag=str(response.get("ETag")) if response.get("ETag") else None,
            )
            with get_session_factory().session() as commit_session:
                record_creation(commit_session, s3_receipt, object_kind="backup")
                commit_session.commit()
            logger.info("backup %s uploaded to S3: %s", backup_id, s3_key)
        except Exception:
            logger.warning("backup %s: S3 upload failed", backup_id, exc_info=True)

    # The destination ledger rows were committed immediately after each
    # publication. Keep this audit transaction separate from ownership state.
    with get_session_factory().session() as session:
        audit.record(
            session,
            action="backup.create",
            resource_type="backup",
            diff={
                "backup_id": backup_id,
                "size_bytes": final_size,
                "file_count": written_files,
            },
        )
        session.commit()

    return BackupMeta(
        id=backup_id,
        created_at=ts,
        size_bytes=final_size,
        storage_backend=backend_name,
        file_count=len(file_entries),
        app_version=settings.app_version,
        path=str(archive_path),
        location="local",
    )


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


def reconcile_backup_publications(limit: int = 100) -> int:
    """Finish or block backup reservations left across a publication crash."""
    reconciled = 0
    with get_session_factory().session() as session:
        pending = session.exec(
            select(OwnedStorageObject)
            .where(
                OwnedStorageObject.object_kind == "backup",
                OwnedStorageObject.state == StorageObjectState.PENDING,
            )
            .order_by(OwnedStorageObject.id.asc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
        s3 = _get_backup_s3()
        for row in pending:
            receipt: CreationReceipt | None = None
            try:
                if row.backend == "local":
                    if row.size_bytes is None or row.sha256 is None:
                        raise RuntimeError("backup_publication_evidence_missing")
                    receipt = LocalStorageBackend().adopt_existing(
                        row.key,
                        expected_size=row.size_bytes,
                        expected_sha256=row.sha256,
                    )
                elif row.backend == "backup-s3" and s3 is not None:
                    response = s3.head_object(
                        Bucket=settings.backup_s3_bucket, Key=row.key
                    )
                    metadata = response.get("Metadata", {})
                    if (
                        not row.token
                        or metadata.get("printstash-create-token") != row.token
                        or int(response.get("ContentLength", -1)) != row.size_bytes
                    ):
                        raise RuntimeError("backup_publication_evidence_mismatch")
                    # HEAD supplies only identity/size metadata.  It has no
                    # response body, so fetch the exact object separately for
                    # the digest and archive validation proof.
                    get_kwargs: dict[str, str] = {
                        "Bucket": settings.backup_s3_bucket,
                        "Key": row.key,
                    }
                    if response.get("VersionId"):
                        get_kwargs["VersionId"] = str(response["VersionId"])
                    object_response = s3.get_object(**get_kwargs)
                    fd, raw_name = tempfile.mkstemp(
                        prefix=".printstash-backup-reconcile-",
                        dir=settings.backup_dir,
                    )
                    os.close(fd)
                    candidate = Path(raw_name)
                    try:
                        digest = hashlib.sha256()
                        body = object_response["Body"]
                        try:
                            with candidate.open("wb") as output:
                                while chunk := body.read(1024 * 1024):
                                    digest.update(chunk)
                                    output.write(chunk)
                        finally:
                            body.close()
                        if row.sha256 and digest.hexdigest() != row.sha256:
                            raise RuntimeError("backup_publication_digest_mismatch")
                        _validate_created_archive_payload(candidate)
                    finally:
                        candidate.unlink(missing_ok=True)
                    receipt = CreationReceipt(
                        key=row.key,
                        size=int(response["ContentLength"]),
                        token=row.token,
                        backend="backup-s3",
                        namespace=row.namespace,
                        etag=str(response.get("ETag"))
                        if response.get("ETag")
                        else None,
                        version_id=(
                            str(response.get("VersionId"))
                            if response.get("VersionId")
                            else None
                        ),
                    )
                else:
                    raise RuntimeError("backup_publication_backend_unavailable")
                assert receipt is not None
                complete_publication(
                    session,
                    int(row.id),
                    receipt,
                    object_kind="backup",
                    sha256=row.sha256,
                )
                reconciled += 1
            except RuntimeError as exc:
                row.state = StorageObjectState.BLOCKED
                row.last_error = type(exc).__name__[:255]
                session.add(row)
            except Exception as exc:
                # A provider outage is retryable; do not turn an unavailable
                # S3 endpoint into a permanent operator decision.
                row.last_error = f"retryable:{type(exc).__name__}"[:255]
                session.add(row)
        session.commit()
    return reconciled


def _committed_backup_keys(backend: str, namespace: str | None = None) -> set[str]:
    """Return only archives whose publication reached COMMITTED in the ledger."""
    with get_session_factory().session() as session:
        statement = select(OwnedStorageObject.key).where(
            OwnedStorageObject.backend == backend,
            OwnedStorageObject.object_kind == "backup",
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
        if namespace is not None:
            statement = statement.where(OwnedStorageObject.namespace == namespace)
        return {str(key) for key in session.exec(statement).all()}


def _list_local_backups() -> list[BackupMeta]:
    results: list[BackupMeta] = []
    if not settings.backup_dir.exists():
        return results
    committed = _committed_backup_keys("local")

    for archive in sorted(
        [
            *settings.backup_dir.glob(f"{_BACKUP_NAME_PREFIX}*.tar.gz"),
            *settings.backup_dir.glob(f"{_LEGACY_BACKUP_NAME_PREFIX}*.tar.gz"),
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        if str(archive) not in committed:
            # A visible archive can be left by a crash between publication and
            # its ownership commit; it is not a listable backup yet.
            continue
        try:
            meta = _read_manifest(archive)
            if meta is not None:
                meta.location = "local"
                meta.path = str(archive)
                results.append(meta)
        except Exception:
            logger.warning("backup: cannot read manifest from %s", archive.name)

    return results


def _list_s3_backups() -> list[BackupMeta]:
    s3 = _get_backup_s3()
    if not s3:
        return []

    results: list[BackupMeta] = []
    committed = {
        *_committed_backup_keys(
            "backup-s3", f"{settings.backup_s3_bucket}/{_BACKUP_S3_PREFIX}"
        ),
        *_committed_backup_keys(
            "backup-s3", f"{settings.backup_s3_bucket}/{_LEGACY_BACKUP_S3_PREFIX}"
        ),
    }
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for prefix in (_BACKUP_S3_PREFIX, _LEGACY_BACKUP_S3_PREFIX):
            for page in paginator.paginate(
                Bucket=settings.backup_s3_bucket, Prefix=prefix
            ):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key not in committed:
                        continue
                    archive_name = key.rsplit("/", 1)[-1]
                    if not archive_name.startswith(
                        (_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX)
                    ):
                        continue
                    try:
                        # Read manifest from S3 without downloading full archive
                        resp = s3.get_object(Bucket=settings.backup_s3_bucket, Key=key)
                        with gzip.open(resp["Body"], "rb") as gz:
                            with tarfile.open(fileobj=gz, mode="r|") as tar:
                                for member in tar:
                                    if member.name == "manifest.json":
                                        f = tar.extractfile(member)
                                        if f:
                                            manifest = json.loads(
                                                f.read().decode("utf-8")
                                            )
                                            backup_id = archive_name.rsplit("-", 1)[
                                                -1
                                            ].replace(".tar.gz", "")
                                            results.append(
                                                BackupMeta(
                                                    id=backup_id,
                                                    created_at=manifest["created_at"],
                                                    size_bytes=obj.get("Size", 0),
                                                    storage_backend=manifest.get(
                                                        "storage_backend", "local"
                                                    ),
                                                    file_count=manifest.get(
                                                        "file_count", 0
                                                    ),
                                                    app_version=manifest.get(
                                                        "app_version", "unknown"
                                                    ),
                                                    path=key,
                                                    location="s3",
                                                )
                                            )
                                            break
                    except Exception:
                        logger.warning("backup: cannot read S3 manifest for %s", key)
                        continue
    except Exception:
        logger.warning("backup: failed to list S3 backups", exc_info=True)

    return results


def list_backups() -> list[BackupMeta]:
    """List all backups: local + S3, sorted by date descending."""
    reconcile_backup_publications()
    local = _list_local_backups()
    s3 = _list_s3_backups()
    # Merge, dedup by ID (local wins if same ID exists in both)
    seen: set[str] = set()
    merged: list[BackupMeta] = []
    for m in local:
        seen.add(m.id)
        merged.append(m)
    for m in s3:
        if m.id not in seen:
            seen.add(m.id)
            merged.append(m)
    merged.sort(key=lambda m: m.created_at, reverse=True)
    return merged


def _read_manifest(archive_path: Path) -> BackupMeta | None:
    with gzip.open(archive_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            for member in tar:
                if member.name == "manifest.json":
                    f = tar.extractfile(member)
                    if f is None:
                        return None
                    manifest = json.loads(f.read().decode("utf-8"))
                    return BackupMeta(
                        # ``.stem`` only strips ``.gz`` from ``*.tar.gz`` and
                        # would leave a ``.tar`` suffix on the id, so the id here
                        # would not match the one ``create_backup`` returns.
                        id=archive_path.name.removesuffix(".tar.gz").rsplit("-", 1)[-1],
                        created_at=manifest["created_at"],
                        size_bytes=archive_path.stat().st_size,
                        storage_backend=manifest.get("storage_backend", "local"),
                        file_count=manifest.get("file_count", 0),
                        app_version=manifest.get("app_version", "unknown"),
                        path=str(archive_path),
                        location="local",
                    )
    return None


def _validate_archive_for_adoption(archive_path: Path) -> BackupMeta:
    """Validate an unowned local archive before making it listable.

    Legacy archives are intentionally not auto-adopted during listing: an
    administrator must identify the exact file.  The same manifest, storage
    namespace, member, hash, and SQLite checks used by restore run before a
    durable ownership row is created.
    """
    meta = _read_manifest(archive_path)
    if meta is None:
        raise RuntimeError("backup_manifest_invalid")
    with tarfile.open(archive_path, mode="r:gz") as tar:
        members = tar.getmembers()
        if any(
            _unsafe_member_name(member.name) or member.issym() or member.islnk()
            for member in members
        ):
            raise RuntimeError("backup_manifest_invalid")
        _restore_manifest_entries(tar)
        db_members = [member for member in members if member.name == "db.sqlite3"]
        if len(db_members) != 1 or not db_members[0].isfile():
            raise RuntimeError("backup_member_missing:db.sqlite3")
        # Validate the archived DB without trusting the current database.
        fd, raw_db = tempfile.mkstemp(prefix=".printstash-adopt-db-")
        os.close(fd)
        db_path = Path(raw_db)
        try:
            stream = tar.extractfile(db_members[0])
            if stream is None:
                raise RuntimeError("backup_member_missing:db.sqlite3")
            with db_path.open("wb") as destination:
                shutil.copyfileobj(stream, destination)
            _validate_sqlite_snapshot(db_path)
        finally:
            db_path.unlink(missing_ok=True)
    return meta


def adopt_local_backup(filename: str) -> BackupMeta:
    """Explicitly adopt one validated legacy archive into the ownership ledger."""
    if not filename or Path(filename).name != filename:
        raise ValueError("backup_filename_invalid")
    if not filename.startswith((_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX)):
        raise ValueError("backup_filename_invalid")
    root = settings.backup_dir.expanduser().resolve(strict=False)
    archive = (root / filename).resolve(strict=False)
    if archive.parent != root or not archive.is_file():
        raise FileNotFoundError(filename)
    meta = _validate_archive_for_adoption(archive)
    backend = LocalStorageBackend()
    digest = _sha256_path(archive)
    receipt = backend.adopt_existing(
        str(archive), expected_size=archive.stat().st_size, expected_sha256=digest
    )
    with get_session_factory().session() as session:
        record_creation(session, receipt, object_kind="backup", sha256=digest)
        session.commit()
    meta.path = str(archive)
    meta.location = "local"
    return meta


def discover_unowned_local_backups() -> list[dict[str, object]]:
    """Describe valid legacy archives awaiting explicit administrator adoption.

    Normal listing remains ownership-only.  This bounded operator view exposes
    only archives that pass the complete manifest, namespace, member, and
    SQLite validation used by adoption; malformed candidates are logged but
    never returned as actionable backups.
    """
    root = settings.backup_dir.expanduser().resolve(strict=False)
    if not root.is_dir():
        return []
    committed = _committed_backup_keys("local")
    candidates: list[dict[str, object]] = []
    for archive in sorted(root.glob("*.tar.gz")):
        if str(archive) in committed or not archive.name.startswith(
            (_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX)
        ):
            continue
        try:
            meta = _validate_archive_for_adoption(archive)
        except Exception:
            logger.info(
                "backup: unowned archive failed adoption validation: %s", archive
            )
            continue
        candidates.append(
            {
                "filename": archive.name,
                "backup_id": meta.id,
                "created_at": meta.created_at,
                "size_bytes": meta.size_bytes,
                "file_count": meta.file_count,
                "storage_backend": meta.storage_backend,
                "app_version": meta.app_version,
                "location": "local",
            }
        )
    return candidates


def get_backup(backup_id: str) -> BackupMeta | None:
    for meta in list_backups():
        if meta.id == backup_id:
            return meta
    return None


def get_backup_archive_path(backup_id: str) -> Path:
    """Return a local archive path, downloading cloud-only backups first."""
    meta = get_backup(backup_id)
    if meta is None:
        raise FileNotFoundError(f"backup {backup_id} not found")
    return _download_backup_to_local(meta)


def _require_backup_archive_owned(meta: BackupMeta) -> OwnedStorageObject:
    """Require current proof for the archive selected by restore/delete."""
    with get_session_factory().session() as session:
        if meta.location == "local":
            backend = LocalStorageBackend()
            try:
                require_owned_key(session, backend, meta.path)
            except Exception as exc:
                raise BackupOwnershipError(
                    "backup_storage_ownership_unverified"
                ) from exc
            row = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == meta.path)
            ).first()
            assert row is not None
            return row

        s3 = _get_backup_s3()
        if not s3:
            raise BackupOwnershipError("backup_storage_ownership_unverified")
        namespace = f"{settings.backup_s3_bucket}/{_BACKUP_S3_PREFIX}"
        candidates = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == "backup-s3",
                OwnedStorageObject.namespace == namespace,
                OwnedStorageObject.key == meta.path,
            )
        ).all()
        response = s3.head_object(Bucket=settings.backup_s3_bucket, Key=meta.path)
        for candidate in candidates:
            if (
                response.get("Metadata", {}).get("printstash-create-token")
                == candidate.token
                and int(response.get("ContentLength", -1)) == candidate.size_bytes
                and (
                    not candidate.etag
                    or str(response.get("ETag", "")) == candidate.etag
                )
            ):
                return candidate
    raise BackupOwnershipError("backup_storage_ownership_unverified")


def _unsafe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or not name or "\\" in name


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_key(key: str) -> str:
    digest = hashlib.sha256()
    for chunk in get_backend().stream_chunks(key):
        digest.update(chunk)
    return digest.hexdigest()


def verify_backup(backup_id: str) -> BackupVerification:
    """Validate archive structure, manifest membership, sizes, and safe paths."""
    archive = get_backup_archive_path(backup_id)
    findings: list[dict[str, str | int]] = []
    manifest: dict | None = None
    members: list[tarfile.TarInfo] = []
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                if _unsafe_member_name(member.name) or member.issym() or member.islnk():
                    findings.append(
                        {"code": "backup_manifest_invalid", "member": member.name[:255]}
                    )
            manifests = [member for member in members if member.name == "manifest.json"]
            if len(manifests) != 1:
                findings.append(
                    {"code": "backup_manifest_invalid", "member": "manifest.json"}
                )
            else:
                stream = tar.extractfile(manifests[0])
                try:
                    parsed = (
                        json.loads(stream.read().decode("utf-8")) if stream else None
                    )
                except (ValueError, UnicodeDecodeError):
                    parsed = None
                if isinstance(parsed, dict):
                    manifest = parsed
                else:
                    findings.append(
                        {"code": "backup_manifest_invalid", "member": "manifest.json"}
                    )
            if sum(member.name == "db.sqlite3" for member in members) != 1:
                findings.append(
                    {"code": "backup_member_missing", "member": "db.sqlite3"}
                )
            if manifest is not None:
                if manifest.get("version") == MANIFEST_VERSION and (
                    not isinstance(manifest.get("provider_id"), str)
                    or not isinstance(manifest.get("transport"), str)
                    or not isinstance(manifest.get("namespace"), (str, type(None)))
                    or not isinstance(manifest.get("namespaces"), list)
                    or any(
                        not isinstance(value, str) for value in manifest["namespaces"]
                    )
                ):
                    findings.append(
                        {"code": "backup_manifest_invalid", "member": "manifest.json"}
                    )
                expected_entries = manifest.get("files")
                if not isinstance(expected_entries, list):
                    findings.append(
                        {"code": "backup_manifest_invalid", "member": "files"}
                    )
                else:
                    by_name: dict[str, list[tarfile.TarInfo]] = {}
                    for member in members:
                        by_name.setdefault(member.name, []).append(member)
                    declared_members: set[str] = set()
                    for entry in expected_entries:
                        if not isinstance(entry, dict):
                            findings.append(
                                {"code": "backup_manifest_invalid", "member": "files"}
                            )
                            continue
                        arc = entry.get("member", entry.get("arc"))
                        if not isinstance(arc, str):
                            findings.append(
                                {"code": "backup_manifest_invalid", "member": "files"}
                            )
                            continue
                        if arc in declared_members:
                            findings.append(
                                {"code": "backup_manifest_invalid", "member": arc[:255]}
                            )
                            continue
                        declared_members.add(arc)
                        matches = by_name.get(arc, [])
                        if len(matches) != 1 or not matches[0].isfile():
                            findings.append(
                                {"code": "backup_member_missing", "member": arc[:255]}
                            )
                            continue
                        expected_size = entry.get("size")
                        if (
                            isinstance(expected_size, int)
                            and matches[0].size != expected_size
                        ):
                            findings.append(
                                {
                                    "code": "backup_member_size_mismatch",
                                    "member": arc[:255],
                                    "expected_size": expected_size,
                                    "actual_size": matches[0].size,
                                }
                            )
                        expected_sha = entry.get("sha256")
                        if str(manifest.get("version")) in {
                            _LEGACY_MANIFEST_V2,
                            MANIFEST_VERSION,
                        } and (
                            not isinstance(entry.get("key"), str)
                            or not isinstance(entry.get("namespace"), str)
                            or not isinstance(expected_size, int)
                            or not isinstance(expected_sha, str)
                            or (
                                str(manifest.get("version")) == _LEGACY_MANIFEST_V2
                                and not isinstance(entry.get("provider"), str)
                            )
                            or (
                                str(manifest.get("version")) == MANIFEST_VERSION
                                and (
                                    not isinstance(entry.get("provider_id"), str)
                                    or not isinstance(entry.get("transport"), str)
                                )
                            )
                        ):
                            findings.append(
                                {"code": "backup_manifest_invalid", "member": arc[:255]}
                            )
                            continue
                        if isinstance(expected_sha, str) and len(expected_sha) == 64:
                            stream = tar.extractfile(matches[0])
                            digest = hashlib.sha256()
                            if stream is not None:
                                while chunk := stream.read(1024 * 1024):
                                    digest.update(chunk)
                            if digest.hexdigest() != expected_sha.lower():
                                findings.append(
                                    {
                                        "code": "backup_member_hash_mismatch",
                                        "member": arc[:255],
                                    }
                                )
                    if str(manifest.get("version")) in {
                        _LEGACY_MANIFEST_V2,
                        MANIFEST_VERSION,
                    }:
                        archived_regular_files = {
                            member.name
                            for member in members
                            if member.isfile() and member.name.startswith("files/")
                        }
                        if archived_regular_files != declared_members:
                            findings.append(
                                {
                                    "code": "backup_manifest_invalid",
                                    "member": "files",
                                }
                            )
                        if manifest.get("file_count") != len(expected_entries):
                            findings.append(
                                {
                                    "code": "backup_manifest_invalid",
                                    "member": "file_count",
                                }
                            )
    except (tarfile.TarError, OSError, EOFError):
        findings.append({"code": "backup_manifest_invalid", "member": "archive"})

    manifest_version = str(manifest.get("version")) if manifest else None
    app_compatible = manifest_version in _SUPPORTED_MANIFEST_VERSIONS
    if manifest is not None and not app_compatible:
        findings.append({"code": "backup_manifest_invalid", "member": "version"})
    result = BackupVerification(
        backup_id=backup_id,
        valid=not findings,
        app_compatible=app_compatible,
        manifest_version=manifest_version,
        checked_members=len(members),
        findings=findings,
    )
    with get_session_factory().session() as session:
        audit.record(
            session,
            action="backup.verify",
            resource_type="backup",
            diff={
                "backup_id": backup_id,
                "valid": result.valid,
                "findings": len(findings),
            },
        )
    return result


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def delete_backup(backup_id: str) -> bool:
    """Delete a backup from both local and S3 storage."""
    meta = get_backup(backup_id)
    if meta is None:
        return False

    local_key = meta.path if meta.location == "local" else None
    s3 = _get_backup_s3()
    s3_key = next(
        (
            candidate.path
            for candidate in _list_s3_backups()
            if candidate.id == backup_id
        ),
        None,
    )

    deleted = False
    with get_session_factory().session() as session:
        local_backend = LocalStorageBackend()
        if local_key is not None:
            try:
                local_backend.verify_destructive_access([local_key])
                require_owned_key(session, local_backend, local_key)
            except Exception as exc:
                raise BackupOwnershipError(
                    "backup_storage_ownership_unverified"
                ) from exc

        s3_owned: OwnedStorageObject | None = None
        if s3 and s3_key:
            namespace = f"{settings.backup_s3_bucket}/{_BACKUP_S3_PREFIX}"
            candidates = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.backend == "backup-s3",
                    OwnedStorageObject.namespace == namespace,
                    OwnedStorageObject.key == s3_key,
                )
            ).all()
            for candidate in candidates:
                response = s3.head_object(Bucket=settings.backup_s3_bucket, Key=s3_key)
                if (
                    response.get("Metadata", {}).get("printstash-create-token")
                    == candidate.token
                    and int(response.get("ContentLength", -1)) == candidate.size_bytes
                    and (
                        not candidate.etag
                        or str(response.get("ETag", "")) == candidate.etag
                    )
                ):
                    s3_owned = candidate
                    break
            if s3_owned is None:
                raise BackupOwnershipError("backup_storage_ownership_unverified")

        # Every target was preflighted before the first delete. Late failures
        # leak the uncertain backup and retain its ledger row.
        if local_key is not None:
            deleted = delete_owned_key(session, local_backend, local_key) or deleted
        if s3 and s3_key and s3_owned is not None:
            try:
                kwargs = {"Bucket": settings.backup_s3_bucket, "Key": s3_key}
                if s3_owned.etag:
                    kwargs["IfMatch"] = s3_owned.etag
                s3.delete_object(**kwargs)
            except Exception:
                logger.exception("backup: failed to delete S3 %s", backup_id)
            else:
                session.delete(s3_owned)
                deleted = True
        session.commit()

    if deleted:
        logger.info("backup %s deleted", backup_id)
    return deleted


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def _download_backup_to_local(meta: BackupMeta) -> Path:
    """Ensure a local copy of the backup exists, downloading from S3 if needed."""
    local_path = Path(meta.path) if meta.location == "local" else None

    if local_path and local_path.exists():
        return local_path

    if meta.location == "s3":
        # Download from S3 to a temp location
        s3 = _get_backup_s3()
        if not s3:
            raise RuntimeError("backup is in S3 but no S3 client is available")

        owned = _require_backup_archive_owned(meta)
        archive_name = meta.path.rsplit("/", 1)[-1]
        local_path = settings.backup_dir / archive_name
        settings.backup_dir.mkdir(parents=True, exist_ok=True)

        fd, raw_temp = tempfile.mkstemp(
            prefix=".printstash-backup-download-", dir=settings.backup_dir
        )
        os.close(fd)
        download_temp = Path(raw_temp)
        try:
            kwargs = {"Bucket": settings.backup_s3_bucket, "Key": meta.path}
            if owned.etag:
                kwargs["IfMatch"] = owned.etag
            response = s3.get_object(**kwargs)
            with download_temp.open("wb") as destination:
                shutil.copyfileobj(response["Body"], destination)
            if download_temp.stat().st_size != owned.size_bytes:
                raise RuntimeError("backup_download_size_mismatch")
            with get_session_factory().session() as publish_session:
                publish_file(
                    publish_session,
                    LocalStorageBackend(),
                    str(local_path),
                    download_temp,
                    object_kind="backup",
                    move=True,
                )
                publish_session.commit()
        except Exception:
            download_temp.unlink(missing_ok=True)
            raise
        logger.info("backup %s downloaded from S3 to %s", meta.id, local_path)
        return local_path

    raise FileNotFoundError(f"backup {meta.id} not found locally or in S3")


def _has_member(tar: tarfile.TarFile, name: str) -> bool:
    try:
        tar.getmember(name)
        return True
    except KeyError:
        return False


def _restore_key_map(tar: tarfile.TarFile) -> dict[str, str]:
    """Return the arcname → original storage key map from the archive manifest.

    Empty for legacy archives that predate the map; callers fall back to the
    arcname transform in that case.
    """
    if not _has_member(tar, "manifest.json"):
        return {}
    f = tar.extractfile("manifest.json")
    if f is None:
        return {}
    try:
        manifest = json.loads(f.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    result: dict[str, str] = {}
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict):
            continue
        member = entry.get("member", entry.get("arc"))
        key = entry.get("key")
        if isinstance(member, str) and isinstance(key, str):
            result[member] = key
    return result


def _restore_manifest_entries(tar: tarfile.TarFile) -> tuple[dict, dict[str, dict]]:
    """Read and validate manifest metadata before any destination is written."""
    if not _has_member(tar, "manifest.json"):
        raise RuntimeError("backup_manifest_invalid")
    source = tar.extractfile("manifest.json")
    if source is None:
        raise RuntimeError("backup_manifest_invalid")
    try:
        manifest = json.loads(source.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("backup_manifest_invalid") from exc
    if (
        not isinstance(manifest, dict)
        or str(manifest.get("version")) not in _SUPPORTED_MANIFEST_VERSIONS
    ):
        raise RuntimeError("backup_manifest_invalid")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeError("backup_manifest_invalid")

    version = str(manifest["version"])
    if version == MANIFEST_VERSION and (
        not isinstance(manifest.get("provider_id"), str)
        or not isinstance(manifest.get("transport"), str)
        or not isinstance(manifest.get("namespace"), (str, type(None)))
        or not isinstance(manifest.get("namespaces"), list)
        or any(not isinstance(value, str) for value in manifest["namespaces"])
    ):
        raise RuntimeError("backup_manifest_invalid")
    entries: dict[str, dict] = {}
    keys: set[str] = set()
    backend = get_backend()
    for entry in files:
        if not isinstance(entry, dict):
            raise RuntimeError("backup_manifest_invalid")
        member = entry.get("member", entry.get("arc"))
        key = entry.get("key")
        if not isinstance(member, str) or not isinstance(key, str):
            raise RuntimeError("backup_manifest_invalid")
        if (
            member in entries
            or key in keys
            or _unsafe_member_name(member)
            or not member.startswith("files/")
        ):
            raise RuntimeError("backup_manifest_invalid")
        _validate_restore_key(key)
        if version in {_LEGACY_MANIFEST_V2, MANIFEST_VERSION}:
            provider_id = str(getattr(backend, "provider_id", backend.backend_name))
            transport = str(getattr(backend, "transport", backend.backend_name))
            sha256 = entry.get("sha256")
            if (
                entry.get("namespace") != backend.namespace_for(key)
                or not isinstance(entry.get("size"), int)
                or entry["size"] < 0
                or not isinstance(sha256, str)
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
                or (
                    version == _LEGACY_MANIFEST_V2
                    and entry.get("provider") != backend.backend_name
                )
                or (
                    version == MANIFEST_VERSION
                    and (
                        entry.get("provider_id") != provider_id
                        or entry.get("transport") != transport
                        # v3 keeps the v2 ``provider`` field for old readers.
                        # When present it is still identity evidence, so a
                        # foreign value must not be silently ignored.
                        or (
                            entry.get("provider") is not None
                            and entry.get("provider") != backend.backend_name
                        )
                    )
                )
            ):
                raise RuntimeError("backup_storage_namespace_mismatch")
        entries[member] = entry
        keys.add(key)
    if version == MANIFEST_VERSION and manifest.get("file_count") != len(entries):
        raise RuntimeError("backup_manifest_invalid")
    if version == MANIFEST_VERSION:
        expected_provider_id = str(
            getattr(backend, "provider_id", backend.backend_name)
        )
        expected_transport = str(getattr(backend, "transport", backend.backend_name))
        namespaces = sorted({str(item["namespace"]) for item in entries.values()})
        if (
            manifest.get("provider_id") != expected_provider_id
            or manifest.get("transport") != expected_transport
            or manifest.get("namespaces") != namespaces
            or manifest.get("namespace")
            != (namespaces[0] if len(namespaces) == 1 else None)
        ):
            raise RuntimeError("backup_storage_namespace_mismatch")
    return manifest, entries


@dataclass(frozen=True)
class _StagedBlob:
    key: str
    path: Path
    size: int
    sha256: str
    namespace: str


@dataclass(frozen=True)
class _AppliedBlob:
    key: str
    receipt: CreationReceipt
    sha256: str
    generation: int


def _stage_restore_archive(
    archive_path: Path, staging_dir: Path
) -> tuple[Path, list[_StagedBlob]]:
    """Extract a validated database and blobs into private staging files."""
    database_path = staging_dir / "db.sqlite3"
    staged_blobs: list[_StagedBlob] = []
    with tarfile.open(archive_path, mode="r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            if _unsafe_member_name(member.name) or member.issym() or member.islnk():
                raise RuntimeError("backup_manifest_invalid")

        manifest, manifest_entries = _restore_manifest_entries(tar)
        version = str(manifest["version"])
        if version in {_LEGACY_MANIFEST_V2, MANIFEST_VERSION}:
            regular_by_name: dict[str, list[tarfile.TarInfo]] = {}
            for member in members:
                if member.isfile():
                    regular_by_name.setdefault(member.name, []).append(member)
            if len(regular_by_name.get("manifest.json", [])) != 1:
                raise RuntimeError("backup_manifest_invalid")
            if len(regular_by_name.get("db.sqlite3", [])) != 1:
                raise RuntimeError("backup_manifest_invalid")
            archived_files = {
                name for name in regular_by_name if name.startswith("files/")
            }
            if archived_files != set(manifest_entries):
                raise RuntimeError("backup_manifest_invalid")
            if any(len(regular_by_name[name]) != 1 for name in archived_files):
                raise RuntimeError("backup_manifest_invalid")
            if set(regular_by_name) != {
                "manifest.json",
                "db.sqlite3",
                *manifest_entries,
            }:
                raise RuntimeError("backup_manifest_invalid")
        arc_to_key = _restore_key_map(tar)
        db_member = (
            tar.extractfile("db.sqlite3") if _has_member(tar, "db.sqlite3") else None
        )
        if db_member is None:
            raise RuntimeError("backup_member_missing:db.sqlite3")
        with database_path.open("wb") as destination:
            shutil.copyfileobj(db_member, destination)
        _validate_sqlite_snapshot(database_path)

        for member in members:
            if not member.name.startswith("files/") or member.name == "files/":
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            key = arc_to_key.get(member.name, member.name[len("files/") :])
            staged_path = staging_dir / f"blob-{len(staged_blobs):08d}"
            with staged_path.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            if staged_path.stat().st_size != member.size:
                raise RuntimeError("backup_member_size_mismatch")
            entry = manifest_entries.get(member.name)
            digest = _sha256_path(staged_path)
            if entry is not None and isinstance(entry.get("size"), int):
                if member.size != int(entry["size"]):
                    raise RuntimeError("backup_member_size_mismatch")
                expected_sha = entry.get("sha256")
                if isinstance(expected_sha, str):
                    if digest != expected_sha:
                        raise RuntimeError("backup_member_hash_mismatch")
            staged_blobs.append(
                _StagedBlob(
                    key=key,
                    path=staged_path,
                    size=member.size,
                    sha256=digest,
                    namespace=get_backend().namespace_for(key),
                )
            )
    return database_path, staged_blobs


def _write_staged_blob(staged_path: Path, key: str) -> int:
    with staged_path.open("rb") as source:
        return get_backend().create_stream(source, key).size


def _rollback_applied_blobs(
    applied: list[_AppliedBlob], *, journal_path: Path | None = None
) -> None:
    backend = get_backend()
    for item in reversed(applied):
        try:
            removed = backend.rollback_create(item.receipt)
            if removed and journal_path is not None:
                _append_restore_journal(
                    journal_path,
                    {
                        "event": "retracted",
                        "key": item.key,
                        "generation": item.generation,
                    },
                )
            if not removed:
                logger.error(
                    "restore rollback preserved uncertain storage key %s", item.key
                )
        except Exception:
            logger.exception("restore rollback failed for storage key %s", item.key)


def _sync_restored_ownership(
    database_path: Path,
    applied: list[_AppliedBlob],
    *,
    archive_ownership: OwnedStorageObject,
) -> None:
    """Replace archived fingerprints with proof from this restore operation."""
    with sqlite3.connect(database_path) as connection:
        for item in applied:
            receipt = item.receipt
            existing = connection.execute(
                "SELECT object_kind FROM owned_storage_objects WHERE key = ? LIMIT 1",
                (item.key,),
            ).fetchone()
            object_kind = str(existing[0]) if existing else "restored"
            # Archived inode/ETag values prove an old object, not the one just
            # created. Replace them with this operation's current receipt.
            connection.execute(
                "DELETE FROM owned_storage_objects WHERE key = ?", (item.key,)
            )
            connection.execute(
                """
                INSERT INTO owned_storage_objects (
                    backend, namespace, key, object_kind, state, token,
                    size_bytes, sha256, etag, version_id, device, inode, ctime_ns,
                    committed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.backend,
                    receipt.namespace,
                    receipt.key,
                    object_kind,
                    "committed",
                    receipt.token,
                    receipt.size,
                    item.sha256,
                    receipt.etag,
                    receipt.version_id,
                    receipt.device,
                    receipt.inode,
                    receipt.ctime_ns,
                    utcnow().isoformat(sep=" "),
                    utcnow().isoformat(sep=" "),
                ),
            )
        connection.execute(
            """
            DELETE FROM owned_storage_objects
            WHERE backend = ? AND namespace = ? AND key = ?
            """,
            (
                archive_ownership.backend,
                archive_ownership.namespace,
                archive_ownership.key,
            ),
        )
        connection.execute(
            """
            INSERT INTO owned_storage_objects (
                backend, namespace, key, object_kind, state, token,
                size_bytes, sha256, etag, version_id, device, inode, ctime_ns,
                committed_at, created_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                archive_ownership.backend,
                archive_ownership.namespace,
                archive_ownership.key,
                archive_ownership.object_kind,
                archive_ownership.state.value,
                archive_ownership.token,
                archive_ownership.size_bytes,
                archive_ownership.sha256,
                archive_ownership.etag,
                archive_ownership.version_id,
                archive_ownership.device,
                archive_ownership.inode,
                archive_ownership.ctime_ns,
                archive_ownership.committed_at,
                archive_ownership.created_at,
                archive_ownership.last_error,
            ),
        )
        connection.commit()


def _sync_restored_storage_identity(database_path: Path) -> None:
    """Carry the current installation binding into a restored database.

    v1/v2 databases predate the durable identity column (or contain a NULL
    value).  Swapping one in while the existing mount sentinel retains the
    current identity would otherwise make the next startup classify every
    managed root as a foreign mount.  The target identity is trusted only from
    the already-running, validated installation and is written before the DB
    swap PONR.
    """
    identity = str(getattr(settings, "storage_identity", "") or "").strip()
    if not identity:
        return
    with sqlite3.connect(database_path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(system_config)")
        }
        if "storage_identity" not in columns:
            return
        connection.execute(
            "UPDATE system_config SET storage_identity = ? WHERE id = 1",
            (identity,),
        )
        connection.commit()


def _apply_staged_blobs(
    blobs: list[_StagedBlob],
    rollback_dir: Path,
    *,
    journal_path: Path | None = None,
    journal_state: _RestoreJournalState | None = None,
) -> tuple[list[_AppliedBlob], list[_AppliedBlob]]:
    """Publish a restore only into empty, in-bound destinations.

    Restore used to overwrite every manifest key and then attempt a best-effort
    rollback. A malicious/stale manifest or remapped root could therefore
    clobber unrelated bytes. Colliding restores now fail before the first write;
    operators must restore into dedicated empty storage.
    """
    del rollback_dir
    backend = get_backend()
    applied: list[_AppliedBlob] = []
    created: list[_AppliedBlob] = []

    seen: set[str] = set()
    for blob in blobs:
        _validate_restore_key(blob.key)
        if blob.key in seen:
            raise RestoreConflictError("restore_duplicate_destination")
        seen.add(blob.key)
        if not backend.exists(blob.key):
            continue
        intent = journal_state.intents.get(blob.key) if journal_state else None
        if intent is None or not _journal_intent_matches(intent, blob):
            raise RestoreConflictError("restore_destination_exists")
        if not _stored_blob_matches(blob):
            raise RestoreConflictError("restore_destination_changed")

    try:
        for blob in blobs:
            intent = journal_state.intents.get(blob.key) if journal_state else None
            published = journal_state.published.get(blob.key) if journal_state else None
            if backend.exists(blob.key):
                receipt = _adopt_restored_blob(blob, published)
                assert intent is not None
                item = _AppliedBlob(
                    key=blob.key,
                    receipt=receipt,
                    sha256=blob.sha256,
                    generation=_journal_generation(intent),
                )
                applied.append(item)
                if journal_path is not None and published is None:
                    event = _published_restore_event(item)
                    _append_restore_journal(journal_path, event)
                    if journal_state is not None:
                        journal_state.published[blob.key] = event
                continue

            if published is not None and journal_path is not None:
                generation = _journal_generation(published)
                _append_restore_journal(
                    journal_path,
                    {
                        "event": "retracted",
                        "key": blob.key,
                        "generation": generation,
                    },
                )
                if journal_state is not None:
                    journal_state.intents.pop(blob.key, None)
                    journal_state.published.pop(blob.key, None)
                intent = None

            if intent is None:
                generation = (
                    journal_state.generations.get(blob.key, 0) + 1
                    if journal_state is not None
                    else 1
                )
                intent = {
                    "event": "intent",
                    "key": blob.key,
                    "size": blob.size,
                    "sha256": blob.sha256,
                    "namespace": blob.namespace,
                    "generation": generation,
                }
            else:
                generation = _journal_generation(intent)
            if journal_path is not None and (
                journal_state is None or blob.key not in journal_state.intents
            ):
                _append_restore_journal(journal_path, intent)
                if journal_state is not None:
                    journal_state.intents[blob.key] = intent
                    journal_state.generations[blob.key] = generation
            with blob.path.open("rb") as source:
                receipt = backend.create_stream(source, blob.key)
            if receipt.size != blob.path.stat().st_size:
                raise RuntimeError("restore_blob_size_mismatch")
            if not _stored_blob_matches(blob):
                backend.rollback_create(receipt)
                raise RuntimeError("restore_blob_hash_mismatch")
            item = _AppliedBlob(
                key=blob.key,
                receipt=receipt,
                sha256=blob.sha256,
                generation=generation,
            )
            applied.append(item)
            created.append(item)
            if journal_path is not None:
                event = _published_restore_event(item)
                _append_restore_journal(journal_path, event)
                if journal_state is not None:
                    journal_state.published[blob.key] = event
    except Exception:
        _rollback_applied_blobs(created, journal_path=journal_path)
        raise
    return applied, created


@dataclass(frozen=True)
class _RestoreJournalState:
    started: dict[str, object]
    intents: dict[str, dict[str, object]]
    published: dict[str, dict[str, object]]
    generations: dict[str, int]
    database_swap_intent: bool = False
    database_active: bool = False


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_restore_journal(path: Path, event: dict[str, object]) -> None:
    """Durably append one restore transition before proceeding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _remove_restore_journal(path: Path) -> None:
    path.unlink(missing_ok=True)
    _fsync_directory(path.parent)


def _stage_restore_marker(
    database_path: Path,
    backup_id: str,
    *,
    operation_nonce: str,
    archive_sha256: str,
) -> None:
    """Commit the PONR marker into the private database before swapping it."""
    engine = create_engine(URL.create("sqlite", database=str(database_path)))
    try:
        with Session(engine) as session:
            # Markers are operation evidence, not a historical restore log.
            # Remove stale rows copied from an older backup before inserting
            # this operation's marker, avoiding a false active result when the
            # same backup is restored again.
            session.exec(delete(RestoreMarker))
            session.add(
                RestoreMarker(
                    backup_id=backup_id,
                    operation_nonce=operation_nonce,
                    archive_sha256=archive_sha256,
                    state="database_active",
                )
            )
            session.commit()
    finally:
        engine.dispose()
    fd = os.open(database_path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _active_restore_marker(
    backup_id: str,
    *,
    operation_nonce: str | None = None,
    archive_sha256: str | None = None,
) -> bool | None:
    """Read the active DB's marker; ``None`` means the state is unknowable."""
    try:
        with get_session_factory().session() as session:
            statement = select(RestoreMarker).where(
                RestoreMarker.backup_id == backup_id
            )
            if operation_nonce is not None:
                statement = statement.where(
                    RestoreMarker.operation_nonce == operation_nonce
                )
            if archive_sha256 is not None:
                statement = statement.where(
                    RestoreMarker.archive_sha256 == archive_sha256
                )
            marker = session.exec(statement).first()
            return marker is not None and marker.state == "database_active"
    except Exception:
        logger.exception(
            "unable to prove restored database marker", extra={"backup_id": backup_id}
        )
        return None


def _load_restore_journal(path: Path) -> _RestoreJournalState:
    try:
        raw_lines = path.read_bytes().splitlines()
        events = [json.loads(line.decode("utf-8")) for line in raw_lines]
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise RestoreConflictError("restore_journal_invalid") from exc
    if not events or any(not isinstance(event, dict) for event in events):
        raise RestoreConflictError("restore_journal_invalid")
    started = events[0]
    if started.get("event") != "started":
        raise RestoreConflictError("restore_journal_invalid")
    if started.get("version") not in {1, _RESTORE_JOURNAL_VERSION}:
        raise RestoreConflictError("restore_journal_invalid")
    if started.get("version") == _RESTORE_JOURNAL_VERSION:
        nonce = started.get("operation_nonce")
        archive_sha = started.get("archive_sha256")
        if (
            not isinstance(nonce, str)
            or len(nonce) != 64
            or any(char not in "0123456789abcdef" for char in nonce)
            or not isinstance(archive_sha, str)
            or len(archive_sha) != 64
            or any(char not in "0123456789abcdef" for char in archive_sha)
        ):
            raise RestoreConflictError("restore_journal_invalid")
    intents: dict[str, dict[str, object]] = {}
    published: dict[str, dict[str, object]] = {}
    generations: dict[str, int] = {}
    database_swap_intent = False
    database_active = False
    for event in events[1:]:
        event_name = event.get("event")
        if event_name == "database_swap_intent":
            if (
                database_swap_intent
                or database_active
                or event.get("backup_id") != started.get("backup_id")
                or event.get("operation_nonce") != started.get("operation_nonce")
                or event.get("archive_sha256") != started.get("archive_sha256")
            ):
                raise RestoreConflictError("restore_journal_invalid")
            database_swap_intent = True
            continue
        if event_name == "journal_upgrade":
            if (
                event.get("backup_id") != started.get("backup_id")
                or event.get("from_version") != 1
                or event.get("to_version") != _RESTORE_JOURNAL_VERSION
            ):
                raise RestoreConflictError("restore_journal_invalid")
            continue
        if event_name == "database_active":
            if (
                database_active
                or event.get("backup_id") != started.get("backup_id")
                or event.get("operation_nonce") != started.get("operation_nonce")
                or event.get("archive_sha256") != started.get("archive_sha256")
            ):
                raise RestoreConflictError("restore_journal_invalid")
            database_active = True
            continue
        if event_name not in {"intent", "published", "retracted", "complete"}:
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "complete":
            continue
        key = event.get("key")
        generation = event.get("generation")
        if not isinstance(key, str) or not isinstance(generation, int):
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "intent":
            if key in intents or generation != generations.get(key, 0) + 1:
                raise RestoreConflictError("restore_journal_invalid")
            intents[key] = event
            generations[key] = generation
            continue
        active_intent = intents.get(key)
        if active_intent is None or active_intent.get("generation") != generation:
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "published":
            if key in published:
                raise RestoreConflictError("restore_journal_invalid")
            published[key] = event
            continue
        intents.pop(key)
        published.pop(key)
    return _RestoreJournalState(
        started,
        intents,
        published,
        generations,
        database_swap_intent,
        database_active,
    )


def _prepare_restore_journal(
    path: Path,
    *,
    backup_id: str,
    archive_sha256: str,
    blobs: list[_StagedBlob],
    operation_nonce: str | None = None,
) -> _RestoreJournalState:
    operation_nonce = operation_nonce or secrets.token_hex(32)
    for other in path.parent.glob(".restore-*.journal"):
        if other != path:
            raise RestoreConflictError("restore_incomplete_other_backup")
    backend = get_backend()
    expected_start: dict[str, object] = {
        "event": "started",
        "version": _RESTORE_JOURNAL_VERSION,
        "backup_id": backup_id,
        "archive_sha256": archive_sha256,
        "operation_nonce": operation_nonce,
        "backend": backend.backend_name,
        "namespaces": sorted({blob.namespace for blob in blobs}),
    }
    if not path.exists():
        _append_restore_journal(path, expected_start)
        return _RestoreJournalState(expected_start, {}, {}, {})
    state = _load_restore_journal(path)
    if state.started.get("version") == _RESTORE_JOURNAL_VERSION:
        existing_nonce = state.started.get("operation_nonce")
        if isinstance(existing_nonce, str):
            expected_start["operation_nonce"] = existing_nonce
    if state.started != expected_start:
        # v1 had no DB swap intent/marker. Upgrade only a journal whose
        # identity and archive hash are an exact match, and keep its existing
        # events for forward-only resume.
        legacy_identity = {
            key: state.started.get(key)
            for key in expected_start
            if key not in {"version", "operation_nonce"}
        }
        expected_identity = {
            key: expected_start[key]
            for key in expected_start
            if key not in {"version", "operation_nonce"}
        }
        if state.started.get("version") != 1 or legacy_identity != expected_identity:
            raise RestoreConflictError("restore_journal_mismatch")
        _append_restore_journal(
            path,
            {
                "event": "journal_upgrade",
                "backup_id": backup_id,
                "from_version": 1,
                "to_version": _RESTORE_JOURNAL_VERSION,
                "operation_nonce": operation_nonce,
                "archive_sha256": archive_sha256,
            },
        )
    expected_keys = {blob.key for blob in blobs}
    if not set(state.intents).issubset(expected_keys) or not set(
        state.published
    ).issubset(expected_keys):
        raise RestoreConflictError("restore_journal_invalid")
    by_key = {blob.key: blob for blob in blobs}
    if any(
        not _journal_intent_matches(event, by_key[key])
        for key, event in state.intents.items()
    ):
        raise RestoreConflictError("restore_journal_mismatch")
    return state


def _journal_intent_matches(event: dict[str, object], blob: _StagedBlob) -> bool:
    generation = event.get("generation")
    return isinstance(generation, int) and event == {
        "event": "intent",
        "key": blob.key,
        "size": blob.size,
        "sha256": blob.sha256,
        "namespace": blob.namespace,
        "generation": generation,
    }


def _journal_generation(event: dict[str, object]) -> int:
    generation = event.get("generation")
    if not isinstance(generation, int):
        raise RestoreConflictError("restore_journal_invalid")
    return generation


def _stored_blob_matches(blob: _StagedBlob) -> bool:
    backend = get_backend()
    try:
        return (
            backend.stat_size(blob.key) == blob.size
            and _sha256_key(blob.key) == blob.sha256
        )
    except FileNotFoundError:
        return False


def _receipt_from_event(event: dict[str, object]) -> CreationReceipt:
    key = event.get("key")
    size = event.get("size")
    token = event.get("token")
    backend = event.get("backend")
    namespace = event.get("namespace")
    etag = event.get("etag")
    version_id = event.get("version_id")
    device = event.get("device")
    inode = event.get("inode")
    ctime_ns = event.get("ctime_ns")
    if (
        not isinstance(key, str)
        or not isinstance(size, int)
        or not isinstance(token, str)
        or not isinstance(backend, str)
        or not isinstance(namespace, str)
        or (etag is not None and not isinstance(etag, str))
        or (version_id is not None and not isinstance(version_id, str))
        or (device is not None and not isinstance(device, int))
        or (inode is not None and not isinstance(inode, int))
        or (ctime_ns is not None and not isinstance(ctime_ns, int))
    ):
        raise RestoreConflictError("restore_journal_invalid")
    return CreationReceipt(
        key=key,
        size=size,
        token=token,
        backend=backend,
        namespace=namespace,
        etag=etag,
        version_id=version_id,
        device=device,
        inode=inode,
        ctime_ns=ctime_ns,
    )


def _published_restore_event(item: _AppliedBlob) -> dict[str, object]:
    receipt = item.receipt
    return {
        "event": "published",
        "key": item.key,
        "generation": item.generation,
        "size": receipt.size,
        "sha256": item.sha256,
        "token": receipt.token,
        "backend": receipt.backend,
        "namespace": receipt.namespace,
        "etag": receipt.etag,
        "version_id": receipt.version_id,
        "device": receipt.device,
        "inode": receipt.inode,
        "ctime_ns": receipt.ctime_ns,
    }


def _adopt_restored_blob(
    blob: _StagedBlob, published: dict[str, object] | None
) -> CreationReceipt:
    backend = get_backend()
    if published is not None:
        receipt = _receipt_from_event(published)
        if (
            receipt.key != blob.key
            or receipt.size != blob.size
            or receipt.namespace != blob.namespace
            or published.get("sha256") != blob.sha256
        ):
            raise RestoreConflictError("restore_journal_mismatch")
        try:
            if backend.creation_matches(receipt):
                return receipt
        except Exception as exc:
            raise RestoreConflictError("restore_destination_changed") from exc
    try:
        return backend.adopt_existing(
            blob.key,
            expected_size=blob.size,
            expected_sha256=blob.sha256,
        )
    except NotImplementedError:
        # Guarded transports deliberately cannot promise a deletable identity.
        # The content hash is nevertheless sufficient to resume without
        # overwriting or deleting the existing object; ownership keeps that SHA.
        return CreationReceipt(
            key=blob.key,
            size=blob.size,
            token=blob.sha256,
            backend=backend.backend_name,
            namespace=blob.namespace,
        )
    except Exception as exc:
        raise RestoreConflictError("restore_destination_changed") from exc


def _validate_restore_key(key: str) -> None:
    """Delegate destination policy to the active storage backend."""
    try:
        get_backend().validate_restore_key(key)
    except Exception as exc:
        raise RuntimeError("backup_restore_key_outside_storage") from exc


@_exclusive_backup_operation
def restore_backup(backup_id: str) -> dict:
    """Restore a backup with staged blobs and SQLite's online backup API.

    Downloads from S3 if the backup is only in cloud storage.
    WARNING: This replaces the current database, but publishes archived files
    only into empty destinations and refuses conflicting live storage keys.

    Sets a process-wide gate so background loops (GC, external scans, printer
    sync) skip their tick instead of racing the restore. Refuses with
    ``RestoreConflictError`` if ingestion work is still running after a short
    grace period, rather than restoring underneath it.
    """
    _require_database_backup_support(restore=True)
    if restore_in_progress():
        recovery_id = unresolved_restore_backup_id()
        if recovery_id != backup_id:
            raise RestoreConflictError("restore_recovery_required")
    meta = get_backup(backup_id)
    if meta is None:
        raise FileNotFoundError(f"backup {backup_id} not found")

    # Captured before any DB swap: the actor/IP behind this restore, for the
    # post-swap "complete" row (the ambient ContextVar survives the swap, but
    # writing it from a session bound to the restored DB is easiest to read).
    restoring_actor_id, restoring_ip = audit.current_audit_context()
    restored_files = 0
    maintenance_required = False

    _begin_restore_maintenance()
    try:
        with get_session_factory().session() as session:
            audit.record(
                session,
                action="restore.start",
                resource_type="backup",
                diff={"backup_id": backup_id},
            )

        time.sleep(_RESTORE_GRACE_PERIOD_S)
        counts = registry.snapshot_counts()
        active_jobs = counts["pending"] + counts["running"]
        with get_session_factory().scoped_session() as lease_session:
            active_leases = len(
                lease_session.exec(
                    select(StagingLease).where(StagingLease.expires_at > utcnow())
                ).all()
            )
        if active_jobs or active_leases:
            with get_session_factory().session() as session:
                audit.record(
                    session,
                    action="restore.failed",
                    resource_type="backup",
                    diff={
                        "backup_id": backup_id,
                        "reason": "jobs_running",
                        "running": counts["running"],
                        "pending": counts["pending"],
                        "staging_leases": active_leases,
                    },
                )
            raise RestoreConflictError(
                f"{active_jobs} ingestion job(s) and {active_leases} staging lease(s) active"
            )

        try:
            archive_path = _download_backup_to_local(meta)
            settings.backup_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=".printstash-restore-", dir=settings.backup_dir
            ) as raw_staging_dir:
                staging_dir = Path(raw_staging_dir)
                database_path, staged_blobs = _stage_restore_archive(
                    archive_path, staging_dir
                )
                # Detect replacement/in-place mutation while the archive was
                # being staged, before any live blob or database mutation.
                _require_backup_archive_owned(meta)
                archive_ownership = _require_backup_archive_owned(
                    BackupMeta(
                        id=meta.id,
                        created_at=meta.created_at,
                        size_bytes=archive_path.stat().st_size,
                        storage_backend=meta.storage_backend,
                        file_count=meta.file_count,
                        app_version=meta.app_version,
                        path=str(archive_path),
                        location="local",
                    )
                )
                # Upgrade the private staged copy before touching live bytes.
                # This keeps old backups restorable and guarantees the
                # ownership ledger exists for this operation's receipts.
                run_migrations(str(URL.create("sqlite", database=str(database_path))))
                rollback_dir = staging_dir / "rollback"
                rollback_dir.mkdir()
                journal_path = settings.backup_dir / f".restore-{backup_id}.journal"
                resuming_journal = journal_path.exists()
                operation_nonce = secrets.token_hex(32)
                journal_state = _prepare_restore_journal(
                    journal_path,
                    backup_id=backup_id,
                    archive_sha256=_sha256_path(archive_path),
                    blobs=staged_blobs,
                    operation_nonce=operation_nonce,
                )
                operation_nonce = str(
                    journal_state.started.get("operation_nonce", operation_nonce)
                )
                archive_sha256 = str(
                    journal_state.started.get(
                        "archive_sha256", _sha256_path(archive_path)
                    )
                )
                marker_nonce = (
                    operation_nonce
                    if journal_state.started.get("version") == _RESTORE_JOURNAL_VERSION
                    else None
                )

                def active_marker_state() -> bool | None:
                    # Keep genuine v1 journal compatibility: its marker cannot
                    # carry a nonce, so the archive hash remains the exact
                    # binding and older test/maintenance adapters may expose
                    # the original one-argument seam.
                    if marker_nonce is None:
                        return _active_restore_marker(backup_id)
                    return _active_restore_marker(
                        backup_id,
                        operation_nonce=marker_nonce,
                        archive_sha256=archive_sha256,
                    )

                # The database marker is authoritative if the process died
                # after swapping but before its sidecar journal acknowledgement.
                # Treat that database as active and finish forward.
                # A successful prior restore leaves its marker in the active
                # database. It is evidence for a journal being resumed, not
                # proof that this fresh restore has already swapped databases.
                if resuming_journal and active_marker_state() is True:
                    journal_state = replace(
                        journal_state,
                        database_swap_intent=True,
                        database_active=True,
                    )
                applied, created = _apply_staged_blobs(
                    staged_blobs,
                    rollback_dir,
                    journal_path=journal_path,
                    journal_state=journal_state,
                )
                db_swapped = False
                try:
                    if any(not _stored_blob_matches(blob) for blob in staged_blobs):
                        raise RestoreConflictError("restore_destination_changed")
                    if not journal_state.database_active:
                        _sync_restored_ownership(
                            database_path,
                            applied,
                            archive_ownership=archive_ownership,
                        )
                        _sync_restored_storage_identity(database_path)
                        # Commit an active marker into the staged DB, then
                        # fsync the sidecar swap intent before touching the live
                        # database. The marker is the cross-store PONR proof.
                        _stage_restore_marker(
                            database_path,
                            backup_id,
                            operation_nonce=operation_nonce,
                            archive_sha256=archive_sha256,
                        )
                        _append_restore_journal(
                            journal_path,
                            {
                                "event": "database_swap_intent",
                                "backup_id": backup_id,
                                "operation_nonce": operation_nonce,
                                "archive_sha256": archive_sha256,
                            },
                        )
                        # Restore the DB last. Until this succeeds, rollback
                        # can put every touched blob back under the
                        # still-current database.
                        try:
                            _restore_database_from_path(database_path)
                        except Exception as exc:
                            # SQLite's online backup can fail after replacing
                            # the destination. Always query the marker before
                            # deciding whether blob rollback is safe.
                            marker_state = active_marker_state()
                            if marker_state is True:
                                # The swap is complete despite the reporting
                                # exception. Continue forward and acknowledge
                                # the durable marker; never retract blobs.
                                db_swapped = True
                                journal_state = replace(
                                    journal_state,
                                    database_swap_intent=True,
                                    database_active=True,
                                )
                            elif marker_state is None:
                                db_swapped = True
                                maintenance_required = True
                                raise RestoreConflictError(
                                    "restore_database_state_unknown"
                                ) from exc
                            else:
                                # The marker proves the old database remains
                                # active, so pre-PONR rollback is safe.
                                raise
                        if not db_swapped:
                            active_marker = active_marker_state()
                            if active_marker is False:
                                raise RestoreConflictError(
                                    "restore_database_swap_not_active"
                                )
                            if active_marker is None:
                                # We cannot prove whether the replacement
                                # happened; preserving blobs is the only safe
                                # recovery action.
                                db_swapped = True
                                maintenance_required = True
                                raise RestoreConflictError(
                                    "restore_database_state_unknown"
                                )
                            db_swapped = True
                        # Database replacement is the restore point of no
                        # return. A journal acknowledgement failure after this
                        # point must never retract bytes referenced by the
                        # marker. A later retry can finish the journal.
                        if not journal_state.database_active:
                            _append_restore_journal(
                                journal_path,
                                {
                                    "event": "database_active",
                                    "backup_id": backup_id,
                                    "operation_nonce": operation_nonce,
                                    "archive_sha256": archive_sha256,
                                },
                            )
                            journal_state = replace(journal_state, database_active=True)
                    else:
                        db_swapped = True
                    _append_restore_journal(
                        journal_path,
                        {
                            "event": "complete",
                            "backup_id": backup_id,
                            "operation_nonce": operation_nonce,
                            "archive_sha256": archive_sha256,
                        },
                    )
                    _remove_restore_journal(journal_path)
                except Exception:
                    if not db_swapped:
                        _rollback_applied_blobs(created, journal_path=journal_path)
                        raise
                    if maintenance_required:
                        # Marker lookup itself failed. Preserve the original
                        # unknown outcome and the staged bytes for operator
                        # recovery; do not mask it as a generic ack failure.
                        raise
                    # The marker proves the new database owns the staged
                    # bytes. Keep maintenance enabled and leave the journal
                    # for a forward retry when acknowledgement or cleanup
                    # fails; never roll those bytes back.
                    maintenance_required = True
                    raise RestoreConflictError(
                        "restore_post_swap_recovery_required"
                    ) from None
                restored_files = len(staged_blobs)
        except Exception:
            try:
                with get_session_factory().session() as session:
                    audit.record(
                        session,
                        action="restore.failed",
                        resource_type="backup",
                        diff={"backup_id": backup_id, "reason": "restore_error"},
                    )
            except Exception:
                logger.exception(
                    "restore %s failed and audit recording also failed", backup_id
                )
            raise
    finally:
        if not maintenance_required and not _restore_journal_pending():
            _end_restore_maintenance()
        else:
            logger.critical(
                "restore outcome is unknown; leaving the application in maintenance mode",
                extra={"backup_id": backup_id},
            )

    logger.info("backup %s restored: %d files", backup_id, restored_files)

    # Written against the now-restored database. The pre-restore actor may not
    # exist there (an older/different backup's users table), so validate
    # before trusting the id — a foreign-key violation here must not turn a
    # successful restore into a failure.
    with get_session_factory().session() as session:
        safe_actor_id = (
            restoring_actor_id
            if restoring_actor_id is not None
            and session.get(User, restoring_actor_id) is not None
            else None
        )
        audit.record(
            session,
            action="restore.complete",
            resource_type="backup",
            actor_id=safe_actor_id,
            ip=restoring_ip,
            diff={"backup_id": backup_id, "restored_files": restored_files},
        )

    return {
        "backup_id": backup_id,
        "restored_files": restored_files,
    }


def _dispose_session_engine() -> None:
    factory = get_session_factory()
    dispose = getattr(factory, "dispose", None)
    if callable(dispose):
        dispose()
    else:  # Compatibility for third-party SessionFactory implementations.
        get_engine().dispose()


def _restore_database_from_path(source_path: Path) -> None:
    db_path = _db_path()
    if db_path is None:
        raise RuntimeError("cannot restore to non-file database")
    _validate_sqlite_snapshot(source_path)

    # Close idle pooled connections before and after the online copy. Checked
    # out read connections remain safe: SQLite coordinates them with the backup
    # transaction instead of replaying a stale sidecar over a raw file swap.
    _dispose_session_engine()
    try:
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
    if _db_path() is None:
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


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


def purge_old_backups(retain_days: int | None = None) -> int:
    """Remove backups older than the retention period (local + S3)."""
    if retain_days is None:
        retain_days = settings.backup_retention_days
    if retain_days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(retain_days, 1))
    removed = 0

    for meta in list_backups():
        try:
            created = datetime.fromisoformat(meta.created_at)
            if created < cutoff:
                if delete_backup(meta.id):
                    removed += 1
        except (ValueError, OSError):
            continue

    if removed:
        logger.info("backup purge: removed %d old backups", removed)
    return removed
