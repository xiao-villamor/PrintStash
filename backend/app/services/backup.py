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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterator, Literal, ParamSpec, TypeVar
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.engine import URL
from sqlalchemy.engine.url import make_url
from sqlmodel import Session, create_engine, delete, select

from app.core.config import _overlay, settings
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
from app.services import audit, storage
from app.services.backup_destination import (
    BackupDestinationError,
    BackupTrigger,
    RemoteBackupDestination,
    configured_destinations,
    destination_for_ownership,
    local_destination_enabled,
)
from app.services.jobs import registry
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    get_backend,
)
from app.services.storage_ownership import (
    complete_publication,
    delete_owned_key,
    provider_ref_for_backend,
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
# A committed cache receipt can survive a process crash after publication.
# Once it is no longer referenced by a valid restore journal, retain it only
# for a bounded recovery window before reclaiming the rebuildable derivative.
_BACKUP_CACHE_STALE_AFTER_S = 24 * 60 * 60
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


class _BackupConfigUnstableError(RuntimeError):
    """Settings changed during a target snapshot; retry the next operation."""


class BackupIdentityConflictError(RuntimeError):
    """More than one different archive claims the requested backup id."""


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
        # An unreadable backup directory cannot prove that no restore journal
        # exists. Keep maintenance active until an operator can inspect or
        # repair the directory; clearing the gate here would allow writes to
        # race an unresolved restore.
        with _mutation_condition:
            _restore_gate.set()
        return True
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
            nonce = state.started.get("operation_nonce")
            archive_sha = state.started.get("archive_sha256")
            if not isinstance(nonce, str) or not isinstance(archive_sha, str):
                # An un-upgraded v1 journal cannot authorise a marker lookup;
                # leave maintenance active until the normal resume upgrades it.
                continue
            _active_restore_marker(
                str(state.started.get("backup_id", "")),
                operation_nonce=nonce,
                archive_sha256=archive_sha,
            )
    if unresolved:
        with _mutation_condition:
            _restore_gate.set()
    return unresolved


def unresolved_restore_backup_id() -> str | None:
    """Return the only journaled backup allowed to resume recovery.

    ``None`` is returned when the journal is unreadable, malformed, or
    ambiguous.  Callers must fail closed in that case rather than allowing a
    new restore to bypass the unresolved operation.
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
    if not backup_id:
        return None
    # Route journals whose first record is still readable to the restore parser
    # so sequence corruption reports the precise ``restore_journal_invalid``
    # reason.  A completely unreadable first record has no trustworthy routing
    # identity and must remain fail-closed (no restore may bypass maintenance).
    try:
        first_line = path.read_bytes().splitlines()[0]
        started = json.loads(first_line.decode("utf-8"))
    except (IndexError, OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(started, dict):
        return None
    return backup_id


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
    # Content identity is deliberately separate from the human-facing id.  An
    # id is a filename convention and can collide after a copy or migration.
    archive_sha256: str | None = None
    provider_ref: str | None = None
    source_ref: str | None = None
    namespace: str | None = None
    # For a logical id with several sources these make the deterministic
    # precedence visible to operators.  They are presentation metadata only;
    # ``source_ref`` remains the sole authorization for an operation.
    canonical: bool = False
    precedence: int = 0


@dataclass
class BackupVerification:
    backup_id: str
    valid: bool
    app_compatible: bool
    manifest_version: str | None
    checked_members: int
    findings: list[dict[str, str | int]]


BackupOwnershipVerificationStatus = Literal[
    "valid", "missing", "inaccessible", "identity", "digest", "corrupt"
]


@dataclass(frozen=True)
class BackupOwnershipVerification:
    """Verification result for one exact ownership-ledger row."""

    ownership_id: int
    status: BackupOwnershipVerificationStatus
    verification: BackupVerification | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# S3 client for backup operations (independent from vault S3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BackupS3Target:
    client: Any
    bucket: str
    signature: str
    provider_ref: str = ""


_backup_s3: Any = None  # compatibility seam used by existing tests
_backup_s3_target: _BackupS3Target | None = None
_backup_s3_lock = threading.RLock()
_backup_s3_last_signature: str | None = None


def _backup_s3_config() -> tuple[str, str, str, str, str]:
    # Runtime configuration is applied to ``_overlay`` in one ``dict.update``
    # operation.  Snapshot that mapping once, rather than resolving five
    # attributes independently: a concurrent admin update must yield either
    # the old complete target or the new complete target, never a bucket from
    # one credential set combined with a secret from another.
    snapshot = dict(_overlay)
    frozen = settings._frozen  # type: ignore[attr-defined]

    def value(name: str, default: str) -> str:
        configured = snapshot.get(name)
        if configured is None:
            configured = getattr(frozen, name)
        return str(configured or default)

    return (
        value("backup_s3_bucket", ""),
        value("backup_s3_endpoint_url", ""),
        value("backup_s3_region", "auto"),
        value("backup_s3_access_key", ""),
        value("backup_s3_secret_key", ""),
    )


def _stable_backup_s3_config() -> tuple[str, str, str, str, str]:
    """Read the complete target tuple atomically enough for env/admin flips.

    Runtime settings are ordinary attributes and an admin update changes more
    than one of them.  Never construct a client from a mixed bucket/credential
    tuple: retry until two consecutive snapshots agree.
    """
    previous = _backup_s3_config()
    for _ in range(3):
        current = _backup_s3_config()
        if current == previous:
            return current
        previous = current
    raise _BackupConfigUnstableError("backup_s3_config_changed")


def _backup_s3_signature(
    config: tuple[str, str, str, str, str] | None = None,
) -> str:
    """Return a non-secret fingerprint of the effective S3 target."""
    values = config or _backup_s3_config()
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _normalize_provider_endpoint(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise ValueError("backup_s3_endpoint_invalid") from None
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("backup_s3_endpoint_invalid")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("backup_s3_endpoint_invalid")
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("backup_s3_endpoint_invalid") from None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port in {None, 80} and parsed.scheme.lower() == "http":
        netloc = host
    elif port in {None, 443} and parsed.scheme.lower() == "https":
        netloc = host
    else:
        netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), "", ""))


def _backup_provider_ref(config: tuple[str, str, str, str, str]) -> str:
    _, endpoint, region, _, _ = config
    payload = {
        "backend": "backup-s3",
        "provider": "backup-s3",
        "transport": "s3",
        "endpoint": _normalize_provider_endpoint(endpoint),
        "region": str(region or "").strip().lower(),
        "addressing_style": "path",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _get_backup_s3() -> Any:
    """Return a boto3 S3 client for the backup bucket, or None if not configured."""
    global _backup_s3, _backup_s3_target, _backup_s3_last_signature
    with _backup_s3_lock:
        try:
            config = _stable_backup_s3_config()
        except _BackupConfigUnstableError:
            # Fail closed rather than constructing a client from a mixed
            # bucket/endpoint/credential tuple. A later operation retries.
            _backup_s3 = False
            _backup_s3_target = None
            _backup_s3_last_signature = None
            return None
        bucket, endpoint, region, access_key, secret_key = config
        signature = _backup_s3_signature(config)
        # A disabled or failed target must not poison the cache after an admin
        # fixes credentials/endpoint.  The signature is intentionally hashed so
        # secrets never enter logs or reprs.
        if signature == _backup_s3_last_signature:
            return None if _backup_s3 is False else _backup_s3
        _backup_s3_last_signature = signature
        _backup_s3_target = None
        _backup_s3 = None
        if not bucket:
            _backup_s3 = False
            return None
        try:
            import boto3
            from botocore.config import Config as BotoConfig

            kwargs: dict = {
                "service_name": "s3",
                "region_name": region,
                "aws_access_key_id": access_key or None,
                "aws_secret_access_key": secret_key or None,
                "config": BotoConfig(
                    signature_version="s3v4", s3={"addressing_style": "path"}
                ),
            }
            if endpoint:
                kwargs["endpoint_url"] = endpoint
            _backup_s3 = boto3.client(**kwargs)
            _backup_s3_target = _BackupS3Target(
                client=_backup_s3,
                bucket=bucket,
                signature=signature,
                provider_ref=_backup_provider_ref(config),
            )
            logger.info("backup: S3 client initialised for configured target")
            return _backup_s3
        except Exception:
            logger.warning("backup: failed to initialise S3 client", exc_info=True)
            _backup_s3 = False
            # Do not memoize an outage forever; the next operation retries so
            # an endpoint becoming reachable does not require a process
            # restart.
            _backup_s3_last_signature = None
            return None


def _get_backup_s3_target() -> _BackupS3Target | None:
    """Capture client and bucket atomically for one backup operation."""
    with _backup_s3_lock:
        client = _get_backup_s3()
        if not client:
            return None
        target = _backup_s3_target
        if target is not None and target.client is client:
            return target
        # Tests and integrations replace _get_backup_s3 with a fake. Keep that
        # seam safe while still snapshotting the bucket for the operation.
        try:
            config = _stable_backup_s3_config()
        except _BackupConfigUnstableError:
            return None
        return _BackupS3Target(
            client,
            config[0],
            _backup_s3_signature(config),
            _backup_provider_ref(config),
        )


def _backup_s3_key(archive_name: str) -> str:
    return f"{_BACKUP_S3_PREFIX}{archive_name}"


def _source_ref(
    *, location: str, namespace: str | None, path: str, provider_ref: str | None = None
) -> str:
    """Opaque, stable locator identity (never contains credentials)."""
    raw = f"{provider_ref or ''}\x1f{location}\x1f{namespace or ''}\x1f{path}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _s3_prefix_for_key(key: str) -> str | None:
    for prefix in (_BACKUP_S3_PREFIX, _LEGACY_BACKUP_S3_PREFIX):
        if key.startswith(prefix):
            return prefix
    return None


def _backup_id_from_archive_name(name: str) -> str:
    if not name.endswith(".tar.gz"):
        raise ValueError("backup_key_invalid")
    stem = name.removesuffix(".tar.gz")
    backup_id = stem.rsplit("-", 1)[-1]
    if not backup_id:
        raise ValueError("backup_key_invalid")
    return backup_id


def _is_direct_remote_backup_key(key: str, prefix: str) -> bool:
    """Accept only a portable archive name directly under the reserved prefix."""
    if not key or "\\" in key:
        return False
    path = PurePosixPath(key)
    root = PurePosixPath(prefix.rstrip("/"))
    return not path.is_absolute() and ".." not in path.parts and path.parent == root


def _s3_object_kwargs(
    *, bucket: str, key: str, row: OwnedStorageObject, delete: bool = False
) -> dict[str, str]:
    """Build an immutable S3 locator proof for HEAD/GET/DELETE.

    Version ids are stronger than ETags and must be used whenever present. An
    unversioned object is only safe when the provider gave us an ETag that can
    be sent as a conditional request.  In particular, never perform an
    unconditional delete after a check-then-delete race.
    """
    kwargs: dict[str, str] = {"Bucket": bucket, "Key": key}
    if row.version_id:
        kwargs["VersionId"] = row.version_id
    elif row.etag:
        kwargs["IfMatch"] = row.etag
    else:
        # Reads are destructive in practice too: downloading an object that
        # cannot be bound to an immutable locator can restore the wrong bytes.
        # Require a VersionId or conditional ETag for every remote operation,
        # not only DELETE.
        raise BackupOwnershipError("backup_remote_identity_unavailable")
    return kwargs


def _s3_head_owned(target: _BackupS3Target, row: OwnedStorageObject) -> dict:
    return target.client.head_object(
        **_s3_object_kwargs(bucket=target.bucket, key=row.key, row=row)
    )


def _s3_get_owned(target: _BackupS3Target, row: OwnedStorageObject) -> dict:
    return target.client.get_object(
        **_s3_object_kwargs(bucket=target.bucket, key=row.key, row=row)
    )


def _assert_s3_identity(
    response: dict, *, size_bytes: int | None, etag: str | None, version_id: str | None
) -> None:
    """Require a response to describe the exact object selected by the ledger."""
    if size_bytes is not None and int(response.get("ContentLength", -1)) != size_bytes:
        raise BackupOwnershipError("backup_remote_size_changed")
    if etag is not None and str(response.get("ETag", "")) != etag:
        raise BackupOwnershipError("backup_remote_etag_changed")
    if version_id is not None and str(response.get("VersionId", "")) != version_id:
        raise BackupOwnershipError("backup_remote_version_changed")


def _s3_identity_kwargs(*, bucket: str, key: str, response: dict) -> dict[str, str]:
    """Build a conditional locator from a just-captured S3 identity."""
    version_id = response.get("VersionId")
    etag = response.get("ETag")
    if version_id:
        return {"Bucket": bucket, "Key": key, "VersionId": str(version_id)}
    if etag:
        return {"Bucket": bucket, "Key": key, "IfMatch": str(etag)}
    raise BackupOwnershipError("backup_remote_identity_unavailable")


def _assert_same_s3_identity(actual: dict, expected: dict) -> None:
    """Require both identity components returned by a proof to be preserved."""
    _require_remote_identity(expected)
    _require_remote_identity(actual)
    if actual.get("VersionId") != expected.get("VersionId"):
        raise BackupOwnershipError("backup_remote_version_changed")
    if actual.get("ETag") != expected.get("ETag"):
        raise BackupOwnershipError("backup_remote_etag_changed")


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
                _validate_restore_key(blob.key)
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
def create_backup(*, trigger: BackupTrigger = BackupTrigger.MANUAL) -> BackupMeta:
    """Create a full vault backup: DB + all stored files as a tar.gz.

    The archive is staged locally while it is built, then published only to
    the destinations selected for this trigger. At least one publication must
    succeed; a remote-only backup never leaves a registered local copy.
    """
    _require_database_backup_support()
    keep_local = local_destination_enabled(trigger)
    remote_destinations = configured_destinations(trigger)
    target = _get_backup_s3_target()
    if not keep_local and target is None and not remote_destinations:
        raise RuntimeError("backup_destination_required")
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
        manifest_namespaces = sorted(
            {str(entry["namespace"]) for entry in file_entries}
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
            "namespace": (
                manifest_namespaces[0] if len(manifest_namespaces) == 1 else None
            ),
            "namespaces": manifest_namespaces,
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
        except Exception:
            archive_temp.unlink(missing_ok=True)
            logger.exception("backup %s failed while streaming owned blobs", backup_id)
            raise

    final_size = archive_temp.stat().st_size
    archive_sha256 = _sha256_path(archive_temp)
    created_sources: list[BackupMeta] = []

    if keep_local:
        try:
            local_backend = LocalStorageBackend()
            local_namespace = local_backend.namespace_for(str(archive_path))
            local_provider_ref = provider_ref_for_backend(
                local_backend, namespace=local_namespace
            )
            with get_session_factory().session() as publish_session:
                local_receipt = publish_file(
                    publish_session,
                    local_backend,
                    str(archive_path),
                    archive_temp,
                    object_kind="backup",
                )
                publish_session.commit()
            created_sources.append(
                BackupMeta(
                    id=backup_id,
                    created_at=ts,
                    size_bytes=local_receipt.size,
                    storage_backend=backend_name,
                    file_count=len(file_entries),
                    app_version=settings.app_version,
                    path=str(archive_path),
                    location="local",
                    archive_sha256=archive_sha256,
                    provider_ref=local_provider_ref,
                    namespace=local_namespace,
                    source_ref=_source_ref(
                        location="local",
                        namespace=local_namespace,
                        path=str(archive_path),
                        provider_ref=local_provider_ref,
                    ),
                )
            )
            logger.info(
                "backup %s created locally: %d files, %.1f MiB",
                backup_id,
                written_files,
                final_size / (1024 * 1024),
            )
        except Exception:
            logger.warning(
                "backup %s: local publication failed", backup_id, exc_info=True
            )

    # Upload to S3 if configured
    if target:
        s3 = target.client
        bucket = target.bucket
        try:
            s3_key = _backup_s3_key(archive_name)
            namespace = f"{bucket}/{_BACKUP_S3_PREFIX}"
            token = uuid.uuid4().hex
            with get_session_factory().session() as reservation_session:
                reservation = OwnedStorageObject(
                    backend="backup-s3",
                    namespace=namespace,
                    key=s3_key,
                    object_kind="backup",
                    provider_ref=target.provider_ref,
                    state=StorageObjectState.PENDING,
                    size_bytes=final_size,
                    sha256=archive_sha256,
                    token=token,
                )
                reservation_session.add(reservation)
                reservation_session.commit()
            with archive_temp.open("rb") as source:
                s3.put_object(
                    Bucket=bucket,
                    Key=s3_key,
                    Body=source,
                    IfNoneMatch="*",
                    Metadata={"printstash-create-token": token},
                )
            # A PUT response is not a portable proof (notably, many S3
            # compatible services omit VersionId or return a non-content
            # ETag). Capture the object identity from HEAD before committing
            # the ledger row, and ensure the create token/size still match.
            response = s3.head_object(Bucket=bucket, Key=s3_key)
            _require_remote_identity(response)
            if (
                int(response.get("ContentLength", -1)) != final_size
                or response.get("Metadata", {}).get("printstash-create-token") != token
            ):
                raise RuntimeError("backup_publication_evidence_mismatch")
            s3_receipt = CreationReceipt(
                key=s3_key,
                size=final_size,
                token=token,
                backend="backup-s3",
                namespace=namespace,
                etag=str(response.get("ETag")) if response.get("ETag") else None,
                version_id=(
                    str(response.get("VersionId"))
                    if response.get("VersionId")
                    else None
                ),
            )
            with get_session_factory().session() as commit_session:
                record_creation(
                    commit_session,
                    s3_receipt,
                    object_kind="backup",
                    provider_ref=target.provider_ref,
                )
                commit_session.commit()
            created_sources.append(
                BackupMeta(
                    id=backup_id,
                    created_at=ts,
                    size_bytes=final_size,
                    storage_backend=backend_name,
                    file_count=len(file_entries),
                    app_version=settings.app_version,
                    path=s3_key,
                    location="s3",
                    archive_sha256=archive_sha256,
                    provider_ref=target.provider_ref,
                    namespace=namespace,
                    source_ref=_source_ref(
                        location="s3",
                        namespace=namespace,
                        path=s3_key,
                        provider_ref=target.provider_ref,
                    ),
                )
            )
            logger.info("backup %s uploaded to S3: %s", backup_id, s3_key)
        except Exception:
            logger.warning("backup %s: S3 upload failed", backup_id, exc_info=True)

    # Purpose-scoped connections are independent replicas. A failed remote
    # destination never invalidates the already committed local archive, and a
    # failure at one provider does not prevent the remaining replicas.
    for destination in remote_destinations:
        try:
            remote_key = destination.key(archive_name)
            with get_session_factory().session() as remote_session:
                remote_receipt = publish_file(
                    remote_session,
                    destination.backend,
                    remote_key,
                    archive_temp,
                    object_kind="backup",
                    sha256=archive_sha256,
                    provider_ref=destination.provider_ref,
                )
                remote_session.commit()
            created_sources.append(
                BackupMeta(
                    id=backup_id,
                    created_at=ts,
                    size_bytes=remote_receipt.size,
                    storage_backend=backend_name,
                    file_count=len(file_entries),
                    app_version=settings.app_version,
                    path=remote_key,
                    location=destination.location,
                    archive_sha256=archive_sha256,
                    provider_ref=destination.provider_ref,
                    namespace=destination.namespace,
                    source_ref=_source_ref(
                        location=destination.location,
                        namespace=destination.namespace,
                        path=remote_key,
                        provider_ref=destination.provider_ref,
                    ),
                )
            )
            logger.info(
                "backup %s replicated through OpenDAL provider %s",
                backup_id,
                destination.provider,
            )
        except Exception:
            logger.warning(
                "backup %s: OpenDAL replica %s failed",
                backup_id,
                destination.name,
                exc_info=True,
            )

    archive_temp.unlink(missing_ok=True)
    if not created_sources:
        raise RuntimeError("backup_all_destinations_failed")

    # Destination ledger rows were committed immediately after each
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
                "trigger": trigger.value,
                "locations": [source.location for source in created_sources],
            },
        )
        session.commit()

    return created_sources[0]


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


def reconcile_backup_publications(limit: int = 100) -> int:
    """Finish or block backup reservations left across a publication crash."""
    # Cache projections have a separate lifecycle from remote backup
    # publications.  Reconcile them before touching backup rows so an absent
    # cloud target can never make a cache row look like a root backup.
    reconcile_backup_caches(limit=limit)
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
        target = _get_backup_s3_target()
        s3 = target.client if target else None
        bucket = target.bucket if target else ""
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
                    # A config flip must not probe a new bucket using an old
                    # pending row's key. Leave the old target retryable until
                    # the administrator restores that exact configuration.
                    row_bucket = row.namespace.split("/", 1)[0]
                    if not bucket:
                        # Compatibility for injected test clients; a real
                        # configured target always carries its bucket.
                        bucket = row_bucket
                        target = replace(target, bucket=bucket)
                        s3 = target.client
                    if row_bucket != bucket:
                        continue
                    if row.provider_ref and row.provider_ref != target.provider_ref:
                        row.last_error = "retryable:backup_target_changed"
                        session.add(row)
                        continue
                    # A reservation is intentionally written before PUT, so it
                    # has no remote ETag/version yet.  This one reconciliation
                    # probe is the only permitted unconditional S3 read: the
                    # row's persisted namespace selects the bucket, and the
                    # response supplies the immutable identity used for every
                    # subsequent conditional operation.
                    response = s3.head_object(Bucket=bucket, Key=row.key)
                    metadata = response.get("Metadata", {})
                    if (
                        not row.token
                        or not row.sha256
                        or metadata.get("printstash-create-token") != row.token
                        or int(response.get("ContentLength", -1)) != row.size_bytes
                        or not response.get("VersionId")
                        and not response.get("ETag")
                    ):
                        raise RuntimeError("backup_publication_evidence_mismatch")
                    # HEAD supplies only identity/size metadata.  It has no
                    # response body, so fetch the exact object separately for
                    # the digest and archive validation proof.
                    get_kwargs: dict[str, str] = {
                        "Bucket": bucket,
                        "Key": row.key,
                    }
                    if response.get("VersionId"):
                        get_kwargs["VersionId"] = str(response["VersionId"])
                    else:
                        get_kwargs["IfMatch"] = str(response["ETag"])
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
                        if digest.hexdigest() != row.sha256:
                            raise RuntimeError("backup_publication_digest_mismatch")
                        _validate_created_archive_payload(candidate)
                    finally:
                        candidate.unlink(missing_ok=True)
                    confirm_kwargs: dict[str, str] = {
                        "Bucket": bucket,
                        "Key": row.key,
                    }
                    if response.get("VersionId"):
                        confirm_kwargs["VersionId"] = str(response["VersionId"])
                    else:
                        confirm_kwargs["IfMatch"] = str(response["ETag"])
                    confirmed = s3.head_object(**confirm_kwargs)
                    if confirmed.get("VersionId") != response.get(
                        "VersionId"
                    ) or confirmed.get("ETag") != response.get("ETag"):
                        raise RuntimeError("backup_publication_evidence_mismatch")
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
                elif row.backend.startswith("backup-opendal-"):
                    destination = destination_for_ownership(row)
                    if destination is None:
                        row.last_error = "retryable:backup_target_changed"
                        session.add(row)
                        continue
                    info = destination.backend.object_info(row.key)
                    if (
                        info is None
                        or row.size_bytes is None
                        or row.sha256 is None
                        or info.size != row.size_bytes
                    ):
                        raise RuntimeError("backup_publication_evidence_mismatch")
                    fd, raw_name = tempfile.mkstemp(
                        prefix=".printstash-backup-reconcile-",
                        dir=settings.backup_dir,
                    )
                    os.close(fd)
                    candidate = Path(raw_name)
                    candidate.unlink()
                    try:
                        committed = OwnedStorageObject.model_validate(row.model_dump())
                        committed.state = StorageObjectState.COMMITTED
                        destination.download_owned(committed, candidate)
                        _validate_created_archive_payload(candidate)
                    finally:
                        candidate.unlink(missing_ok=True)
                    receipt = CreationReceipt(
                        key=row.key,
                        size=info.size,
                        token=row.token or "",
                        backend=row.backend,
                        namespace=row.namespace,
                        etag=info.etag,
                        version_id=info.version_id,
                        provider_ref=row.provider_ref,
                    )
                else:
                    if row.backend == "backup-s3" or row.backend.startswith(
                        "backup-opendal-"
                    ):
                        # A provider outage is not evidence that the
                        # publication is corrupt. Keep the reservation pending
                        # so a later reconciliation can prove this exact
                        # namespace rather than making it permanently blocked.
                        row.last_error = "retryable:backup_provider_unavailable"
                        session.add(row)
                        continue
                    raise RuntimeError("backup_publication_backend_unavailable")
                assert receipt is not None
                complete_publication(
                    session,
                    int(row.id),
                    receipt,
                    object_kind="backup",
                    sha256=row.sha256,
                    provider_ref=(row.provider_ref or target.provider_ref)
                    if row.backend == "backup-s3"
                    else row.provider_ref
                    if row.backend.startswith("backup-opendal-")
                    else provider_ref_for_backend(
                        LocalStorageBackend(), namespace=row.namespace
                    ),
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


def _cache_path_pinned_by_restore_journal(path: str) -> bool:
    """Return whether an unresolved restore names this exact cache locator."""
    try:
        journals = settings.backup_dir.glob(".restore-*.journal")
        for journal in journals:
            try:
                state = _load_restore_journal(journal)
            except RestoreConflictError:
                # Arbitrary or malformed text is not ownership evidence for a
                # cache.  It may keep restore maintenance fail-closed, but it
                # must not pin an unrelated derivative forever.
                continue
            if str(path) in state.cache_paths:
                return True
        return False
    except OSError:
        # An unreadable journal directory is already fail-closed for restore;
        # retaining the cache is the safe choice for reconciliation as well.
        return True


def reconcile_backup_caches(limit: int = 100) -> int:
    """Reconcile only crash-staged per-source cloud cache projections.

    Cache rows are deliberately not handled by the generic storage sweep: a
    journal may still need the exact file after a database swap.  Missing rows
    are safe to clear; present rows are never deleted on a hash mismatch.
    """
    reconciled = 0
    backend = LocalStorageBackend()
    with get_session_factory().session() as session:
        rows = session.exec(
            select(OwnedStorageObject)
            .where(
                OwnedStorageObject.backend == "local",
                OwnedStorageObject.object_kind == "backup-cloud-cache",
                OwnedStorageObject.state.in_(
                    (StorageObjectState.PENDING, StorageObjectState.COMMITTED)
                ),
            )
            .order_by(OwnedStorageObject.id.asc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
        for row in rows:
            if _cache_path_pinned_by_restore_journal(row.key):
                continue
            path = Path(row.key).resolve(strict=False)
            cache_root = (settings.backup_dir / ".cloud-cache").resolve(strict=False)
            if path.parent != cache_root or not path.name:
                row.state = StorageObjectState.BLOCKED
                row.last_error = "backup_cache_path_invalid"
                session.add(row)
                continue
            if not path.exists():
                session.delete(row)
                reconciled += 1
                continue
            if row.state == StorageObjectState.COMMITTED:
                created_at = row.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age_s = (utcnow() - created_at).total_seconds()
                if age_s >= _BACKUP_CACHE_STALE_AFTER_S:
                    # A committed cache is a rebuildable projection, not a
                    # root backup.  Reclaim only an exact, still-provable
                    # receipt; pinned journals were filtered above.
                    try:
                        if delete_owned_key(session, backend, str(path)):
                            reconciled += 1
                    except Exception as exc:
                        row.state = StorageObjectState.BLOCKED
                        row.last_error = type(exc).__name__[:255]
                        session.add(row)
                    continue
            if row.size_bytes is None or not row.sha256:
                row.state = StorageObjectState.BLOCKED
                row.last_error = "backup_cache_evidence_missing"
                session.add(row)
                continue
            try:
                receipt = backend.adopt_existing(
                    str(path),
                    expected_size=row.size_bytes,
                    expected_sha256=row.sha256,
                )
                if row.state == StorageObjectState.PENDING:
                    complete_publication(
                        session,
                        int(row.id),
                        receipt,
                        object_kind="backup-cloud-cache",
                        sha256=row.sha256,
                        provider_ref=provider_ref_for_backend(
                            backend, namespace=backend.namespace_for(str(path))
                        ),
                    )
                reconciled += 1
            except Exception as exc:
                row.state = StorageObjectState.BLOCKED
                row.last_error = type(exc).__name__[:255]
                session.add(row)
        session.commit()
    return reconciled


def _committed_backup_keys(
    backend: str,
    namespace: str | None = None,
    provider_ref: str | None = None,
) -> set[str]:
    """Return only archives whose publication reached COMMITTED in the ledger."""
    with get_session_factory().session() as session:
        statement = select(OwnedStorageObject.key).where(
            OwnedStorageObject.backend == backend,
            OwnedStorageObject.object_kind.in_(("backup", "backup-legacy")),
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
        if namespace is not None:
            statement = statement.where(OwnedStorageObject.namespace == namespace)
        if provider_ref is not None:
            statement = statement.where(OwnedStorageObject.provider_ref == provider_ref)
        return {str(key) for key in session.exec(statement).all()}


def _backup_ownership_rows(
    *, key: str | None = None, bucket: str | None = None
) -> list[OwnedStorageObject]:
    """Load committed/incomplete S3 backup evidence for exact locators."""
    with get_session_factory().session() as session:
        statement = select(OwnedStorageObject).where(
            OwnedStorageObject.backend == "backup-s3",
            OwnedStorageObject.object_kind.in_(("backup", "backup-legacy")),
            OwnedStorageObject.state.in_(
                (StorageObjectState.COMMITTED, StorageObjectState.BLOCKED)
            ),
        )
        if key is not None:
            statement = statement.where(OwnedStorageObject.key == key)
        if bucket is not None:
            statement = statement.where(
                OwnedStorageObject.namespace.startswith(f"{bucket}/")
            )
        return list(session.exec(statement).all())


def _list_local_backups() -> list[BackupMeta]:
    results: list[BackupMeta] = []
    if not settings.backup_dir.exists():
        return results
    local = LocalStorageBackend()
    local_namespace = f"backup:{settings.backup_dir.expanduser().resolve(strict=False)}"
    committed = _committed_backup_keys(
        "local",
        provider_ref=provider_ref_for_backend(local, namespace=local_namespace),
    )

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
                meta.archive_sha256 = _sha256_path(archive)
                meta.namespace = LocalStorageBackend().namespace_for(str(archive))
                meta.provider_ref = provider_ref_for_backend(
                    LocalStorageBackend(), namespace=meta.namespace
                )
                meta.source_ref = _source_ref(
                    location="local",
                    namespace=meta.namespace,
                    path=meta.path,
                    provider_ref=meta.provider_ref,
                )
                results.append(meta)
        except Exception:
            logger.warning("backup: cannot read manifest from %s", archive.name)

    return results


def _list_s3_backups() -> list[BackupMeta]:
    target = _get_backup_s3_target()
    if target is None:
        return []
    results: list[BackupMeta] = []
    s3, bucket = target.client, target.bucket
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for prefix in (_BACKUP_S3_PREFIX, _LEGACY_BACKUP_S3_PREFIX):
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = str(obj.get("Key", ""))
                    archive_name = key.rsplit("/", 1)[-1]
                    if not archive_name.startswith(
                        (_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX)
                    ):
                        continue
                    try:
                        namespace = f"{bucket}/{prefix}"
                        ownership = next(
                            (
                                row
                                for row in _backup_ownership_rows(
                                    key=key, bucket=bucket
                                )
                                if row.namespace == namespace
                                and row.state == StorageObjectState.COMMITTED
                                and row.provider_ref == target.provider_ref
                            ),
                            None,
                        )
                        # Unowned/legacy objects remain in the explicit admin
                        # discovery view. Listing is ownership-only.
                        # A content hash alone does not bind an operation to a
                        # stable remote object. Historical rows lacking both a
                        # version id and an ETag are therefore fail-closed and
                        # must be re-adopted after an operator can prove the
                        # exact object identity.
                        if (
                            ownership is None
                            or not ownership.sha256
                            or (not ownership.version_id and not ownership.etag)
                        ):
                            continue
                        head = _s3_head_owned(target, ownership)
                        if (
                            int(head.get("ContentLength", -1)) != ownership.size_bytes
                            or (
                                ownership.etag
                                and str(head.get("ETag", "")) != ownership.etag
                            )
                            or (
                                ownership.version_id
                                and str(head.get("VersionId", ""))
                                != ownership.version_id
                            )
                            or (
                                ownership.object_kind == "backup"
                                and head.get("Metadata", {}).get(
                                    "printstash-create-token"
                                )
                                != ownership.token
                            )
                        ):
                            continue
                        # Listing is a metadata operation. The durable ledger
                        # already carries the archive's content hash and exact
                        # remote identity; do not download and fully validate
                        # every historical archive whenever the UI refreshes.
                        # The GET is conditional and consumes only the first
                        # manifest member (created archives put it first).
                        downloaded = _s3_get_owned(target, ownership)
                        body = downloaded.get("Body")
                        try:
                            _assert_s3_identity(
                                downloaded,
                                size_bytes=ownership.size_bytes,
                                etag=ownership.etag,
                                version_id=ownership.version_id,
                            )
                            if body is None or not hasattr(body, "read"):
                                continue
                            meta = _read_manifest_from_stream(body)
                        finally:
                            if body is not None and hasattr(body, "close"):
                                body.close()
                        confirmed = _s3_head_owned(target, ownership)
                        _assert_s3_identity(
                            confirmed,
                            size_bytes=ownership.size_bytes,
                            etag=ownership.etag,
                            version_id=ownership.version_id,
                        )
                        if meta is None:
                            continue
                        meta.id = _backup_id_from_archive_name(archive_name)
                        meta.path = key
                        meta.location = "s3"
                        meta.size_bytes = int(ownership.size_bytes)
                        meta.archive_sha256 = ownership.sha256
                        meta.provider_ref = ownership.provider_ref
                        meta.namespace = namespace
                        meta.source_ref = _source_ref(
                            location="s3",
                            namespace=namespace,
                            path=key,
                            provider_ref=ownership.provider_ref,
                        )
                        results.append(meta)
                    except Exception:
                        logger.warning("backup: cannot read S3 manifest for %s", key)
                        continue
    except Exception:
        logger.warning("backup: failed to list S3 backups", exc_info=True)

    return results


def _list_opendal_backups() -> list[BackupMeta]:
    """List owned replicas for the currently configured OpenDAL destinations."""
    results: list[BackupMeta] = []
    for destination in configured_destinations():
        try:
            with get_session_factory().scoped_session() as session:
                rows = session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.backend == destination.backend.backend_name,
                        OwnedStorageObject.namespace == destination.namespace,
                        OwnedStorageObject.provider_ref == destination.provider_ref,
                        OwnedStorageObject.object_kind.in_(("backup", "backup-legacy")),
                        OwnedStorageObject.state == StorageObjectState.COMMITTED,
                    )
                ).all()
                for row in rows:
                    try:
                        with destination.open_owned(row) as body:
                            meta = _read_manifest_from_stream(body)
                        if meta is None:
                            continue
                        archive_name = row.key.rsplit("/", 1)[-1]
                        meta.id = _backup_id_from_archive_name(archive_name)
                        meta.path = row.key
                        meta.location = destination.location
                        meta.size_bytes = int(row.size_bytes or 0)
                        meta.archive_sha256 = row.sha256
                        meta.provider_ref = row.provider_ref
                        meta.namespace = row.namespace
                        meta.source_ref = _source_ref(
                            location=destination.location,
                            namespace=row.namespace,
                            path=row.key,
                            provider_ref=row.provider_ref,
                        )
                        results.append(meta)
                    except Exception:
                        logger.warning(
                            "backup: cannot read OpenDAL manifest for %s", row.key
                        )
        except Exception:
            logger.warning(
                "backup: failed to list OpenDAL destination %s",
                destination.name,
                exc_info=True,
            )
    return results


def list_backups() -> list[BackupMeta]:
    """List all backups: local + S3, sorted by date descending."""
    reconcile_backup_publications()
    sources = list_backup_sources(reconcile=False)
    grouped: dict[str, list[BackupMeta]] = {}
    for item in sources:
        grouped.setdefault(item.id, []).append(item)
    merged: list[BackupMeta] = []
    for candidates in grouped.values():
        hashes = {m.archive_sha256 for m in candidates}
        if len(candidates) > 1 and (None in hashes or len(hashes) != 1):
            # Preserve visibility of an ambiguous id.  ``get_backup`` still
            # fails closed until the caller supplies the exact source_ref.
            merged.extend(candidates)
        else:
            merged.append(min(candidates, key=_backup_precedence))
    merged.sort(key=lambda m: m.created_at, reverse=True)
    return merged


def list_backup_sources(*, reconcile: bool = True) -> list[BackupMeta]:
    """Return every owned source, including identical replicas.

    This is the restart-stable source contract.  ``canonical`` is merely the
    display winner under local > current S3 > legacy S3 precedence; operations
    must always use the opaque ``source_ref``.
    """
    if reconcile:
        reconcile_backup_publications()
    sources = [*_list_local_backups(), *_list_s3_backups(), *_list_opendal_backups()]
    grouped: dict[str, list[BackupMeta]] = {}
    for item in sources:
        grouped.setdefault(item.id, []).append(item)
    for candidates in grouped.values():
        ordered = sorted(candidates, key=_backup_precedence)
        hashes = {item.archive_sha256 for item in candidates}
        safe_canonical = len(candidates) == 1 or (
            None not in hashes and len(hashes) == 1
        )
        for rank, item in enumerate(ordered):
            item.precedence = rank
            item.canonical = safe_canonical and rank == 0
    sources.sort(key=lambda m: m.created_at, reverse=True)
    return sources


def _backup_precedence(meta: BackupMeta) -> tuple[int, str]:
    if meta.location == "local":
        return (0, meta.path)
    if meta.path.startswith(_BACKUP_S3_PREFIX):
        return (1, meta.path)
    if meta.location.startswith("opendal:"):
        return (2, f"{meta.location}:{meta.path}")
    return (3, meta.path)


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


def _read_manifest_from_stream(body: Any) -> BackupMeta | None:
    """Read the first manifest from an S3 body without draining the archive.

    Backup creation writes ``manifest.json`` as the first tar member.  The
    stream mode therefore lets listing release the response after a small,
    bounded read instead of downloading and validating the entire archive.
    Full member/hash/SQLite validation belongs to explicit verification and
    recovery paths.
    """
    with gzip.GzipFile(fileobj=body, mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            first = next(iter(tar), None)
            if first is None or first.name != "manifest.json" or not first.isfile():
                return None
            stream = tar.extractfile(first)
            if stream is None:
                return None
            try:
                parsed = json.loads(stream.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
            if not isinstance(parsed, dict):
                return None
            return BackupMeta(
                id="",
                created_at=str(parsed["created_at"]),
                size_bytes=0,
                storage_backend=str(parsed.get("storage_backend", "local")),
                file_count=int(parsed.get("file_count", 0)),
                app_version=str(parsed.get("app_version", "unknown")),
                path="",
                location="s3",
            )


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
    # Use the same complete validator as operator verification, including
    # exact manifest membership, member sizes/digests, safe paths, and the
    # archived SQLite integrity check.  Discovery and adoption must never
    # commit a merely parseable tarball.
    verification = verify_backup(meta.id, archive_path=archive_path, record_audit=False)
    if not verification.valid:
        raise RuntimeError(
            str(verification.findings[0].get("code", "backup_manifest_invalid"))
        )
    with tarfile.open(archive_path, mode="r:gz") as tar:
        members = tar.getmembers()
        if any(
            _unsafe_member_name(member.name) or member.issym() or member.islnk()
            for member in members
        ):
            raise RuntimeError("backup_manifest_invalid")
        manifest, entries = _restore_manifest_entries(tar)
        declared = set(entries)
        regular = {member.name for member in members if member.isfile()}
        if regular != {"manifest.json", "db.sqlite3", *declared}:
            raise RuntimeError("backup_manifest_invalid")
        if manifest.get("file_count") is not None and manifest.get("file_count") != len(
            entries
        ):
            raise RuntimeError("backup_manifest_invalid")
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


def _download_s3_archive(
    target: _BackupS3Target,
    key: str,
    row: OwnedStorageObject | None = None,
    *,
    version_id: str | None = None,
    etag: str | None = None,
) -> tuple[Path, dict]:
    """Download one exact remote object into a private temporary file."""
    if row is not None:
        response = _s3_get_owned(target, row)
    else:
        kwargs: dict[str, str] = {"Bucket": target.bucket, "Key": key}
        if version_id:
            kwargs["VersionId"] = version_id
        elif etag:
            kwargs["IfMatch"] = etag
        else:
            raise BackupOwnershipError("backup_remote_identity_unavailable")
        response = target.client.get_object(**kwargs)
    fd, raw = tempfile.mkstemp(prefix=".printstash-s3-adopt-", dir=settings.backup_dir)
    os.close(fd)
    path = Path(raw)
    body = response["Body"]
    try:
        with path.open("wb") as output:
            shutil.copyfileobj(body, output)
    finally:
        body.close()
    return path, response


def _require_remote_identity(response: dict) -> None:
    if not response.get("VersionId") and not response.get("ETag"):
        raise RuntimeError("backup_remote_identity_unavailable")


def discover_unowned_s3_backups() -> list[dict[str, object]]:
    """Find valid tokenless archives, without making them listable."""
    target = _get_backup_s3_target()
    if target is None:
        return []
    rows = _backup_ownership_rows(bucket=target.bucket)
    committed = {
        (row.namespace, row.key, row.provider_ref)
        for row in rows
        if row.state == StorageObjectState.COMMITTED
    }
    incomplete = {
        (row.namespace, row.key)
        for row in rows
        if row.state == StorageObjectState.BLOCKED
    }
    result: list[dict[str, object]] = []
    try:
        paginator = target.client.get_paginator("list_objects_v2")
        for prefix in (_BACKUP_S3_PREFIX, _LEGACY_BACKUP_S3_PREFIX):
            for page in paginator.paginate(Bucket=target.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = str(obj.get("Key", ""))
                    name = key.rsplit("/", 1)[-1]
                    namespace = f"{target.bucket}/{prefix}"
                    if (
                        (namespace, key, target.provider_ref) in committed
                        or not name.startswith(
                            (_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX)
                        )
                        or not name.endswith(".tar.gz")
                    ):
                        continue
                    temp: Path | None = None
                    try:
                        head_before = target.client.head_object(
                            Bucket=target.bucket, Key=key
                        )
                        _require_remote_identity(head_before)
                        temp, head = _download_s3_archive(
                            target,
                            key,
                            version_id=str(head_before.get("VersionId"))
                            if head_before.get("VersionId")
                            else None,
                            etag=str(head_before.get("ETag"))
                            if head_before.get("ETag")
                            else None,
                        )
                        if int(head.get("ContentLength", -1)) != int(
                            head_before.get("ContentLength", -1)
                        ):
                            raise RuntimeError("backup_remote_changed")
                        _assert_same_s3_identity(head, head_before)
                        meta = _validate_archive_for_adoption(temp)
                        meta.id = _backup_id_from_archive_name(name)
                        digest = _sha256_path(temp)
                        if temp.stat().st_size != int(
                            head_before.get("ContentLength", -1)
                        ):
                            raise RuntimeError("backup_download_size_mismatch")
                        head_after = target.client.head_object(
                            **_s3_identity_kwargs(
                                bucket=target.bucket, key=key, response=head_before
                            )
                        )
                        _assert_same_s3_identity(head_after, head_before)
                        result.append(
                            {
                                "key": key,
                                "backup_id": meta.id,
                                "created_at": meta.created_at,
                                "size_bytes": int(
                                    head.get("ContentLength", temp.stat().st_size)
                                ),
                                "file_count": meta.file_count,
                                "storage_backend": meta.storage_backend,
                                "app_version": meta.app_version,
                                "location": "s3",
                                # The bucket/prefix namespace is required for
                                # an operator to review and authorize one
                                # exact remote locator.  It contains no
                                # credentials and is already part of the
                                # opaque source-ref derivation.
                                "namespace": namespace,
                                "prefix": prefix,
                                "archive_sha256": digest,
                                "source_ref": _source_ref(
                                    location="s3",
                                    namespace=namespace,
                                    path=key,
                                    provider_ref=target.provider_ref,
                                ),
                                "provider_ref": target.provider_ref,
                                "candidate_kind": (
                                    "receipt_upgrade"
                                    if (namespace, key) in incomplete
                                    else "unowned_archive"
                                ),
                            }
                        )
                    except Exception:
                        logger.info(
                            "backup: unowned S3 archive failed validation: %s", key
                        )
                    finally:
                        if temp is not None:
                            temp.unlink(missing_ok=True)
    except Exception:
        logger.warning("backup: failed to discover unowned S3 backups", exc_info=True)
    return result


def _download_opendal_candidate(
    destination: RemoteBackupDestination, key: str
) -> tuple[Path, str, CreationReceipt]:
    """Download one unowned remote archive while pinning observable identity."""
    before = destination.backend.object_info(key)
    if before is None:
        raise FileNotFoundError(key)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(
        prefix=".printstash-opendal-adopt-", dir=settings.backup_dir
    )
    os.close(fd)
    path = Path(raw)
    digest = hashlib.sha256()
    written = 0
    try:
        with path.open("wb") as output:
            for chunk in destination.backend.stream_chunks(key):
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
        after = destination.backend.object_info(key)
        if (
            after is None
            or written != before.size
            or after.size != before.size
            or (before.etag and after.etag != before.etag)
            or (before.version_id and after.version_id != before.version_id)
        ):
            raise RuntimeError("backup_remote_changed")
        return (
            path,
            digest.hexdigest(),
            CreationReceipt(
                key=key,
                size=written,
                token=digest.hexdigest(),
                backend=destination.backend.backend_name,
                namespace=destination.namespace,
                etag=after.etag,
                version_id=after.version_id,
                provider_ref=destination.provider_ref,
            ),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise


def discover_unowned_opendal_backups() -> list[dict[str, object]]:
    """Find validated archives under every configured OpenDAL backup root."""
    result: list[dict[str, object]] = []
    for destination in configured_destinations():
        try:
            with get_session_factory().scoped_session() as session:
                committed = {
                    row.key
                    for row in session.exec(
                        select(OwnedStorageObject).where(
                            OwnedStorageObject.backend
                            == destination.backend.backend_name,
                            OwnedStorageObject.namespace == destination.namespace,
                            OwnedStorageObject.provider_ref == destination.provider_ref,
                            OwnedStorageObject.object_kind.in_(  # type: ignore[union-attr]
                                ("backup", "backup-legacy")
                            ),
                            OwnedStorageObject.state == StorageObjectState.COMMITTED,
                        )
                    ).all()
                }
            prefix_key = destination.key("")
            for key in destination.backend.list_prefix(prefix_key):
                name = key.rsplit("/", 1)[-1]
                if (
                    key in committed
                    or not _is_direct_remote_backup_key(key, prefix_key)
                    or not name.startswith(
                        (_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX)
                    )
                    or not name.endswith(".tar.gz")
                ):
                    continue
                temp: Path | None = None
                try:
                    temp, digest, receipt = _download_opendal_candidate(
                        destination, key
                    )
                    meta = _validate_archive_for_adoption(temp)
                    meta.id = _backup_id_from_archive_name(name)
                    source_ref = _source_ref(
                        location=destination.location,
                        namespace=destination.namespace,
                        path=key,
                        provider_ref=destination.provider_ref,
                    )
                    result.append(
                        {
                            "connection_id": destination.connection_id,
                            "connection_name": destination.name,
                            "provider": destination.provider,
                            "key": key,
                            "backup_id": meta.id,
                            "created_at": meta.created_at,
                            "size_bytes": receipt.size,
                            "file_count": meta.file_count,
                            "storage_backend": meta.storage_backend,
                            "app_version": meta.app_version,
                            "location": destination.location,
                            "namespace": destination.namespace,
                            "prefix": prefix_key,
                            "archive_sha256": digest,
                            "source_ref": source_ref,
                            "provider_ref": destination.provider_ref,
                            "candidate_kind": "unowned_archive",
                        }
                    )
                except Exception:
                    logger.info(
                        "backup: unowned OpenDAL archive failed validation: %s",
                        key,
                    )
                finally:
                    if temp is not None:
                        temp.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "backup: failed to discover OpenDAL backups from %s",
                destination.name,
                exc_info=True,
            )
    return result


def adopt_opendal_backup(
    connection_id: int,
    key: str,
    *,
    source_ref: str,
    expected_archive_sha256: str,
) -> BackupMeta:
    """Validate and ledger-adopt one exact OpenDAL archive in place."""
    destination = next(
        (
            item
            for item in configured_destinations()
            if item.connection_id == connection_id
        ),
        None,
    )
    if destination is None:
        raise RuntimeError("backup_remote_connection_unavailable")
    prefix_key = destination.key("")
    name = key.rsplit("/", 1)[-1]
    if (
        not _is_direct_remote_backup_key(key, prefix_key)
        or not name.startswith((_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX))
        or not name.endswith(".tar.gz")
    ):
        raise ValueError("backup_key_invalid")
    expected_source_ref = _source_ref(
        location=destination.location,
        namespace=destination.namespace,
        path=key,
        provider_ref=destination.provider_ref,
    )
    if source_ref != expected_source_ref:
        raise ValueError("backup_source_ref_mismatch")
    with get_session_factory().scoped_session() as session:
        existing = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == destination.backend.backend_name,
                OwnedStorageObject.namespace == destination.namespace,
                OwnedStorageObject.key == key,
                OwnedStorageObject.provider_ref == destination.provider_ref,
                OwnedStorageObject.object_kind.in_(  # type: ignore[union-attr]
                    ("backup", "backup-legacy")
                ),
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
            )
        ).first()
    if existing is not None:
        raise ValueError("backup_already_adopted")

    temp: Path | None = None
    try:
        temp, digest, receipt = _download_opendal_candidate(destination, key)
        if digest != expected_archive_sha256:
            raise RuntimeError("backup_archive_digest_mismatch")
        meta = _validate_archive_for_adoption(temp)
        meta.id = _backup_id_from_archive_name(name)
        with get_session_factory().session() as session:
            record_creation(
                session,
                receipt,
                object_kind="backup-legacy",
                sha256=digest,
                provider_ref=destination.provider_ref,
            )
            session.commit()
        meta.path = key
        meta.location = destination.location
        meta.size_bytes = receipt.size
        meta.archive_sha256 = digest
        meta.provider_ref = destination.provider_ref
        meta.namespace = destination.namespace
        meta.source_ref = expected_source_ref
        return meta
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def adopt_s3_backup(
    key: str,
    *,
    source_ref: str | None = None,
    expected_archive_sha256: str | None = None,
) -> BackupMeta:
    """Validate and ledger-adopt one remote archive without copying it."""
    if (
        not key
        or _s3_prefix_for_key(key) is None
        or key.endswith("/")
        or not source_ref
        or not expected_archive_sha256
    ):
        raise ValueError("backup_key_invalid")
    name = key.rsplit("/", 1)[-1]
    if not name.startswith((_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX)):
        raise ValueError("backup_key_invalid")
    target = _get_backup_s3_target()
    if target is None:
        raise RuntimeError("backup_s3_unavailable")
    prefix = _s3_prefix_for_key(key)
    assert prefix is not None
    namespace = f"{target.bucket}/{prefix}"
    with get_session_factory().session() as session:
        existing = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == "backup-s3",
                OwnedStorageObject.namespace == namespace,
                OwnedStorageObject.key == key,
                (
                    (OwnedStorageObject.provider_ref == target.provider_ref)
                    | OwnedStorageObject.provider_ref.is_(None)  # type: ignore[union-attr]
                ),
            )
        ).first()
        if existing is not None and existing.state == StorageObjectState.COMMITTED:
            if existing.provider_ref not in (None, target.provider_ref):
                raise ValueError("backup_provider_identity_mismatch")
            if existing.sha256 is not None:
                raise ValueError("backup_already_adopted")
        if existing is not None and existing.state == StorageObjectState.PENDING:
            raise ValueError("backup_already_adopted")
    temp: Path | None = None
    try:
        head_before = target.client.head_object(Bucket=target.bucket, Key=key)
        _require_remote_identity(head_before)
        candidate_ref = _source_ref(
            location="s3",
            namespace=namespace,
            path=key,
            provider_ref=target.provider_ref,
        )
        if source_ref != candidate_ref:
            raise ValueError("backup_source_ref_mismatch")
        temp, head = _download_s3_archive(
            target,
            key,
            version_id=str(head_before.get("VersionId"))
            if head_before.get("VersionId")
            else None,
            etag=str(head_before.get("ETag")) if head_before.get("ETag") else None,
        )
        _require_remote_identity(head)
        if int(head.get("ContentLength", -1)) != int(
            head_before.get("ContentLength", -1)
        ):
            raise RuntimeError("backup_remote_changed")
        _assert_same_s3_identity(head, head_before)
        meta = _validate_archive_for_adoption(temp)
        meta.id = _backup_id_from_archive_name(name)
        digest = _sha256_path(temp)
        if digest != expected_archive_sha256:
            raise RuntimeError("backup_archive_digest_mismatch")
        size = int(head.get("ContentLength", temp.stat().st_size))
        if size != temp.stat().st_size:
            raise RuntimeError("backup_download_size_mismatch")
        receipt = CreationReceipt(
            key=key,
            size=size,
            # Historical objects have no trusted create token. The complete
            # content digest is the adoption token; verification below uses
            # immutable size/hash/eTag/version evidence instead of metadata.
            token=digest,
            backend="backup-s3",
            namespace=namespace,
            etag=str(head["ETag"]) if head.get("ETag") else None,
            version_id=str(head["VersionId"]) if head.get("VersionId") else None,
        )
        head_after = target.client.head_object(
            **_s3_identity_kwargs(bucket=target.bucket, key=key, response=head)
        )
        _assert_same_s3_identity(head_after, head)
        with get_session_factory().session() as session:
            record_creation(
                session,
                receipt,
                object_kind="backup-legacy",
                sha256=digest,
                provider_ref=target.provider_ref,
                upgrade_provider_ref=True,
            )
            session.commit()
        meta.path = key
        meta.location = "s3"
        meta.archive_sha256 = digest
        meta.provider_ref = target.provider_ref
        meta.namespace = namespace
        meta.source_ref = _source_ref(
            location="s3",
            namespace=namespace,
            path=key,
            provider_ref=target.provider_ref,
        )
        return meta
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


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
        record_creation(
            session,
            receipt,
            object_kind="backup",
            sha256=digest,
            provider_ref=provider_ref_for_backend(
                backend, namespace=backend.namespace_for(str(archive))
            ),
            upgrade_provider_ref=True,
        )
        session.commit()
    meta.path = str(archive)
    meta.location = "local"
    meta.archive_sha256 = digest
    meta.namespace = backend.namespace_for(str(archive))
    meta.provider_ref = provider_ref_for_backend(backend, namespace=meta.namespace)
    meta.source_ref = _source_ref(
        location="local",
        namespace=meta.namespace,
        path=meta.path,
        provider_ref=meta.provider_ref,
    )
    return meta


@_exclusive_backup_operation
def upload_backup_archive(filename: str, source: BinaryIO) -> BackupMeta:
    """Validate and register a backup archive uploaded by an administrator."""
    if not filename or "\\" in filename or Path(filename).name != filename:
        raise ValueError("backup_filename_invalid")
    if not filename.startswith((_BACKUP_NAME_PREFIX, _LEGACY_BACKUP_NAME_PREFIX)):
        raise ValueError("backup_filename_invalid")
    backup_id = _backup_id_from_archive_name(filename)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    archive_path = settings.backup_dir / filename
    if archive_path.exists():
        raise FileExistsError("backup_already_exists")
    fd, raw_staged = tempfile.mkstemp(
        prefix=".printstash-backup-upload-", dir=settings.backup_dir
    )
    os.close(fd)
    staged = Path(raw_staged)
    staged.unlink()
    digest = hashlib.sha256()
    try:
        storage.stream_to_path(
            source,
            staged,
            max_bytes=settings.max_upload_bytes,
            digest=digest,
        )
        meta = _validate_archive_for_adoption(staged)
        backend = LocalStorageBackend()
        with get_session_factory().session() as session:
            receipt = publish_file(
                session,
                backend,
                str(archive_path),
                staged,
                object_kind="backup",
                sha256=digest.hexdigest(),
                move=True,
            )
            audit.record(
                session,
                action="backup.upload",
                resource_type="backup",
                diff={"backup_id": backup_id, "size_bytes": receipt.size},
            )
            session.commit()
        namespace = backend.namespace_for(str(archive_path))
        provider_ref = provider_ref_for_backend(backend, namespace=namespace)
        meta.id = backup_id
        meta.path = str(archive_path)
        meta.location = "local"
        meta.size_bytes = receipt.size
        meta.archive_sha256 = digest.hexdigest()
        meta.provider_ref = provider_ref
        meta.namespace = namespace
        meta.source_ref = _source_ref(
            location="local",
            namespace=namespace,
            path=str(archive_path),
            provider_ref=provider_ref,
        )
        return meta
    finally:
        staged.unlink(missing_ok=True)


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
    local = LocalStorageBackend()
    local_namespace = f"backup:{root}"
    committed = _committed_backup_keys(
        "local",
        provider_ref=provider_ref_for_backend(local, namespace=local_namespace),
    )
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
                "namespace": local_namespace,
                "provider_ref": provider_ref_for_backend(
                    local, namespace=local_namespace
                ),
            }
        )
    return candidates


def get_backup(backup_id: str, *, source_ref: str | None = None) -> BackupMeta | None:
    matches = [meta for meta in list_backup_sources() if meta.id == backup_id]
    if source_ref is not None:
        for meta in matches:
            if meta.source_ref == source_ref:
                return meta
        return None
    hashes = {meta.archive_sha256 for meta in matches}
    if len(matches) > 1 and (None in hashes or len(hashes) != 1):
        raise BackupIdentityConflictError("backup_identity_conflict")
    if matches:
        return min(matches, key=_backup_precedence)
    return None


def get_backup_archive_path(backup_id: str, *, source_ref: str | None = None) -> Path:
    """Return a local archive path, downloading cloud-only backups first."""
    meta = get_backup(backup_id, source_ref=source_ref)
    if meta is None:
        raise FileNotFoundError(f"backup {backup_id} not found")
    return _download_backup_to_local(meta)


def _require_backup_archive_owned(
    meta: BackupMeta, *, target: _BackupS3Target | None = None
) -> OwnedStorageObject:
    """Require current proof for the archive selected by restore/delete."""
    with get_session_factory().session() as session:
        if meta.location == "local":
            backend = LocalStorageBackend()
            rows = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.backend == "local",
                    OwnedStorageObject.namespace == backend.namespace_for(meta.path),
                    OwnedStorageObject.key == meta.path,
                    OwnedStorageObject.provider_ref
                    == provider_ref_for_backend(
                        backend, namespace=backend.namespace_for(meta.path)
                    ),
                    OwnedStorageObject.object_kind.in_(  # type: ignore[union-attr]
                        ("backup", "backup-legacy")
                    ),
                    OwnedStorageObject.state == StorageObjectState.COMMITTED,
                )
            ).all()
            for row in rows:
                if row.token is None or row.size_bytes is None:
                    continue
                receipt = CreationReceipt(
                    key=row.key,
                    size=row.size_bytes,
                    token=row.token,
                    backend=row.backend,
                    namespace=row.namespace,
                    etag=row.etag,
                    version_id=row.version_id,
                    device=row.device,
                    inode=row.inode,
                    ctime_ns=row.ctime_ns,
                    provider_ref=row.provider_ref,
                )
                try:
                    if backend.creation_matches(receipt):
                        return row
                    if (
                        row.sha256 is None
                        or row.device is None
                        or row.inode is None
                    ):
                        continue
                    refreshed = backend.adopt_existing(
                        row.key,
                        expected_size=row.size_bytes,
                        expected_sha256=row.sha256,
                    )
                except Exception:
                    continue
                # A root-level metadata repair can change ctime, but it cannot
                # replace the directory entry. Require the original inode in
                # addition to exact bytes before refreshing the receipt.
                if (refreshed.device, refreshed.inode) != (row.device, row.inode):
                    continue
                owned = record_creation(
                    session,
                    refreshed,
                    object_kind=row.object_kind,
                    sha256=row.sha256,
                    provider_ref=row.provider_ref,
                )
                session.commit()
                session.refresh(owned)
                return owned
            raise BackupOwnershipError("backup_storage_ownership_unverified")

        if meta.location.startswith("opendal:"):
            candidates = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.namespace == meta.namespace,
                    OwnedStorageObject.key == meta.path,
                    OwnedStorageObject.provider_ref == meta.provider_ref,
                    OwnedStorageObject.object_kind.in_(  # type: ignore[union-attr]
                        ("backup", "backup-legacy")
                    ),
                    OwnedStorageObject.state == StorageObjectState.COMMITTED,
                )
            ).all()
            for candidate in candidates:
                destination = destination_for_ownership(candidate)
                if destination is None:
                    continue
                try:
                    destination.require_owned(candidate)
                except BackupDestinationError:
                    continue
                return candidate
            raise BackupOwnershipError("backup_storage_ownership_unverified")

        target = target or _get_backup_s3_target()
        if target is None:
            raise BackupOwnershipError("backup_storage_ownership_unverified")
        if not target.bucket and meta.namespace:
            target = replace(target, bucket=meta.namespace.split("/", 1)[0])
        bucket = target.bucket
        prefix = _s3_prefix_for_key(meta.path)
        if prefix is None:
            raise BackupOwnershipError("backup_storage_ownership_unverified")
        expected_namespace = f"{bucket}/{prefix}"
        if meta.namespace is not None and meta.namespace != expected_namespace:
            raise BackupOwnershipError("backup_storage_target_changed")
        candidates = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == "backup-s3",
                OwnedStorageObject.namespace == expected_namespace,
                OwnedStorageObject.key == meta.path,
                OwnedStorageObject.object_kind.in_(("backup", "backup-legacy")),
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
            )
        ).all()
        for candidate in candidates:
            if candidate.provider_ref != target.provider_ref:
                continue
            if not candidate.version_id and not candidate.etag:
                continue
            try:
                response = _s3_head_owned(target, candidate)
            except Exception:
                continue
            if (
                int(response.get("ContentLength", -1)) == candidate.size_bytes
                and (
                    not candidate.etag
                    or str(response.get("ETag", "")) == candidate.etag
                )
                and (
                    not candidate.version_id
                    or str(response.get("VersionId", "")) == candidate.version_id
                )
                and (
                    candidate.object_kind == "backup-legacy"
                    or response.get("Metadata", {}).get("printstash-create-token")
                    == candidate.token
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


def verify_backup(
    backup_id: str,
    *,
    source_ref: str | None = None,
    archive_path: Path | None = None,
    record_audit: bool = True,
) -> BackupVerification:
    """Validate archive structure, manifest membership, sizes, and safe paths."""
    explicit_archive = archive_path is not None
    # Keep the narrow helper seam compatible with integrations that replace it
    # with a one-argument callable; source selection is only needed on the
    # collision path.
    archive = archive_path or (
        get_backup_archive_path(backup_id)
        if source_ref is None
        else get_backup_archive_path(backup_id, source_ref=source_ref)
    )
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
            else:
                db_member = next(
                    member for member in members if member.name == "db.sqlite3"
                )
                if not db_member.isfile():
                    findings.append(
                        {"code": "backup_manifest_invalid", "member": "db.sqlite3"}
                    )
                else:
                    fd, raw_db = tempfile.mkstemp(prefix=".printstash-verify-db-")
                    os.close(fd)
                    db_path = Path(raw_db)
                    try:
                        stream = tar.extractfile(db_member)
                        if stream is None:
                            raise RuntimeError("backup_member_missing:db.sqlite3")
                        with db_path.open("wb") as destination:
                            shutil.copyfileobj(stream, destination)
                        try:
                            _validate_sqlite_snapshot(db_path)
                        except Exception:
                            findings.append(
                                {
                                    "code": "backup_manifest_invalid",
                                    "member": "db.sqlite3",
                                }
                            )
                    finally:
                        db_path.unlink(missing_ok=True)
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
                                    not isinstance(entry.get("provider"), str)
                                    or not isinstance(entry.get("provider_id"), str)
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
    if not explicit_archive:
        cleanup_backup_cache(archive)
    if record_audit:
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


def verify_backup_ownership(ownership_id: int) -> BackupOwnershipVerification:
    """Verify one exact committed backup receipt without discovery/listing.

    Audits must be able to report a missing or inaccessible replica.  Resolving
    a logical backup id through the discovery APIs can hide that replica (and
    can collapse same-id sources), so this seam starts from the durable ledger
    primary key and carries its exact locator through download and validation.
    """
    with get_session_factory().session() as session:
        row = session.get(OwnedStorageObject, ownership_id)
        if row is None or row.object_kind not in {"backup", "backup-legacy"}:
            return BackupOwnershipVerification(
                ownership_id=ownership_id,
                status="missing",
                error="backup_ownership_not_found",
            )
        row = OwnedStorageObject.model_validate(row.model_dump())

    if row.state == StorageObjectState.BLOCKED:
        return BackupOwnershipVerification(
            ownership_id=ownership_id,
            status="identity",
            error=row.last_error or "backup_ownership_blocked",
        )
    if row.state != StorageObjectState.COMMITTED:
        return BackupOwnershipVerification(
            ownership_id=ownership_id,
            status="missing",
            error="backup_ownership_not_committed",
        )

    if row.backend == "local":
        location = "local"
    elif row.backend.startswith("backup-opendal-"):
        destination = destination_for_ownership(row)
        if destination is None:
            return BackupOwnershipVerification(
                ownership_id=ownership_id,
                status="identity",
                error="backup_storage_ownership_unverified",
            )
        location = destination.location
    else:
        location = "s3"
    try:
        backup_id = _backup_id_from_archive_name(Path(row.key).name)
    except ValueError:
        backup_id = str(ownership_id)
    source_ref = _source_ref(
        location=location,
        namespace=row.namespace,
        path=row.key,
        provider_ref=row.provider_ref,
    )
    meta = BackupMeta(
        id=backup_id,
        created_at=row.created_at.isoformat(),
        size_bytes=row.size_bytes or 0,
        storage_backend=row.backend,
        file_count=0,
        app_version="unknown",
        path=row.key,
        location=location,
        archive_sha256=row.sha256,
        provider_ref=row.provider_ref,
        namespace=row.namespace,
        source_ref=source_ref,
    )
    cache_path: Path | None = None
    try:
        archive = (
            Path(row.key) if location == "local" else _download_backup_to_local(meta)
        )
        if location != "local":
            cache_path = archive
        result = verify_backup(
            backup_id,
            archive_path=archive,
            record_audit=False,
        )
    except FileNotFoundError as exc:
        return BackupOwnershipVerification(
            ownership_id=ownership_id,
            status="missing",
            error=type(exc).__name__,
        )
    except BackupOwnershipError as exc:
        message = str(exc)
        status: BackupOwnershipVerificationStatus = (
            "digest" if "digest" in message or "hash" in message else "identity"
        )
        return BackupOwnershipVerification(
            ownership_id=ownership_id,
            status=status,
            error=message,
        )
    except OSError as exc:
        return BackupOwnershipVerification(
            ownership_id=ownership_id,
            status="inaccessible",
            error=type(exc).__name__,
        )
    finally:
        if cache_path is not None:
            cleanup_backup_cache(cache_path)

    status = "valid" if result.valid else "corrupt"
    return BackupOwnershipVerification(
        ownership_id=ownership_id,
        status=status,
        verification=result,
    )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def delete_backup(
    backup_id: str,
    *,
    source_ref: str | None = None,
    allow_unversioned: bool = False,
) -> bool:
    """Delete exactly the source authorized by ``source_ref``."""
    meta = get_backup(backup_id, source_ref=source_ref)
    if meta is None:
        return False

    deleted = False
    with get_session_factory().session() as session:
        local_backend = LocalStorageBackend()
        if meta.location == "local":
            try:
                local_backend.verify_destructive_access([meta.path])
                require_owned_key(session, local_backend, meta.path)
            except Exception as exc:
                raise BackupOwnershipError(
                    "backup_storage_ownership_unverified"
                ) from exc
            deleted = delete_owned_key(session, local_backend, meta.path)
        elif meta.location.startswith("opendal:"):
            owned = _require_backup_archive_owned(meta)
            destination = destination_for_ownership(owned)
            if destination is None or not destination.delete_owned(
                owned, allow_unversioned=allow_unversioned
            ):
                raise BackupOwnershipError("backup_remote_delete_unverified")
            session.delete(owned)
            deleted = True
        else:
            target = _get_backup_s3_target()
            if target is None:
                raise BackupOwnershipError("backup_storage_ownership_unverified")
            owned = _require_backup_archive_owned(meta, target=target)
            if not target.bucket:
                target = replace(target, bucket=owned.namespace.split("/", 1)[0])
            try:
                target.client.delete_object(
                    **_s3_object_kwargs(
                        bucket=target.bucket, key=meta.path, row=owned, delete=True
                    )
                )
            except Exception:
                logger.exception("backup: failed to delete S3 backup object")
            else:
                session.delete(owned)
                # A cloud download is a rebuildable derivative.  Remove only
                # the cache derived from this exact source locator and only
                # when its own local receipt still proves the bytes; sibling
                # source caches and canonical local backups are untouched.
                cache_ref = meta.source_ref or _source_ref(
                    location="s3",
                    namespace=owned.namespace,
                    path=owned.key,
                    provider_ref=owned.provider_ref,
                )
                cache_identity = owned.version_id or owned.etag or owned.sha256
                if cache_identity:
                    cache_ref = hashlib.sha256(
                        f"{cache_ref}\x1f{cache_identity}".encode("utf-8")
                    ).hexdigest()
                cache_path = (
                    settings.backup_dir
                    / ".cloud-cache"
                    / f"{cache_ref}-{meta.path.rsplit('/', 1)[-1]}"
                )
                if cache_path.exists():
                    delete_owned_key(session, local_backend, str(cache_path))
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

    if meta.location.startswith("opendal:"):
        owned = _require_backup_archive_owned(meta)
        destination = destination_for_ownership(owned)
        if destination is None:
            raise BackupOwnershipError("backup_storage_ownership_unverified")
        archive_name = meta.path.rsplit("/", 1)[-1]
        source_ref = meta.source_ref or _source_ref(
            location=meta.location,
            namespace=meta.namespace,
            path=meta.path,
            provider_ref=owned.provider_ref,
        )
        remote_identity = owned.version_id or owned.etag or owned.sha256
        if not remote_identity:
            raise BackupOwnershipError("backup_remote_identity_unavailable")
        cache_identity = hashlib.sha256(
            f"{source_ref}\x1f{remote_identity}".encode("utf-8")
        ).hexdigest()
        cache_dir = settings.backup_dir / ".cloud-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local_path = cache_dir / f"{cache_identity}-{archive_name}"
        if local_path.exists():
            if not owned.sha256 or _sha256_path(local_path) != owned.sha256:
                raise BackupOwnershipError("backup_local_destination_conflict")
            cache_ownership = _cache_ownership_for_path(local_path)
            if cache_ownership is None or cache_ownership.sha256 != owned.sha256:
                raise BackupOwnershipError("backup_cache_ownership_unverified")
            return local_path

        fd, raw_temp = tempfile.mkstemp(
            prefix=".printstash-backup-download-", dir=settings.backup_dir
        )
        os.close(fd)
        download_temp = Path(raw_temp)
        download_temp.unlink()
        try:
            destination.download_owned(owned, download_temp)
            with get_session_factory().session() as publish_session:
                publish_file(
                    publish_session,
                    LocalStorageBackend(),
                    str(local_path),
                    download_temp,
                    object_kind="backup-cloud-cache",
                    sha256=owned.sha256,
                    move=True,
                )
                publish_session.commit()
        except Exception:
            download_temp.unlink(missing_ok=True)
            raise
        return local_path

    if meta.location == "s3":
        # Download from S3 to a temp location
        target = _get_backup_s3_target()
        if target is None:
            raise RuntimeError("backup is in S3 but no S3 client is available")
        owned = _require_backup_archive_owned(meta, target=target)
        if not target.bucket:
            target = replace(target, bucket=owned.namespace.split("/", 1)[0])
        archive_name = meta.path.rsplit("/", 1)[-1]
        source_ref = meta.source_ref or _source_ref(
            location="s3",
            namespace=meta.namespace,
            path=meta.path,
            provider_ref=owned.provider_ref,
        )
        # A cloud source is not allowed to overwrite a same-named local or
        # another-source cache.  Keep the cache locator source-specific.
        cache_dir = settings.backup_dir / ".cloud-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Bind the rebuildable cache name to the exact remote content proof.
        # A provider may replace an unversioned object at the same key; a
        # source-only cache name would then turn that replacement into a
        # confusing local collision.
        remote_identity = owned.version_id or owned.etag or owned.sha256
        if not remote_identity:
            raise BackupOwnershipError("backup_remote_identity_unavailable")
        cache_identity = hashlib.sha256(
            f"{source_ref}\x1f{remote_identity}".encode("utf-8")
        ).hexdigest()
        local_path = cache_dir / f"{cache_identity}-{archive_name}"
        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        if local_path.exists():
            try:
                existing_hash = _sha256_path(local_path)
            except OSError:
                existing_hash = None
            if not owned.sha256 or existing_hash != owned.sha256:
                raise BackupOwnershipError("backup_local_destination_conflict")
            cache_ownership = _cache_ownership_for_path(local_path)
            if cache_ownership is None:
                # Never adopt a matching unowned file at a deterministic cache
                # locator: an operator or another process could have placed it
                # there.  A fresh download below reserves ownership first.
                raise BackupOwnershipError("backup_cache_ownership_unverified")
            LocalStorageBackend().adopt_existing(
                str(local_path),
                expected_size=owned.size_bytes or local_path.stat().st_size,
                expected_sha256=owned.sha256,
            )
            if cache_ownership.sha256 != owned.sha256:
                raise BackupOwnershipError("backup_cache_identity_mismatch")
            return local_path

        fd, raw_temp = tempfile.mkstemp(
            prefix=".printstash-backup-download-", dir=settings.backup_dir
        )
        os.close(fd)
        download_temp = Path(raw_temp)
        try:
            response = _s3_get_owned(target, owned)
            body = response["Body"]
            try:
                with download_temp.open("wb") as destination:
                    shutil.copyfileobj(body, destination)
            finally:
                body.close()
            _assert_s3_identity(
                response,
                size_bytes=owned.size_bytes,
                etag=owned.etag,
                version_id=owned.version_id,
            )
            if download_temp.stat().st_size != owned.size_bytes:
                raise RuntimeError("backup_download_size_mismatch")
            if owned.sha256 and _sha256_path(download_temp) != owned.sha256:
                raise RuntimeError("backup_download_digest_mismatch")
            # Re-HEAD through the same immutable version/ETag proof after the
            # body is consumed.  An unversioned object may have been replaced
            # between the initial check and GET; never publish that body.
            confirmed = _s3_head_owned(target, owned)
            _assert_s3_identity(
                confirmed,
                size_bytes=owned.size_bytes,
                etag=owned.etag,
                version_id=owned.version_id,
            )
            with get_session_factory().session() as publish_session:
                publish_file(
                    publish_session,
                    LocalStorageBackend(),
                    str(local_path),
                    download_temp,
                    object_kind="backup-cloud-cache",
                    move=True,
                )
                publish_session.commit()
        except Exception:
            download_temp.unlink(missing_ok=True)
            raise
        logger.info("backup %s downloaded from S3 to %s", meta.id, local_path)
        return local_path

    raise FileNotFoundError(f"backup {meta.id} not found locally or in S3")


def _cache_ownership_for_path(path: Path) -> OwnedStorageObject | None:
    """Load the exact committed/pending receipt for one cache path."""
    backend = LocalStorageBackend()
    namespace = backend.namespace_for(str(path))
    with get_session_factory().session() as session:
        row = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == "local",
                OwnedStorageObject.namespace == namespace,
                OwnedStorageObject.key == str(path),
                OwnedStorageObject.object_kind == "backup-cloud-cache",
                OwnedStorageObject.state.in_(
                    (StorageObjectState.PENDING, StorageObjectState.COMMITTED)
                ),
            )
        ).first()
        if row is None:
            return None
        # Return a detached value so the staged database copy remains usable
        # after the source session closes.
        return OwnedStorageObject.model_validate(row.model_dump())


def cleanup_backup_cache(path: Path) -> None:
    """Remove one per-source cloud derivative after its consumer is done."""
    cache_root = (settings.backup_dir / ".cloud-cache").resolve(strict=False)
    candidate = path.resolve(strict=False)
    if candidate.parent != cache_root or not candidate.name:
        return
    if _cache_path_pinned_by_restore_journal(str(candidate)):
        return
    with get_session_factory().session() as session:
        delete_owned_key(session, LocalStorageBackend(), str(candidate))
        session.commit()


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
    journal_binding: dict[str, object] = {}
    if journal_path is not None:
        try:
            state = _load_restore_journal(journal_path)
        except RestoreConflictError:
            state = None
        if (
            state is not None
            and state.started.get("version") == _RESTORE_JOURNAL_VERSION
        ):
            journal_binding = _journal_binding(state.started)
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
                        **journal_binding,
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
    cache_ownership: OwnedStorageObject | None = None,
) -> None:
    """Replace archived fingerprints with proof from this restore operation."""
    with sqlite3.connect(database_path) as connection:
        for item in applied:
            receipt = item.receipt
            current_provider_ref = provider_ref_for_backend(
                get_backend(), namespace=receipt.namespace
            )
            existing = connection.execute(
                """
                SELECT object_kind FROM owned_storage_objects
                WHERE backend = ? AND namespace = ? AND key = ?
                  AND provider_ref = ? LIMIT 1
                """,
                (receipt.backend, receipt.namespace, item.key, current_provider_ref),
            ).fetchone()
            object_kind = str(existing[0]) if existing else "restored"
            # Archived inode/ETag values prove an old object, not the one just
            # created. Replace them with this operation's current receipt.
            connection.execute(
                """
                DELETE FROM owned_storage_objects
                WHERE backend = ? AND namespace = ? AND key = ?
                  AND provider_ref = ?
                """,
                (receipt.backend, receipt.namespace, item.key, current_provider_ref),
            )
            connection.execute(
                """
                INSERT INTO owned_storage_objects (
                    backend, namespace, key, provider_ref, object_kind, state, token,
                    size_bytes, sha256, etag, version_id, device, inode, ctime_ns,
                    committed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.backend,
                    receipt.namespace,
                    receipt.key,
                    provider_ref_for_backend(
                        get_backend(), namespace=receipt.namespace
                    ),
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
              AND provider_ref IS ?
            """,
            (
                archive_ownership.backend,
                archive_ownership.namespace,
                archive_ownership.key,
                archive_ownership.provider_ref,
            ),
        )
        connection.execute(
            """
            INSERT INTO owned_storage_objects (
                backend, namespace, key, provider_ref, object_kind, state, token,
                size_bytes, sha256, etag, version_id, device, inode, ctime_ns,
                committed_at, created_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                archive_ownership.backend,
                archive_ownership.namespace,
                archive_ownership.key,
                archive_ownership.provider_ref,
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
        if cache_ownership is not None:
            # The cache is a rebuildable derivative, but while this restore is
            # unresolved it is still an exact source locator.  Carrying its
            # receipt into the staged DB makes terminal cleanup work after the
            # database swap instead of orphaning the private cache file.
            connection.execute(
                """
                DELETE FROM owned_storage_objects
                WHERE backend = ? AND namespace = ? AND key = ?
                  AND provider_ref IS ?
                """,
                (
                    cache_ownership.backend,
                    cache_ownership.namespace,
                    cache_ownership.key,
                    cache_ownership.provider_ref,
                ),
            )
            connection.execute(
                """
                INSERT INTO owned_storage_objects (
                    backend, namespace, key, provider_ref, object_kind, state, token,
                    size_bytes, sha256, etag, version_id, device, inode, ctime_ns,
                    committed_at, created_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_ownership.backend,
                    cache_ownership.namespace,
                    cache_ownership.key,
                    cache_ownership.provider_ref,
                    cache_ownership.object_kind,
                    cache_ownership.state.value,
                    cache_ownership.token,
                    cache_ownership.size_bytes,
                    cache_ownership.sha256,
                    cache_ownership.etag,
                    cache_ownership.version_id,
                    cache_ownership.device,
                    cache_ownership.inode,
                    cache_ownership.ctime_ns,
                    cache_ownership.committed_at,
                    cache_ownership.created_at,
                    cache_ownership.last_error,
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
    """Publish a restore into empty or byte-identical, in-bound destinations.

    Restore used to overwrite every manifest key and then attempt a best-effort
    rollback. A malicious/stale manifest or remapped root could therefore
    clobber unrelated bytes. Conflicting restores now fail before the first
    write. Existing bytes are reused only when their size and SHA-256 match the
    archive, and that adoption is journalled before it can affect ownership.
    """
    del rollback_dir
    backend = get_backend()
    applied: list[_AppliedBlob] = []
    created: list[_AppliedBlob] = []

    seen: set[str] = set()
    matching_existing: list[_StagedBlob] = []
    for blob in blobs:
        _validate_restore_key(blob.key)
        if blob.key in seen:
            raise RestoreConflictError("restore_duplicate_destination")
        seen.add(blob.key)
        if not backend.exists(blob.key):
            continue
        intent = journal_state.intents.get(blob.key) if journal_state else None
        if intent is None:
            if not _stored_blob_matches(blob):
                raise RestoreConflictError("restore_destination_exists")
            matching_existing.append(blob)
            continue
        if not _journal_intent_matches(intent, blob):
            raise RestoreConflictError("restore_destination_exists")
        if not _stored_blob_matches(blob):
            raise RestoreConflictError("restore_destination_changed")

    # Complete collision preflight before recording adoption intent. This
    # keeps a later conflicting key from leaving partial restore evidence.
    for blob in matching_existing:
        if journal_path is None or journal_state is None:
            raise RestoreConflictError("restore_destination_exists")
        generation = journal_state.generations.get(blob.key, 0) + 1
        adoption_intent: dict[str, object] = {
            "event": "intent",
            "key": blob.key,
            "size": blob.size,
            "sha256": blob.sha256,
            "namespace": blob.namespace,
            "generation": generation,
        }
        if journal_state.started.get("version") == _RESTORE_JOURNAL_VERSION:
            adoption_intent.update(_journal_binding(journal_state.started))
        _append_restore_journal(journal_path, adoption_intent)
        journal_state.intents[blob.key] = adoption_intent
        journal_state.generations[blob.key] = generation

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
                    if (
                        journal_state is not None
                        and journal_state.started.get("version")
                        == _RESTORE_JOURNAL_VERSION
                    ):
                        event.update(_journal_binding(journal_state.started))
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
                        **(
                            _journal_binding(journal_state.started)
                            if journal_state is not None
                            and journal_state.started.get("version")
                            == _RESTORE_JOURNAL_VERSION
                            else {}
                        ),
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
                if (
                    journal_state is not None
                    and journal_state.started.get("version") == _RESTORE_JOURNAL_VERSION
                ):
                    intent.update(_journal_binding(journal_state.started))
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
                if (
                    journal_state is not None
                    and journal_state.started.get("version") == _RESTORE_JOURNAL_VERSION
                ):
                    event.update(_journal_binding(journal_state.started))
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
    complete: bool = False
    cache_paths: set[str] = field(default_factory=set)


def _journal_has_mutation_evidence(state: _RestoreJournalState) -> bool:
    """Return whether a restore journal must remain for recovery.

    A validated journal containing only ``started`` (and an optional cache pin)
    cannot have authorized a storage write or database swap. It is therefore
    safe to remove after deterministic preflight rejection.
    """
    return bool(
        state.intents
        or state.published
        or state.database_swap_intent
        or state.database_active
        or state.complete
    )


def _restore_provider_ref(blobs: list[_StagedBlob] | None = None) -> str:
    """Return the current vault destination identity for restore journaling.

    Remote adapters derive this from their immutable endpoint/configuration.
    The journal is checked before the archive is staged on resume, so it must
    use an adapter-level identity that does not depend on discovering blob
    namespaces from the archive. Local v1 recovery remains the compatibility
    path for historical journals without this field.
    """
    backend = get_backend()
    del blobs
    return provider_ref_for_backend(backend)


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
    # Backup id is a routing label, not an ownership proof. Never query on it
    # alone: an older database can contain a marker for a different object
    # with the same id.
    if operation_nonce is None or archive_sha256 is None:
        return None
    try:
        with get_session_factory().session() as session:
            statement = select(RestoreMarker).where(
                RestoreMarker.backup_id == backup_id
            )
            statement = statement.where(
                RestoreMarker.operation_nonce == operation_nonce,
                RestoreMarker.archive_sha256 == archive_sha256,
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
    raw_started = events[0]
    if raw_started.get("event") != "started":
        raise RestoreConflictError("restore_journal_invalid")
    if raw_started.get("version") not in {1, _RESTORE_JOURNAL_VERSION}:
        raise RestoreConflictError("restore_journal_invalid")
    started = dict(raw_started)
    upgrade_events = [
        event for event in events[1:] if event.get("event") == "journal_upgrade"
    ]
    if raw_started.get("version") == _RESTORE_JOURNAL_VERSION:
        if upgrade_events:
            raise RestoreConflictError("restore_journal_invalid")
        nonce = started.get("operation_nonce")
        archive_sha = started.get("archive_sha256")
        if (
            not isinstance(nonce, str)
            or len(nonce) != 64
            or any(char not in "0123456789abcdef" for char in nonce)
            or not isinstance(archive_sha, str)
            or len(archive_sha) != 64
            or any(char not in "0123456789abcdef" for char in archive_sha)
            or (
                started.get("provider_ref") is not None
                and (
                    not isinstance(started.get("provider_ref"), str)
                    or len(str(started.get("provider_ref"))) != 64
                    or any(
                        char not in "0123456789abcdef"
                        for char in str(started.get("provider_ref"))
                    )
                )
            )
        ):
            raise RestoreConflictError("restore_journal_invalid")
    upgrade_index: int | None = None
    if raw_started.get("version") == 1 and upgrade_events:
        # v1 journals predate the database marker. Once an upgrade is present,
        # every subsequent proof is bound to the newly generated nonce *and*
        # the original archive hash. The effective state is v2 even though the
        # immutable first line remains v1 for forensic compatibility.
        if len(upgrade_events) != 1:
            raise RestoreConflictError("restore_journal_invalid")
        upgrade = upgrade_events[0]
        upgrade_index = next(
            (index for index, event in enumerate(events) if event is upgrade), None
        )
        if upgrade_index is None:
            raise RestoreConflictError("restore_journal_invalid")
        nonce = upgrade.get("operation_nonce")
        archive_sha = upgrade.get("archive_sha256")
        provider_ref = upgrade.get("provider_ref")
        if (
            upgrade.get("backup_id") != raw_started.get("backup_id")
            or upgrade.get("from_version") != 1
            or upgrade.get("to_version") != _RESTORE_JOURNAL_VERSION
            or not isinstance(nonce, str)
            or len(nonce) != 64
            or any(char not in "0123456789abcdef" for char in nonce)
            or not isinstance(archive_sha, str)
            or len(archive_sha) != 64
            or any(char not in "0123456789abcdef" for char in archive_sha)
            or raw_started.get("archive_sha256") != archive_sha
            or (
                provider_ref is not None
                and (
                    not isinstance(provider_ref, str)
                    or len(provider_ref) != 64
                    or any(char not in "0123456789abcdef" for char in provider_ref)
                )
            )
        ):
            raise RestoreConflictError("restore_journal_invalid")
        started["version"] = _RESTORE_JOURNAL_VERSION
        started["operation_nonce"] = nonce
        started["archive_sha256"] = archive_sha
        if provider_ref is not None:
            started["provider_ref"] = provider_ref
    intents: dict[str, dict[str, object]] = {}
    published: dict[str, dict[str, object]] = {}
    generations: dict[str, int] = {}
    database_swap_intent = False
    database_active = False
    complete = False
    cache_paths: set[str] = set()
    lifecycle_closed = False
    for event_index, event in enumerate(events[1:], start=1):
        event_name = event.get("event")
        if complete:
            raise RestoreConflictError("restore_journal_invalid")
        if raw_started.get("version") == _RESTORE_JOURNAL_VERSION and event_name in {
            "database_swap_intent",
            "database_active",
            "complete",
        }:
            if any(
                event.get(field) != started.get(field)
                for field in (
                    "backup_id",
                    "operation_nonce",
                    "archive_sha256",
                    "provider_ref",
                )
            ):
                raise RestoreConflictError("restore_journal_invalid")
        # A v1 journal may have durable blob intent/publication transitions
        # before the process is upgraded.  Database swap evidence did not
        # exist in v1, however, so accepting it before the upgrade would let
        # a stale backup-id-only marker masquerade as this operation.  Once
        # the upgrade record is present, all database evidence must carry the
        # projected nonce and immutable archive hash checked above.
        if (
            raw_started.get("version") == 1
            and event_name in {"database_swap_intent", "database_active"}
            and (upgrade_index is None or event_index < upgrade_index)
        ):
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "database_swap_intent":
            if (
                database_swap_intent
                or database_active
                or lifecycle_closed
                or any(key not in published for key in intents)
                or event.get("backup_id") != started.get("backup_id")
                or event.get("operation_nonce") != started.get("operation_nonce")
                or event.get("archive_sha256") != started.get("archive_sha256")
                or (
                    started.get("provider_ref") is not None
                    and event.get("provider_ref") != started.get("provider_ref")
                )
            ):
                raise RestoreConflictError("restore_journal_invalid")
            database_swap_intent = True
            lifecycle_closed = True
            continue
        if event_name == "journal_upgrade":
            # Validated above, and a second upgrade was rejected there.
            continue
        if event_name == "cache_pinned":
            cache_path = event.get("cache_path")
            if not isinstance(cache_path, str) or not cache_path:
                raise RestoreConflictError("restore_journal_invalid")
            if raw_started.get("version") == _RESTORE_JOURNAL_VERSION and any(
                event.get(field) != started.get(field)
                for field in (
                    "backup_id",
                    "operation_nonce",
                    "archive_sha256",
                    "provider_ref",
                )
            ):
                raise RestoreConflictError("restore_journal_invalid")
            if cache_path in cache_paths:
                raise RestoreConflictError("restore_journal_invalid")
            cache_paths.add(cache_path)
            continue
        if event_name == "database_active":
            if (
                not database_swap_intent
                or database_active
                or event.get("backup_id") != started.get("backup_id")
                or event.get("operation_nonce") != started.get("operation_nonce")
                or event.get("archive_sha256") != started.get("archive_sha256")
                or event.get("provider_ref") != started.get("provider_ref")
            ):
                raise RestoreConflictError("restore_journal_invalid")
            database_active = True
            continue
        if event_name not in {"intent", "published", "retracted", "complete"}:
            raise RestoreConflictError("restore_journal_invalid")
        # A v2 journal (or a v1 journal after its explicit upgrade) binds every
        # subsequent blob lifecycle transition to the same current provider.
        # Historical v1 transitions before the upgrade remain readable so local
        # recovery can proceed forward without replaying them.
        if (
            started.get("provider_ref") is not None
            and (
                raw_started.get("version") == _RESTORE_JOURNAL_VERSION
                or upgrade_index is None
                or event_index > upgrade_index
            )
            and event.get("provider_ref") != started.get("provider_ref")
        ):
            raise RestoreConflictError("restore_journal_invalid")
        if event_name == "complete":
            if raw_started.get("version") != 1 and not database_active:
                raise RestoreConflictError("restore_journal_invalid")
            complete = True
            continue
        if lifecycle_closed:
            raise RestoreConflictError("restore_journal_invalid")
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
        complete,
        cache_paths,
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
    current_provider_ref = _restore_provider_ref(blobs)
    expected_start: dict[str, object] = {
        "event": "started",
        "version": _RESTORE_JOURNAL_VERSION,
        "backup_id": backup_id,
        "archive_sha256": archive_sha256,
        "operation_nonce": operation_nonce,
        "backend": backend.backend_name,
        "namespaces": sorted({blob.namespace for blob in blobs}),
        "provider_ref": current_provider_ref,
    }
    if not path.exists():
        _append_restore_journal(path, expected_start)
        return _RestoreJournalState(expected_start, {}, {}, {})
    state = _load_restore_journal(path)
    if state.started.get("version") == 1 and state.complete:
        # A legacy terminal journal has no nonce-bound marker proof. It is
        # forensic evidence only and must never be projected into a resumable
        # v2 operation.
        raise RestoreConflictError("restore_journal_terminal")
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
            if key not in {"version", "operation_nonce", "provider_ref"}
        }
        expected_identity = {
            key: expected_start[key]
            for key in expected_start
            if key not in {"version", "operation_nonce", "provider_ref"}
        }
        # v1 journals have no provider binding. They can be resumed safely only
        # against the local backend, whose storage identity is the filesystem
        # root and whose legacy journal already pins its namespace list. A
        # remote v1 journal must be explicitly re-created/adopted rather than
        # guessing which endpoint owns its keys.
        if (
            state.started.get("version") != 1
            or legacy_identity != expected_identity
            or backend.backend_name != "local"
        ):
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
                "provider_ref": current_provider_ref,
            },
        )
        # Re-read so callers use the effective v2 identity immediately. A
        # stale v1 state must never perform a backup-id-only marker lookup.
        state = _load_restore_journal(path)
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
    required = {
        "event": "intent",
        "key": blob.key,
        "size": blob.size,
        "sha256": blob.sha256,
        "namespace": blob.namespace,
        "generation": generation,
    }
    return isinstance(generation, int) and all(
        event.get(key) == value for key, value in required.items()
    )


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


def _journal_binding(started: dict[str, object]) -> dict[str, object]:
    binding = {
        "backup_id": started["backup_id"],
        "operation_nonce": started["operation_nonce"],
        "archive_sha256": started["archive_sha256"],
    }
    provider_ref = started.get("provider_ref")
    if provider_ref is not None:
        binding["provider_ref"] = provider_ref
    return binding


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
def restore_backup(backup_id: str, *, source_ref: str | None = None) -> dict:
    """Restore a backup with staged blobs and SQLite's online backup API.

    Downloads from S3 if the backup is only in cloud storage.
    WARNING: This replaces the current database. Archived files are created or
    reused when byte-identical; conflicting live storage keys are never
    overwritten.

    Sets a process-wide gate so background loops (GC, external scans, printer
    sync) skip their tick instead of racing the restore. Refuses with
    ``RestoreConflictError`` if ingestion work is still running after a short
    grace period, rather than restoring underneath it.
    """
    _require_database_backup_support(restore=True)
    pending_journal: _RestoreJournalState | None = None
    pending_journal_path = settings.backup_dir / f".restore-{backup_id}.journal"
    if restore_in_progress():
        recovery_id = unresolved_restore_backup_id()
        if recovery_id != backup_id:
            raise RestoreConflictError("restore_recovery_required")
        # Validate the current vault destination before discovery, download,
        # reconciliation, or any other operation that could mutate state. A
        # changed remote endpoint must never resume a journal against the same
        # keys on a different provider.
        try:
            pending_journal = _load_restore_journal(pending_journal_path)
            journal_provider_ref = pending_journal.started.get("provider_ref")
            if journal_provider_ref is not None and (
                journal_provider_ref != _restore_provider_ref()
            ):
                raise RestoreConflictError("restore_storage_provider_changed")
            if (
                pending_journal.started.get("version") == 1
                and get_backend().backend_name != "local"
            ):
                raise RestoreConflictError("restore_journal_mismatch")
        except RestoreConflictError:
            raise
        except Exception as exc:
            raise RestoreConflictError("restore_storage_provider_unknown") from exc
    meta = get_backup(backup_id, source_ref=source_ref)
    if meta is None:
        raise FileNotFoundError(f"backup {backup_id} not found")
    if restore_in_progress():
        # Resolve the pending journal's content identity before recording an
        # audit row or touching the archive. A same-id archive from another
        # provider/prefix must not enter the restore path at all.
        journal_path = pending_journal_path
        try:
            pending = pending_journal or _load_restore_journal(journal_path)
        except Exception as exc:
            raise RestoreConflictError("restore_journal_invalid") from exc
        journal_hash = pending.started.get("archive_sha256")
        if not isinstance(journal_hash, str) or meta.archive_sha256 != journal_hash:
            raise RestoreConflictError("restore_journal_mismatch")

    # Captured before any DB swap: the actor/IP behind this restore, for the
    # post-swap "complete" row (the ambient ContextVar survives the swap, but
    # writing it from a session bound to the restored DB is easiest to read).
    restoring_actor_id, restoring_ip = audit.current_audit_context()
    restored_files = 0
    maintenance_required = False
    restore_cache_path: Path | None = None

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
            if meta.location == "s3":
                restore_cache_path = archive_path
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
                # Keep the authoritative remote receipt.  The local path is a
                # source-specific cache and must never replace the remote
                # source in the restored ownership ledger.
                archive_ownership = _require_backup_archive_owned(meta)
                cache_ownership = (
                    _cache_ownership_for_path(archive_path)
                    if restore_cache_path is not None
                    else None
                )
                # Upgrade the private staged copy before touching live bytes.
                # This keeps old backups restorable and guarantees the
                # ownership ledger exists for this operation's receipts.
                run_migrations(str(URL.create("sqlite", database=str(database_path))))
                rollback_dir = staging_dir / "rollback"
                rollback_dir.mkdir()
                journal_path = pending_journal_path
                resuming_journal = journal_path.exists()
                operation_nonce = secrets.token_hex(32)
                journal_state = _prepare_restore_journal(
                    journal_path,
                    backup_id=backup_id,
                    archive_sha256=_sha256_path(archive_path),
                    blobs=staged_blobs,
                    operation_nonce=operation_nonce,
                )
                if restore_cache_path is not None:
                    if str(restore_cache_path) not in journal_state.cache_paths:
                        _append_restore_journal(
                            journal_path,
                            {
                                "event": "cache_pinned",
                                "cache_path": str(restore_cache_path),
                                **_journal_binding(journal_state.started),
                            },
                        )
                        journal_state.cache_paths.add(str(restore_cache_path))
                operation_nonce = str(
                    journal_state.started.get("operation_nonce", operation_nonce)
                )
                archive_sha256 = str(
                    journal_state.started.get(
                        "archive_sha256", _sha256_path(archive_path)
                    )
                )
                # _prepare_restore_journal upgrades v1 journals before this
                # point; marker proof is always the full nonce + archive hash.
                marker_nonce = operation_nonce

                def active_marker_state() -> bool | None:
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
                if resuming_journal and journal_state.database_swap_intent:
                    marker_state = active_marker_state()
                    if marker_state is None:
                        maintenance_required = True
                        raise RestoreConflictError("restore_database_state_unknown")
                    if journal_state.database_active and marker_state is not True:
                        maintenance_required = True
                        raise RestoreConflictError("restore_database_state_unknown")
                    if marker_state is True:
                        # The marker proves the swap happened, but the
                        # sidecar still needs its durable acknowledgement
                        # before the terminal event can be accepted.  Keep
                        # this transition observable on resume so a crash
                        # between the swap and the acknowledgement is not
                        # silently collapsed into a complete journal.
                        if not journal_state.database_active:
                            _append_restore_journal(
                                journal_path,
                                {
                                    "event": "database_active",
                                    **_journal_binding(journal_state.started),
                                },
                            )
                        journal_state = replace(
                            journal_state,
                            database_active=True,
                        )
                if journal_state.database_swap_intent and {
                    blob.key for blob in staged_blobs
                } != set(journal_state.published):
                    maintenance_required = True
                    raise RestoreConflictError("restore_journal_invalid")
                # A terminal journal can remain when the process crashed while
                # unlinking its sidecar.  The database marker is the only
                # proof that this operation completed; never replay its blob
                # publication or database swap.  If proof is unavailable the
                # maintenance gate stays set for operator recovery.
                if journal_state.complete:
                    marker_state = active_marker_state()
                    if marker_state is not True:
                        maintenance_required = True
                        raise RestoreConflictError("restore_database_state_unknown")
                    _remove_restore_journal(journal_path)
                    restored_files = len(staged_blobs)
                    return {
                        "backup_id": backup_id,
                        "restored_files": restored_files,
                    }
                if journal_state.database_active and any(
                    not _stored_blob_matches(blob) for blob in staged_blobs
                ):
                    # After the database marker is active, the journal is a
                    # forward-only recovery record.  A missing or changed
                    # published blob is an unresolved post-swap mutation, not
                    # permission to publish a replacement generation.
                    maintenance_required = True
                    raise RestoreConflictError("restore_destination_changed")
                try:
                    applied, created = _apply_staged_blobs(
                        staged_blobs,
                        rollback_dir,
                        journal_path=journal_path,
                        journal_state=journal_state,
                    )
                except Exception:
                    if not _journal_has_mutation_evidence(journal_state):
                        _remove_restore_journal(journal_path)
                    raise
                db_swapped = False
                try:
                    if any(not _stored_blob_matches(blob) for blob in staged_blobs):
                        raise RestoreConflictError("restore_destination_changed")
                    if not journal_state.database_active:
                        _sync_restored_ownership(
                            database_path,
                            applied,
                            archive_ownership=archive_ownership,
                            cache_ownership=cache_ownership,
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
                        # A retry of a journal that already recorded the swap
                        # intent must reuse that durable intent.  Appending a
                        # second one would make the journal invalid and, more
                        # importantly, would erase the ordering proof around
                        # the database point of no return.
                        if not journal_state.database_swap_intent:
                            _append_restore_journal(
                                journal_path,
                                {
                                    "event": "database_swap_intent",
                                    **_journal_binding(journal_state.started),
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
                                    **_journal_binding(journal_state.started),
                                },
                            )
                            journal_state = replace(journal_state, database_active=True)
                    else:
                        db_swapped = True
                    _append_restore_journal(
                        journal_path,
                        {
                            "event": "complete",
                            **_journal_binding(journal_state.started),
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
            if restore_cache_path is not None:
                cleanup_backup_cache(restore_cache_path)
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

    for meta in list_backup_sources():
        try:
            created = datetime.fromisoformat(meta.created_at)
            if created < cutoff:
                if delete_backup(meta.id, source_ref=meta.source_ref):
                    removed += 1
        except Exception as exc:
            # Retention is best effort per exact source.  A stale credential,
            # provider outage, or ownership conflict must never make us probe
            # another source or abort the remaining purge.  Keep diagnostics
            # secret-safe: source_ref is an opaque digest and exception text
            # may contain provider URLs or credentials.
            logger.warning(
                "backup purge: source %s could not be removed (%s)",
                meta.source_ref or "unknown",
                type(exc).__name__,
            )
            continue

    if removed:
        logger.info("backup purge: removed %d old backups", removed)
    return removed
