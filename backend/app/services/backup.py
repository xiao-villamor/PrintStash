"""Vault backup & restore service.

Creates tar.gz snapshots of the SQLite database and all stored files.
Backups are always written locally first, then optionally uploaded to a
separate S3/R2 bucket for off-site durability.

The backup S3 destination is independent from vault S3 storage — this
allows a "local vault + cloud backup" split architecture.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, ParamSpec, TypeVar

from sqlalchemy.engine.url import make_url
from sqlmodel import Session, create_engine

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import User
from app.db.session import get_engine, get_session_factory
from app.services import audit
from app.services.jobs import registry
from app.services.storage_backend import get_backend
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


MANIFEST_VERSION = "1"
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


def _get_backup_s3():  # pragma: no cover — needs a real S3-compatible endpoint;
    # verified against SeaweedFS in the storage-s3 CI job (see docs/backend.md).
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
    """Return ``(key, size_bytes)`` for every vault-owned primary blob.

    One ``stat_size`` per key doubles as the existence check (it raises when the
    key is gone), and surfacing the size lets ``create_backup`` build the
    manifest *before* streaming the file bodies. Linked external Artifacts are
    indexed by the vault but user-owned, so their paths must never be read into
    a backup archive.
    """
    if session is None:
        with get_session_factory().session() as owned_session:
            return _find_blobs(owned_session)

    keys = sorted(
        {blob.key for blob in ownership_snapshot(session, discover=False).primary}
    )
    backend = get_backend()
    out: list[tuple[str, int]] = []
    for key in keys:
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


def _add_file_to_tar(tar: tarfile.TarFile, key: str, arcname: str) -> int:
    # local_path() yields the real file locally, or a self-cleaning temp
    # download for remote backends — no branching on backend type.
    with get_backend().local_path(key) as path:
        tar.add(str(path), arcname=arcname)
        return path.stat().st_size


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
        blobs = _find_snapshot_blobs(db_snapshot)

        # Map each tar entry back to the exact storage key it came from. Keys can
        # be absolute paths (local) or object keys (S3), so the manifest is the
        # authoritative reverse mapping used by restore.
        file_entries: list[dict[str, str | int]] = [
            {
                "arc": f"files/{key.replace('vault-data/', '').lstrip('/')}",
                "key": key,
                "size": size,
            }
            for key, size in blobs
        ]
        total_size = db_snapshot.stat().st_size + sum(size for _key, size in blobs)
        manifest = {
            "version": MANIFEST_VERSION,
            "created_at": ts,
            "app_version": settings.app_version,
            "storage_backend": backend_name,
            "file_count": len(file_entries),
            "total_size_bytes": total_size,
            "files": file_entries,
        }
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

        try:
            with gzip.open(archive_path, "wb") as gz:
                with tarfile.open(fileobj=gz, mode="w|") as tar:
                    man_info = tarfile.TarInfo(name="manifest.json")
                    man_info.size = len(manifest_bytes)
                    tar.addfile(man_info, io.BytesIO(manifest_bytes))

                    # tarfile streams this file; the database is never loaded as
                    # one in-memory bytes object.
                    tar.add(db_snapshot, arcname="db.sqlite3", recursive=False)

                    for entry in file_entries:
                        key = str(entry["key"])
                        arc = str(entry["arc"])
                        written = _add_file_to_tar(tar, key, arc)
                        expected = int(entry["size"])
                        if written != expected:
                            raise RuntimeError("backup_blob_size_changed")
                        written_files += 1
        except Exception:
            archive_path.unlink(missing_ok=True)
            logger.exception("backup %s failed while streaming owned blobs", backup_id)
            raise

    final_size = archive_path.stat().st_size

    logger.info(
        "backup %s created locally: %d files, %.1f MiB",
        backup_id,
        written_files,
        final_size / (1024 * 1024),
    )

    # Upload to S3 if configured
    s3 = _get_backup_s3()
    if s3:  # pragma: no cover — see _get_backup_s3
        try:
            s3_key = _backup_s3_key(archive_name)
            s3.upload_file(str(archive_path), settings.backup_s3_bucket, s3_key)
            logger.info("backup %s uploaded to S3: %s", backup_id, s3_key)
        except Exception:
            logger.warning("backup %s: S3 upload failed", backup_id, exc_info=True)

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


def _list_local_backups() -> list[BackupMeta]:
    results: list[BackupMeta] = []
    if not settings.backup_dir.exists():
        return results

    for archive in sorted(
        [
            *settings.backup_dir.glob(f"{_BACKUP_NAME_PREFIX}*.tar.gz"),
            *settings.backup_dir.glob(f"{_LEGACY_BACKUP_NAME_PREFIX}*.tar.gz"),
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            meta = _read_manifest(archive)
            if meta is not None:
                meta.location = "local"
                meta.path = str(archive)
                results.append(meta)
        except Exception:
            logger.warning("backup: cannot read manifest from %s", archive.name)

    return results


def _list_s3_backups() -> list[BackupMeta]:  # pragma: no cover — see _get_backup_s3
    s3 = _get_backup_s3()
    if not s3:
        return []

    results: list[BackupMeta] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for prefix in (_BACKUP_S3_PREFIX, _LEGACY_BACKUP_S3_PREFIX):
            for page in paginator.paginate(
                Bucket=settings.backup_s3_bucket, Prefix=prefix
            ):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
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


def _unsafe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or not name or "\\" in name


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
                expected_entries = manifest.get("files")
                if not isinstance(expected_entries, list):
                    findings.append(
                        {"code": "backup_manifest_invalid", "member": "files"}
                    )
                else:
                    by_name: dict[str, list[tarfile.TarInfo]] = {}
                    for member in members:
                        by_name.setdefault(member.name, []).append(member)
                    for entry in expected_entries:
                        if not isinstance(entry, dict) or not isinstance(
                            entry.get("arc"), str
                        ):
                            findings.append(
                                {"code": "backup_manifest_invalid", "member": "files"}
                            )
                            continue
                        arc = entry["arc"]
                        matches = by_name.get(arc, [])
                        if len(matches) != 1:
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
    except (tarfile.TarError, OSError, EOFError):
        findings.append({"code": "backup_manifest_invalid", "member": "archive"})

    manifest_version = str(manifest.get("version")) if manifest else None
    app_compatible = manifest_version == MANIFEST_VERSION
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

    deleted = False

    # Delete local copy
    if meta.location == "local":
        try:
            Path(meta.path).unlink(missing_ok=True)
            deleted = True
        except OSError:
            logger.exception("backup: failed to delete local %s", backup_id)

    # Delete S3 copy
    s3 = _get_backup_s3()
    if s3:  # pragma: no cover — see _get_backup_s3
        # Look up S3 key from the listing
        for sm in _list_s3_backups():
            if sm.id == backup_id:
                try:
                    s3.delete_object(Bucket=settings.backup_s3_bucket, Key=sm.path)
                    deleted = True
                except Exception:
                    logger.exception("backup: failed to delete S3 %s", backup_id)
                break

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

    if meta.location == "s3":  # pragma: no cover — see _get_backup_s3
        # Download from S3 to a temp location
        s3 = _get_backup_s3()
        if not s3:
            raise RuntimeError("backup is in S3 but no S3 client is available")

        archive_name = meta.path.rsplit("/", 1)[-1]
        local_path = settings.backup_dir / archive_name
        settings.backup_dir.mkdir(parents=True, exist_ok=True)

        s3.download_file(settings.backup_s3_bucket, meta.path, str(local_path))
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
    return {
        entry["arc"]: entry["key"]
        for entry in manifest.get("files", [])
        if "arc" in entry and "key" in entry
    }


@dataclass(frozen=True)
class _StagedBlob:
    key: str
    path: Path


@dataclass(frozen=True)
class _AppliedBlob:
    key: str
    existed: bool
    rollback_path: Path | None


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
            staged_blobs.append(_StagedBlob(key=key, path=staged_path))
    return database_path, staged_blobs


def _write_staged_blob(staged_path: Path, key: str) -> int:
    with staged_path.open("rb") as source:
        return get_backend().write_stream(source, key)


def _rollback_applied_blobs(applied: list[_AppliedBlob]) -> None:
    backend = get_backend()
    for item in reversed(applied):
        try:
            if item.existed:
                assert item.rollback_path is not None
                with item.rollback_path.open("rb") as source:
                    backend.write_stream(source, item.key)
            else:
                backend.delete(item.key)
        except Exception:
            logger.exception("restore rollback failed for storage key %s", item.key)


def _apply_staged_blobs(
    blobs: list[_StagedBlob], rollback_dir: Path
) -> list[_AppliedBlob]:
    backend = get_backend()
    applied: list[_AppliedBlob] = []
    try:
        for index, blob in enumerate(blobs):
            existed = backend.exists(blob.key)
            rollback_path = rollback_dir / f"original-{index:08d}" if existed else None
            if rollback_path is not None:
                with backend.local_path(blob.key) as current:
                    shutil.copyfile(current, rollback_path)
            # Record the target before writing so a partial write is rolled back.
            applied.append(
                _AppliedBlob(
                    key=blob.key,
                    existed=existed,
                    rollback_path=rollback_path,
                )
            )
            written = _write_staged_blob(blob.path, blob.key)
            if written != blob.path.stat().st_size:
                raise RuntimeError("restore_blob_size_mismatch")
    except Exception:
        _rollback_applied_blobs(applied)
        raise
    return applied


@_exclusive_backup_operation
def restore_backup(backup_id: str) -> dict:
    """Restore a backup with staged blobs and SQLite's online backup API.

    Downloads from S3 if the backup is only in cloud storage.
    WARNING: This replaces the current database and all files.

    Sets a process-wide gate so background loops (GC, external scans, printer
    sync) skip their tick instead of racing the restore. Refuses with
    ``RestoreConflictError`` if ingestion work is still running after a short
    grace period, rather than restoring underneath it.
    """
    _require_database_backup_support(restore=True)
    meta = get_backup(backup_id)
    if meta is None:
        raise FileNotFoundError(f"backup {backup_id} not found")

    # Captured before any DB swap: the actor/IP behind this restore, for the
    # post-swap "complete" row (the ambient ContextVar survives the swap, but
    # writing it from a session bound to the restored DB is easiest to read).
    restoring_actor_id, restoring_ip = audit.current_audit_context()
    restored_files = 0

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
        running = registry.snapshot_counts()["running"]
        if running:
            with get_session_factory().session() as session:
                audit.record(
                    session,
                    action="restore.failed",
                    resource_type="backup",
                    diff={
                        "backup_id": backup_id,
                        "reason": "jobs_running",
                        "running": running,
                    },
                )
            raise RestoreConflictError(
                f"{running} ingestion job(s) still running; retry once they finish"
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
                rollback_dir = staging_dir / "rollback"
                rollback_dir.mkdir()
                applied = _apply_staged_blobs(staged_blobs, rollback_dir)
                try:
                    # Restore the DB last. Until this succeeds, rollback can put
                    # every touched blob back under the still-current database.
                    _restore_database_from_path(database_path)
                except Exception:
                    _rollback_applied_blobs(applied)
                    raise
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
        _end_restore_maintenance()

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
