"""Persist and consume exact, operation-proven storage ownership."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import OwnedStorageObject, StorageObjectState
from app.services.remote_io import RemoteIO
from app.services.storage_backend import (
    CreationReceipt,
    StorageBackend,
    StorageCollisionError,
    StorageTier,
)

logger = get_logger(__name__)

_ORPHAN_GRACE = timedelta(hours=24)
_SMALL_HASH_LIMIT = 16 * 1024 * 1024
_SMALL_HASH_KINDS = {
    "thumbnail",
    "model_source_cover",
    "multipart_model_cover",
    "source_cover",
    "collection_image",
    "document_image",
    "stl_cache",
    "derived_stl_cache",
}


class UnsafeStorageDeleteError(RuntimeError):
    """The exact target could not be positively and currently proven owned."""


@dataclass(frozen=True)
class OrphanSweepResult:
    examined: int = 0
    cleared: int = 0
    reclaimed: int = 0
    blocked: int = 0
    pending: int = 0


def _backend_name(backend: StorageBackend | RemoteIO) -> str:
    value = getattr(backend, "backend_name", None)
    return value if isinstance(value, str) and value else "unknown"


def _namespace_for(backend: StorageBackend | RemoteIO, key: str) -> str:
    namespace_for = getattr(backend, "namespace_for", None)
    value = (
        namespace_for(key)
        if callable(namespace_for)
        else getattr(backend, "namespace", None)
    )
    if isinstance(value, str) and value:
        return value
    return _backend_name(backend)


def _locator_rows(
    session: Session,
    backend: StorageBackend | RemoteIO,
    key: str,
    *,
    states: tuple[StorageObjectState, ...] | None = None,
    include_legacy: bool = True,
) -> list[OwnedStorageObject]:
    """Load receipts for one exact backend namespace, never key globally.

    A key is only meaningful inside its backend namespace.  Looking it up by
    key alone made a provider switch capable of finding a receipt from a
    different destination and silently rebinding it to the new target.
    """
    backend_name = _backend_name(backend)
    clauses = [OwnedStorageObject.key == key]
    if backend_name != "unknown":
        clauses.append(OwnedStorageObject.backend == backend_name)
    # Third-party/test adapters predating the namespace seam do not expose a
    # namespace.  Keep their compatibility path key-scoped; every production
    # StorageBackend supplies namespace_for and therefore receives exact
    # backend+namespace scoping.
    if backend_name != "unknown" and (
        callable(getattr(backend, "namespace_for", None))
        or hasattr(backend, "namespace")
    ):
        namespace = _namespace_for(backend, key)
        clauses.append(OwnedStorageObject.namespace == namespace)
        expected_ref = provider_ref_for_backend(backend, namespace=namespace)
        if _backend_name(backend) == "local" and include_legacy:
            clauses.append(
                (OwnedStorageObject.provider_ref == expected_ref)
                | OwnedStorageObject.provider_ref.is_(None)  # type: ignore[union-attr]
            )
        else:
            clauses.append(OwnedStorageObject.provider_ref == expected_ref)
    statement = select(OwnedStorageObject).where(*clauses)
    if states is not None:
        statement = statement.where(OwnedStorageObject.state.in_(states))  # type: ignore[attr-defined]
    return list(session.exec(statement).all())


def _normalized_endpoint(value: object) -> str:
    """Normalize an endpoint without retaining userinfo or query secrets."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise ValueError("storage_provider_endpoint_invalid") from None
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("storage_provider_endpoint_invalid")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("storage_provider_endpoint_invalid")
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("storage_provider_endpoint_invalid") from None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port in {80, 443} and (
        (port == 80 and parsed.scheme.lower() == "http")
        or (port == 443 and parsed.scheme.lower() == "https")
    ):
        netloc = host
    else:
        netloc = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def provider_ref_for_backend(
    backend: StorageBackend | RemoteIO, *, namespace: str | None = None
) -> str:
    """Return a stable, credential-free provider destination identity.

    The identity intentionally excludes access/secret keys so credential
    rotation does not relabel existing receipts.  Adapter-specific fields are
    read when available; local/legacy fakes still receive a deterministic
    backend+namespace identity.
    """
    name = _backend_name(backend)
    resolved_namespace = namespace or getattr(backend, "namespace", None)
    if not resolved_namespace:
        resolved_namespace = name
    endpoint = getattr(backend, "_endpoint_url", None)
    if endpoint is None:
        endpoint = getattr(backend, "endpoint_url", None)
    if endpoint is None and name == "s3":
        endpoint = settings.s3_endpoint_url
    if endpoint is None and name == "backup-s3":
        endpoint = settings.backup_s3_endpoint_url
    region = getattr(backend, "_region", None) or getattr(backend, "region", None)
    if region is None and name == "s3":
        region = settings.s3_region
    if region is None and name == "backup-s3":
        region = settings.backup_s3_region
    payload: dict[str, object] = {
        "backend": name,
        "provider": str(getattr(backend, "provider_id", name)),
        "transport": str(getattr(backend, "transport", name)),
        "endpoint": _normalized_endpoint(endpoint),
        "region": str(region or "").strip().lower(),
        "addressing_style": str(
            getattr(backend, "_addressing_style", None)
            or getattr(backend, "addressing_style", None)
            or "path"
        )
        .strip()
        .lower(),
    }
    transport = str(getattr(backend, "transport", name)).lower()
    spec = getattr(backend, "_spec", None)
    options = getattr(spec, "options", {})
    if not isinstance(options, dict):
        options = {}
    if transport == "s3":
        if options.get("endpoint_url") is not None:
            payload["endpoint"] = _normalized_endpoint(options.get("endpoint_url"))
        if options.get("region") is not None:
            payload["region"] = str(options.get("region") or "").strip().lower()
        if options.get("addressing_style") is not None:
            payload["addressing_style"] = (
                str(options.get("addressing_style")).strip().lower()
            )
        payload["namespace"] = str(resolved_namespace)
    elif transport == "webdav" or name in {"webdav", "nextcloud"}:
        # OpenDAL's namespace is only the managed root. The endpoint is the
        # actual destination and must be included, while credentials remain
        # deliberately absent from the identity.
        webdav_endpoint = getattr(backend, "_webdav_endpoint", None)
        if webdav_endpoint is None:
            webdav_endpoint = options.get("endpoint_url")
        payload["endpoint"] = _normalized_endpoint(webdav_endpoint)
        payload["root"] = (
            str(
                getattr(backend, "_webdav_root", None)
                or options.get("root")
                or resolved_namespace
            )
            .strip()
            .strip("/")
        )
    elif transport == "sftp" or name == "sftp":
        # SFTP has no URL endpoint. Pin the network destination and managed
        # root, excluding password/private-key/passphrase material (and any
        # future option whose name advertises it is secret).
        host = str(options.get("host", getattr(backend, "_host", ""))).strip()
        payload["sftp"] = {
            "host": host.lower().rstrip("."),
            "port": int(options.get("port", getattr(backend, "_port", 22)) or 22),
            "username": str(
                options.get("username", getattr(backend, "_username", ""))
            ).strip(),
            "root": str(options.get("root") or resolved_namespace).strip().strip("/"),
        }
    elif transport == "gdrive" or name in {"gdrive", "backup-gdrive"}:
        # The OAuth client id identifies the configured Google application but
        # grants no access by itself. Refresh/access tokens and the client
        # secret remain excluded from durable locators.
        payload["gdrive"] = {
            "client_id": str(options.get("client_id") or "").strip(),
            "root": str(options.get("root") or resolved_namespace).strip().strip("/"),
        }
    elif name not in {"s3", "backup-s3"}:
        payload["namespace"] = str(resolved_namespace)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@contextmanager
def _publication_session(session: Session):
    """Use a durable writer for every reservation and receipt transition.

    Publication ledgers are crash-recovery state, not caller-domain state.  A
    caller rollback must therefore never erase a PENDING reservation.  Callers
    publish only at seams which have ended their prior read/write transaction;
    this is deliberate on SQLite, where sharing the caller transaction would
    make durability depend on connection pooling.
    """
    with Session(bind=session.get_bind(), expire_on_commit=False) as independent:
        yield independent, True


def _commit_if_independent(session: Session, independent: bool) -> None:
    if independent:
        session.commit()
    else:
        session.flush()


def _require_publication_before_sqlite_dml(session: Session) -> None:
    """Reject publication after SQLite caller DML has acquired the write lock.

    A durable reservation must commit on a distinct connection before storage
    is mutated. SQLite cannot do that once the caller owns the database's write
    lock, and committing the caller here would violate its transaction boundary.
    Callers therefore order publication before their first write or establish a
    separate durable domain lease before publishing.
    """
    bind = session.get_bind()
    if bind.dialect.name != "sqlite" or not session.in_transaction():
        return
    connection = session.connection()
    raw = connection.connection.driver_connection
    if bool(getattr(raw, "in_transaction", False)):
        raise RuntimeError("storage_publication_requires_clean_sqlite_transaction")


def reserve_creation(
    session: Session,
    backend: StorageBackend | RemoteIO,
    key: str,
    *,
    object_kind: str,
    expected_size: int | None = None,
    sha256: str | None = None,
    provider_ref: str | None = None,
) -> int:
    """Durably reserve one locator before publishing bytes to storage."""
    backend_name = _backend_name(backend)
    namespace = _namespace_for(backend, key)
    provider_ref = provider_ref or provider_ref_for_backend(
        backend, namespace=namespace
    )
    with _publication_session(session) as (reservation_session, independent):
        existing_rows = _locator_rows(reservation_session, backend, key)
        # A row without provider identity is an old receipt and cannot be
        # silently enrolled into a newly configured provider.  The only safe
        # path is an explicit adoption/upgrade after the exact remote object
        # has been proved by the caller.
        if any(
            row.provider_ref != provider_ref
            and (row.provider_ref is not None or backend_name in {"s3", "backup-s3"})
            for row in existing_rows
        ):
            raise StorageCollisionError(key)
        existing = next(iter(existing_rows), None)
        if existing is not None:
            if existing.state is not StorageObjectState.COMMITTED or backend.exists(
                key
            ):
                raise StorageCollisionError(key)
            existing.state = StorageObjectState.PENDING
            existing.object_kind = object_kind
            existing.token = None
            existing.size_bytes = expected_size
            existing.sha256 = sha256
            existing.provider_ref = provider_ref
            existing.etag = None
            existing.version_id = None
            existing.device = None
            existing.inode = None
            existing.ctime_ns = None
            existing.committed_at = None
            existing.last_error = None
            existing.created_at = utcnow()
            reservation_session.add(existing)
            _commit_if_independent(reservation_session, independent)
            assert existing.id is not None
            return existing.id
        row = OwnedStorageObject(
            backend=backend_name,
            namespace=namespace,
            key=key,
            object_kind=object_kind,
            state=StorageObjectState.PENDING,
            size_bytes=expected_size,
            sha256=sha256,
            provider_ref=provider_ref,
        )
        reservation_session.add(row)
        try:
            _commit_if_independent(reservation_session, independent)
        except IntegrityError as exc:
            reservation_session.rollback()
            raise StorageCollisionError(key) from exc
        reservation_session.refresh(row)
        assert row.id is not None
        return row.id


def fail_publication(session: Session, reservation_id: int, exc: Exception) -> None:
    with _publication_session(session) as (reservation_session, independent):
        row = reservation_session.get(OwnedStorageObject, reservation_id)
        if row is None or row.state is not StorageObjectState.PENDING:
            return
        row.last_error = exc.__class__.__name__[:255]
        reservation_session.add(row)
        _commit_if_independent(reservation_session, independent)


def complete_publication(
    session: Session,
    reservation_id: int,
    receipt: CreationReceipt,
    *,
    object_kind: str,
    sha256: str | None,
    provider_ref: str | None = None,
) -> None:
    with _publication_session(session) as (reservation_session, independent):
        row = reservation_session.get(OwnedStorageObject, reservation_id)
        if row is None or row.state is not StorageObjectState.PENDING:
            raise RuntimeError("storage_reservation_lost")
        incoming_provider_ref = provider_ref or receipt.provider_ref
        if (
            row.backend != receipt.backend
            or row.namespace != receipt.namespace
            or row.key != receipt.key
        ):
            raise StorageCollisionError("storage_locator_mismatch")
        if row.provider_ref not in (None, incoming_provider_ref):
            raise StorageCollisionError("storage_provider_mismatch")
        row.token = receipt.token
        row.size_bytes = receipt.size
        row.sha256 = sha256 or row.sha256
        row.provider_ref = incoming_provider_ref or row.provider_ref
        row.etag = receipt.etag
        row.version_id = receipt.version_id
        row.device = receipt.device
        row.inode = receipt.inode
        row.ctime_ns = receipt.ctime_ns
        row.last_error = None
        reservation_session.add(row)
        _commit_if_independent(reservation_session, independent)
    # The caller may have pending domain rows which intentionally remain in its
    # transaction. Do not trigger an autoflush while joining the durable receipt;
    # publication must remain independently durable even if that transaction is
    # subsequently rolled back.
    with session.no_autoflush:
        record_creation(
            session,
            receipt,
            object_kind=object_kind,
            sha256=sha256,
            reservation_id=reservation_id,
            provider_ref=provider_ref,
        )


def publish_bytes(
    session: Session,
    backend: StorageBackend,
    key: str,
    data: bytes,
    *,
    object_kind: str,
    sha256: str | None = None,
) -> CreationReceipt:
    """Reserve, create, then join ownership to the caller's transaction."""
    _require_publication_before_sqlite_dml(session)
    digest = sha256 or hashlib.sha256(data).hexdigest()
    provider_ref = provider_ref_for_backend(
        backend, namespace=_namespace_for(backend, key)
    )
    reservation_id = reserve_creation(
        session,
        backend,
        key,
        object_kind=object_kind,
        expected_size=len(data),
        sha256=digest,
        provider_ref=provider_ref,
    )
    try:
        receipt = backend.create_bytes(data, key)
    except Exception as exc:
        fail_publication(session, reservation_id, exc)
        raise
    receipt = replace(receipt, provider_ref=provider_ref)
    complete_publication(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=digest,
        provider_ref=provider_ref,
    )
    return receipt


def publish_stream(
    session: Session,
    backend: StorageBackend,
    key: str,
    source: BinaryIO,
    *,
    object_kind: str,
    expected_size: int | None = None,
    sha256: str | None = None,
) -> CreationReceipt:
    """Publish a caller-owned stream without buffering it in memory."""
    _require_publication_before_sqlite_dml(session)
    provider_ref = provider_ref_for_backend(
        backend, namespace=_namespace_for(backend, key)
    )
    reservation_id = reserve_creation(
        session,
        backend,
        key,
        object_kind=object_kind,
        expected_size=expected_size,
        sha256=sha256,
        provider_ref=provider_ref,
    )
    try:
        receipt = backend.create_stream(source, key)
    except Exception as exc:
        fail_publication(session, reservation_id, exc)
        raise
    receipt = replace(receipt, provider_ref=provider_ref)
    complete_publication(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=sha256,
        provider_ref=provider_ref,
    )
    return receipt


def publish_file(
    session: Session,
    backend: StorageBackend,
    key: str,
    source: Path,
    *,
    object_kind: str,
    sha256: str | None = None,
    move: bool = False,
    provider_ref: str | None = None,
) -> CreationReceipt:
    """Publish a staged file with evidence known before storage mutation."""
    _require_publication_before_sqlite_dml(session)
    effective_provider_ref = provider_ref or provider_ref_for_backend(
        backend,
        namespace=_namespace_for(backend, key),
    )
    digest = sha256
    if digest is None:
        hasher = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    size = source.stat().st_size
    reservation_id = reserve_creation(
        session,
        backend,
        key,
        object_kind=object_kind,
        expected_size=size,
        sha256=digest,
        provider_ref=effective_provider_ref,
    )
    try:
        if move:
            receipt = backend.move_in(source, key)
        else:
            with source.open("rb") as handle:
                receipt = backend.create_stream(handle, key)
    except Exception as exc:
        fail_publication(session, reservation_id, exc)
        raise
    receipt = replace(receipt, provider_ref=effective_provider_ref)
    complete_publication(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=digest,
        provider_ref=effective_provider_ref,
    )
    return receipt


def sweep_orphaned_publications(
    session: Session,
    backend: StorageBackend,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> OrphanSweepResult:
    """Reclaim stale never-committed objects without scanning storage."""
    current = now or utcnow()
    cutoff = current - _ORPHAN_GRACE
    rows = session.exec(
        select(OwnedStorageObject)
        .where(
            OwnedStorageObject.state == StorageObjectState.PENDING,
            OwnedStorageObject.created_at < cutoff,
        )
        .order_by(OwnedStorageObject.id.asc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    cleared = reclaimed = blocked = pending = 0
    for row in rows:
        if row.object_kind in {
            "backup",
            "backup-legacy",
            "backup-cache",
            "backup-cloud-cache",
        }:
            # Backup publications have a provider-aware reconciler and cache
            # objects have an exact per-source cleanup path. The generic sweep
            # must never reclaim either class as a normal vault blob.
            pending += 1
            continue
        if row.backend != backend.backend_name:
            row.state = StorageObjectState.BLOCKED
            row.last_error = "storage_backend_mismatch"
            session.add(row)
            blocked += 1
            continue
        expected_provider_ref = provider_ref_for_backend(
            backend, namespace=row.namespace
        )
        transport = str(getattr(backend, "transport", "")).lower()
        provider_bound = backend.backend_name in {
            "s3",
            "backup-s3",
            "opendal",
            "webdav",
            "nextcloud",
            "sftp",
        } or transport in {"s3", "webdav", "sftp"}
        if row.provider_ref != expected_provider_ref and not (
            row.provider_ref is None and not provider_bound
        ):
            row.state = StorageObjectState.BLOCKED
            row.last_error = (
                "storage_provider_identity_missing"
                if row.provider_ref is None
                else "storage_provider_mismatch"
            )
            session.add(row)
            blocked += 1
            continue
        # A content check followed by a remote delete is not an ownership
        # proof on Guarded transports: another writer can replace the key in
        # between. Retain the bytes and make the operator's reauthorization
        # requirement durable until the provider can prove quarantine/delete.
        if backend.capabilities.tier is not StorageTier.VERIFIED:
            row.state = StorageObjectState.BLOCKED
            row.last_error = "storage_reclaim_unsupported"
            session.add(row)
            blocked += 1
            continue
        try:
            if row.version_id is not None:
                removed = backend.reclaim_unverified(
                    row.key,
                    expected_size=row.size_bytes or 0,
                    expected_etag=row.etag,
                    expected_sha256=row.sha256,
                    expected_version_id=row.version_id,
                )
                if not removed:
                    row.state = StorageObjectState.BLOCKED
                    row.last_error = "storage_reclaim_mismatch"
                    session.add(row)
                    blocked += 1
                    continue
                session.delete(row)
                reclaimed += 1
                continue
            info = backend.object_info(row.key)
            if info is None:
                session.delete(row)
                cleared += 1
                continue
            if row.size_bytes is None or info.size != row.size_bytes:
                row.state = StorageObjectState.BLOCKED
                row.last_error = "storage_size_mismatch"
                session.add(row)
                blocked += 1
                continue

            evidence_matches = False
            if row.etag is not None:
                evidence_matches = info.etag == row.etag
            elif (
                row.object_kind in _SMALL_HASH_KINDS
                and row.sha256 is not None
                and info.size <= _SMALL_HASH_LIMIT
            ):
                digest = hashlib.sha256()
                for chunk in backend.stream_chunks(row.key):
                    digest.update(chunk)
                evidence_matches = digest.hexdigest() == row.sha256.lower()
            if not evidence_matches:
                row.state = StorageObjectState.BLOCKED
                row.last_error = "storage_evidence_mismatch"
                session.add(row)
                blocked += 1
                continue

            removed = backend.reclaim_unverified(
                row.key,
                expected_size=info.size,
                expected_etag=info.etag,
                expected_sha256=(
                    row.sha256
                    if row.etag is None and row.object_kind in _SMALL_HASH_KINDS
                    else None
                ),
                expected_version_id=row.version_id,
            )
            if not removed:
                row.state = StorageObjectState.BLOCKED
                row.last_error = "storage_reclaim_mismatch"
                session.add(row)
                blocked += 1
                continue
            session.delete(row)
            reclaimed += 1
        except Exception as exc:
            logger.exception("storage orphan sweep retry", extra={"object_id": row.id})
            row.last_error = exc.__class__.__name__[:255]
            session.add(row)
            pending += 1
    session.flush()
    return OrphanSweepResult(
        examined=len(rows),
        cleared=cleared,
        reclaimed=reclaimed,
        blocked=blocked,
        pending=pending,
    )


def record_creation(
    session: Session,
    receipt: CreationReceipt,
    *,
    object_kind: str,
    sha256: str | None = None,
    reservation_id: int | None = None,
    provider_ref: str | None = None,
    upgrade_provider_ref: bool = False,
) -> OwnedStorageObject:
    if provider_ref is None:
        # Receipts serialized before provider identity was introduced have no
        # trustworthy destination fingerprint. Preserve that NULL marker:
        # local reads may use the legacy row, while remote destructive paths
        # must fail closed until exact-content adoption upgrades it.
        provider_ref = receipt.provider_ref
    existing = (
        session.get(OwnedStorageObject, reservation_id)
        if reservation_id is not None
        else session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == receipt.backend,
                OwnedStorageObject.namespace == receipt.namespace,
                OwnedStorageObject.key == receipt.key,
                # A receipt is scoped to its persisted provider destination;
                # do not select a same-key row from another provider.
                OwnedStorageObject.provider_ref == provider_ref,
            )
        ).first()
    )
    if existing is not None:
        # Atomic create-only publication proved the prior object is absent.
        # Refresh the stale receipt instead of violating the locator uniqueness
        # constraint (e.g. repair after an out-of-band thumbnail loss).
        if (
            existing.backend != receipt.backend
            or existing.namespace != receipt.namespace
            or existing.key != receipt.key
        ):
            raise UnsafeStorageDeleteError("storage_locator_mismatch")
        if (
            existing.provider_ref is not None
            and provider_ref is not None
            and existing.provider_ref != provider_ref
        ):
            raise UnsafeStorageDeleteError("storage_provider_identity_mismatch")
        if upgrade_provider_ref and existing.provider_ref is None:
            existing.provider_ref = provider_ref
        existing.object_kind = object_kind
        existing.state = StorageObjectState.COMMITTED
        existing.token = receipt.token
        existing.size_bytes = receipt.size
        existing.sha256 = sha256 or existing.sha256
        existing.etag = receipt.etag
        existing.version_id = receipt.version_id
        existing.device = receipt.device
        existing.inode = receipt.inode
        existing.ctime_ns = receipt.ctime_ns
        existing.committed_at = utcnow()
        existing.last_error = None
        session.add(existing)
        return existing
    if reservation_id is None:
        legacy_statement = select(OwnedStorageObject).where(
            OwnedStorageObject.backend == receipt.backend,
            OwnedStorageObject.namespace == receipt.namespace,
            OwnedStorageObject.key == receipt.key,
        )
        if upgrade_provider_ref:
            # Explicit adoption may coexist with a same-locator receipt from a
            # different provider. Only a genuinely pre-provider row is eligible
            # for in-place upgrade; foreign provider rows remain untouched and
            # the validated current-provider receipt is inserted as a sibling.
            legacy_statement = legacy_statement.where(
                OwnedStorageObject.provider_ref.is_(None)  # type: ignore[union-attr]
            )
        legacy = session.exec(legacy_statement).first()
        if legacy is not None:
            if not (upgrade_provider_ref and legacy.provider_ref is None):
                raise UnsafeStorageDeleteError("storage_provider_identity_mismatch")
            legacy.provider_ref = provider_ref
            existing = legacy
            existing.object_kind = object_kind
            existing.state = StorageObjectState.COMMITTED
            existing.token = receipt.token
            existing.size_bytes = receipt.size
            existing.sha256 = sha256 or existing.sha256
            existing.etag = receipt.etag
            existing.version_id = receipt.version_id
            existing.device = receipt.device
            existing.inode = receipt.inode
            existing.ctime_ns = receipt.ctime_ns
            existing.committed_at = utcnow()
            existing.last_error = None
            session.add(existing)
            return existing
    row = OwnedStorageObject(
        backend=receipt.backend,
        namespace=receipt.namespace,
        key=receipt.key,
        object_kind=object_kind,
        state=StorageObjectState.COMMITTED,
        token=receipt.token,
        size_bytes=receipt.size,
        sha256=sha256,
        provider_ref=provider_ref,
        etag=receipt.etag,
        version_id=receipt.version_id,
        device=receipt.device,
        inode=receipt.inode,
        ctime_ns=receipt.ctime_ns,
        committed_at=utcnow(),
    )
    session.add(row)
    return row


def _receipt(row: OwnedStorageObject) -> CreationReceipt:
    if row.token is None or row.size_bytes is None:
        raise UnsafeStorageDeleteError("storage_ownership_incomplete")
    return CreationReceipt(
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


def require_owned_key(session: Session, backend: StorageBackend, key: str) -> None:
    candidates = _locator_rows(
        session, backend, key, states=(StorageObjectState.COMMITTED,)
    )
    if not candidates:
        raise UnsafeStorageDeleteError("storage_ownership_unverified")
    for row in candidates:
        try:
            if backend.creation_matches(_receipt(row)):
                return
        except Exception as exc:
            raise UnsafeStorageDeleteError("storage_verification_failed") from exc
    raise UnsafeStorageDeleteError("storage_object_no_longer_matches_receipt")


def require_or_adopt_legacy_artifact(
    session: Session,
    backend: StorageBackend,
    key: str,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    """Require proof, or safely reconstruct it for one pre-ledger Artifact.

    Existing but mismatched receipts are never replaced. Adoption is attempted
    only when the ledger has no claim at all, and the backend must independently
    prove both the historical content hash and a stable deletable identity.
    """
    candidates = _locator_rows(
        session,
        backend,
        key,
        states=(StorageObjectState.COMMITTED,),
        include_legacy=False,
    )
    if candidates:
        require_owned_key(session, backend, key)
        return
    namespace = _namespace_for(backend, key)
    current_provider_ref = provider_ref_for_backend(backend, namespace=namespace)
    # A pre-provider receipt is still usable for reads, but cannot be inferred
    # as belonging to this adapter. Adoption must prove exact bytes and then
    # upgrade that one row to the current destination identity.
    legacy = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.backend == _backend_name(backend),
            OwnedStorageObject.namespace == namespace,
            OwnedStorageObject.key == key,
            OwnedStorageObject.provider_ref.is_(None),  # type: ignore[union-attr]
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).first()
    if legacy is not None:
        try:
            receipt = backend.adopt_existing(
                key,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
        except Exception as exc:
            raise UnsafeStorageDeleteError("storage_ownership_unverified") from exc
        record_creation(
            session,
            receipt,
            object_kind="legacy_artifact",
            sha256=expected_sha256,
            provider_ref=current_provider_ref,
            upgrade_provider_ref=True,
        )
        return
    try:
        receipt = backend.adopt_existing(
            key,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
    except Exception as exc:
        raise UnsafeStorageDeleteError("storage_ownership_unverified") from exc
    record_creation(
        session,
        receipt,
        object_kind="legacy_artifact",
        sha256=expected_sha256,
        provider_ref=current_provider_ref,
    )


def replace_owned_bytes(
    session: Session,
    backend: StorageBackend,
    key: str,
    data: bytes,
    *,
    object_kind: str,
) -> CreationReceipt:
    candidates = _locator_rows(
        session, backend, key, states=(StorageObjectState.COMMITTED,)
    )
    for row in candidates:
        current = _receipt(row)
        if not backend.creation_matches(current):
            continue
        replacement = backend.replace_bytes(data, current)
        backend_name = _backend_name(backend)
        namespace = _namespace_for(backend, key)
        expected_ref = provider_ref_for_backend(backend, namespace=namespace)
        if (
            (backend_name != "unknown" and replacement.backend != backend_name)
            or (namespace != "unknown" and replacement.namespace != namespace)
            or replacement.key != key
            or (
                backend_name != "unknown"
                and replacement.provider_ref is not None
                and replacement.provider_ref != expected_ref
            )
        ):
            raise UnsafeStorageDeleteError("storage_locator_mismatch")
        row.backend = replacement.backend
        row.namespace = replacement.namespace
        row.token = replacement.token
        row.size_bytes = replacement.size
        row.etag = replacement.etag
        row.version_id = replacement.version_id
        row.device = replacement.device
        row.inode = replacement.inode
        row.ctime_ns = replacement.ctime_ns
        row.object_kind = object_kind
        row.sha256 = hashlib.sha256(data).hexdigest()
        row.provider_ref = expected_ref
        session.add(row)
        return replacement
    raise UnsafeStorageDeleteError("storage_ownership_unverified")


def delete_owned_key(
    session: Session,
    backend: StorageBackend,
    key: str,
    *,
    required_proof: bool = False,
) -> bool:
    """Delete *key* only if a persisted creation receipt still matches it."""
    candidates = _locator_rows(
        session, backend, key, states=(StorageObjectState.COMMITTED,)
    )
    for row in candidates:
        try:
            removed = backend.rollback_create(_receipt(row))
        except Exception as exc:
            logger.exception(
                "owned storage delete failed",
                extra={"key": key, "object_kind": row.object_kind},
            )
            if required_proof:
                raise UnsafeStorageDeleteError("storage_delete_failed") from exc
            return False
        if removed:
            session.delete(row)
            logger.info(
                "owned storage object deleted",
                extra={"key": key, "object_kind": row.object_kind},
            )
            return True
        if required_proof:
            raise UnsafeStorageDeleteError("storage_object_no_longer_matches_receipt")
        return False
    logger.warning(
        "storage delete skipped: no matching positive ownership receipt",
        extra={"key": key},
    )
    if required_proof:
        raise UnsafeStorageDeleteError("storage_ownership_unverified")
    return False
