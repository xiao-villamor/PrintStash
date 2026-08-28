"""Persist and consume exact, operation-proven storage ownership."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import OwnedStorageObject, StorageObjectState
from app.services.storage_backend import (
    CreationReceipt,
    StorageBackend,
    StorageCollisionError,
)

logger = get_logger(__name__)

_ORPHAN_GRACE = timedelta(hours=24)
_SMALL_HASH_LIMIT = 16 * 1024 * 1024
_SMALL_HASH_KINDS = {
    "thumbnail",
    "model_source_cover",
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


def _backend_name(backend: StorageBackend) -> str:
    value = getattr(backend, "backend_name", None)
    return value if isinstance(value, str) and value else "unknown"


def _namespace_for(backend: StorageBackend, key: str) -> str:
    value = backend.namespace_for(key)
    if isinstance(value, str) and value:
        return value
    return _backend_name(backend)


def _sqlite_write_transaction(session: Session) -> bool:
    bind = session.get_bind()
    if bind.dialect.name != "sqlite" or not session.in_transaction():
        return False
    connection = session.connection()
    raw = connection.connection.driver_connection
    return bool(getattr(raw, "in_transaction", False))


@contextmanager
def _publication_session(session: Session):
    """Use a durable writer unless SQLite's caller already owns its only writer.

    A fresh engine-bound session is the normal path and makes the reservation
    survive a later domain rollback. SQLite cannot open a second writer after
    the caller has flushed domain rows, so those legacy ID-derived key paths
    join the caller transaction until their keys can be decoupled from row IDs.
    """
    if _sqlite_write_transaction(session):
        yield session, False
        return
    with Session(bind=session.get_bind(), expire_on_commit=False) as independent:
        yield independent, True


def _commit_if_independent(session: Session, independent: bool) -> None:
    if independent:
        session.commit()
    else:
        session.flush()


def reserve_creation(
    session: Session,
    backend: StorageBackend,
    key: str,
    *,
    object_kind: str,
    expected_size: int | None = None,
    sha256: str | None = None,
) -> int:
    """Durably reserve one locator before publishing bytes to storage."""
    backend_name = _backend_name(backend)
    namespace = _namespace_for(backend, key)
    with _publication_session(session) as (reservation_session, independent):
        existing = reservation_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == backend_name,
                OwnedStorageObject.namespace == namespace,
                OwnedStorageObject.key == key,
            )
        ).first()
        if existing is not None:
            if (
                existing.state is not StorageObjectState.COMMITTED
                or backend.exists(key)
            ):
                raise StorageCollisionError(key)
            existing.state = StorageObjectState.PENDING
            existing.object_kind = object_kind
            existing.token = None
            existing.size_bytes = expected_size
            existing.sha256 = sha256
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
) -> None:
    with _publication_session(session) as (reservation_session, independent):
        row = reservation_session.get(OwnedStorageObject, reservation_id)
        if row is None or row.state is not StorageObjectState.PENDING:
            raise RuntimeError("storage_reservation_lost")
        row.token = receipt.token
        row.size_bytes = receipt.size
        row.sha256 = sha256 or row.sha256
        row.etag = receipt.etag
        row.version_id = receipt.version_id
        row.device = receipt.device
        row.inode = receipt.inode
        row.ctime_ns = receipt.ctime_ns
        row.last_error = None
        reservation_session.add(row)
        _commit_if_independent(reservation_session, independent)
    record_creation(
        session,
        receipt,
        object_kind=object_kind,
        sha256=sha256,
        reservation_id=reservation_id,
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
    digest = sha256 or hashlib.sha256(data).hexdigest()
    reservation_id = reserve_creation(
        session,
        backend,
        key,
        object_kind=object_kind,
        expected_size=len(data),
        sha256=digest,
    )
    try:
        receipt = backend.create_bytes(data, key)
    except Exception as exc:
        fail_publication(session, reservation_id, exc)
        raise
    complete_publication(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=digest,
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
    reservation_id = reserve_creation(
        session,
        backend,
        key,
        object_kind=object_kind,
        expected_size=expected_size,
        sha256=sha256,
    )
    try:
        receipt = backend.create_stream(source, key)
    except Exception as exc:
        fail_publication(session, reservation_id, exc)
        raise
    complete_publication(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=sha256,
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
) -> CreationReceipt:
    """Publish a staged file with evidence known before storage mutation."""
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
    complete_publication(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=digest,
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
        if row.backend != backend.backend_name:
            row.state = StorageObjectState.BLOCKED
            row.last_error = "storage_backend_mismatch"
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
) -> OwnedStorageObject:
    existing = (
        session.get(OwnedStorageObject, reservation_id)
        if reservation_id is not None
        else session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == receipt.backend,
                OwnedStorageObject.namespace == receipt.namespace,
                OwnedStorageObject.key == receipt.key,
            )
        ).first()
    )
    if existing is not None:
        # Atomic create-only publication proved the prior object is absent.
        # Refresh the stale receipt instead of violating the locator uniqueness
        # constraint (e.g. repair after an out-of-band thumbnail loss).
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
    )


def require_owned_key(session: Session, backend: StorageBackend, key: str) -> None:
    candidates = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.key == key,
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).all()
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
    candidates = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.key == key,
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).all()
    if candidates:
        require_owned_key(session, backend, key)
        return
    try:
        receipt = backend.adopt_existing(
            key,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
    except Exception as exc:
        raise UnsafeStorageDeleteError("storage_ownership_unverified") from exc
    record_creation(session, receipt, object_kind="legacy_artifact")


def replace_owned_bytes(
    session: Session,
    backend: StorageBackend,
    key: str,
    data: bytes,
    *,
    object_kind: str,
) -> CreationReceipt:
    candidates = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.key == key,
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).all()
    for row in candidates:
        current = _receipt(row)
        if not backend.creation_matches(current):
            continue
        replacement = backend.replace_bytes(data, current)
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
    candidates = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.key == key,
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).all()
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
