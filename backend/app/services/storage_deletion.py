"""DB-first deletion outbox for exact, positively owned storage objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import OwnedStorageObject, StorageDeleteIntent, StorageObjectState
from app.db.session import get_session_factory
from app.services.storage_backend import CreationReceipt, StorageBackend, get_backend
from app.services.storage_ownership import UnsafeStorageDeleteError

logger = get_logger(__name__)


@dataclass(frozen=True)
class DeleteIntentResult:
    completed: int = 0
    pending: int = 0
    blocked: int = 0


def _owned_receipt(row: OwnedStorageObject) -> CreationReceipt:
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


def _intent_receipt(row: StorageDeleteIntent) -> CreationReceipt:
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


def enqueue_owned_key(
    session: Session,
    backend: StorageBackend,
    key: str,
    *,
    required_proof: bool = False,
    resource_kind: str | None = None,
    resource_id: int | str | None = None,
    allow_unverified: bool = False,
) -> bool:
    """Move an ownership receipt into the durable delete outbox.

    This function performs verification and SQL mutations only.  It never
    deletes storage bytes; rollback therefore restores both the logical row and
    its ownership proof.
    """
    rows = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.key == key,
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).all()
    for owned in rows:
        receipt = _owned_receipt(owned)
        try:
            if allow_unverified:
                info = backend.object_info(receipt.key)
                matches = info is not None and info.size == receipt.size
                if matches and receipt.etag is not None:
                    matches = info.etag == receipt.etag
            else:
                matches = backend.creation_matches(receipt)
        except Exception as exc:
            if required_proof:
                raise UnsafeStorageDeleteError("storage_verification_failed") from exc
            return False
        if not matches:
            continue
        intent = StorageDeleteIntent(
            backend=owned.backend,
            namespace=owned.namespace,
            key=owned.key,
            object_kind=owned.object_kind,
            token=owned.token,
            size_bytes=owned.size_bytes,
            etag=owned.etag,
            version_id=owned.version_id,
            device=owned.device,
            inode=owned.inode,
            ctime_ns=owned.ctime_ns,
            resource_kind=resource_kind,
            resource_id=str(resource_id) if resource_id is not None else None,
        )
        session.add(intent)
        session.delete(owned)
        return True
    if required_proof:
        raise UnsafeStorageDeleteError("storage_ownership_unverified")
    return False


def enqueue_creation_receipt(
    session: Session,
    backend: StorageBackend,
    receipt: CreationReceipt,
    *,
    resource_kind: str | None = None,
    resource_id: int | str | None = None,
) -> StorageDeleteIntent:
    """Durably authorize deletion of one exact receipt without touching bytes.

    This is for short-lived objects (such as browser capture slots) that have
    not entered the long-lived ``OwnedStorageObject`` inventory.  The caller's
    transaction owns both this intent and its source rows; a rollback leaves
    the bytes and the source receipt intact.
    """
    if not backend.creation_matches(receipt):
        raise UnsafeStorageDeleteError("storage_object_no_longer_matches_receipt")
    existing = session.exec(
        select(StorageDeleteIntent).where(
            StorageDeleteIntent.backend == receipt.backend,
            StorageDeleteIntent.namespace == receipt.namespace,
            StorageDeleteIntent.key == receipt.key,
            StorageDeleteIntent.token == receipt.token,
        )
    ).first()
    if existing is not None:
        return existing
    intent = StorageDeleteIntent(
        backend=receipt.backend,
        namespace=receipt.namespace,
        key=receipt.key,
        object_kind="capture_upload_slot",
        token=receipt.token,
        size_bytes=receipt.size,
        etag=receipt.etag,
        version_id=receipt.version_id,
        device=receipt.device,
        inode=receipt.inode,
        ctime_ns=receipt.ctime_ns,
        resource_kind=resource_kind,
        resource_id=str(resource_id) if resource_id is not None else None,
    )
    session.add(intent)
    session.flush()
    return intent


def _mark_retry(intent: StorageDeleteIntent, exc: Exception) -> None:
    intent.status = "retry"
    intent.attempts += 1
    delay_seconds = min(3600, 2 ** min(intent.attempts, 10))
    intent.next_attempt_at = utcnow() + timedelta(seconds=delay_seconds)
    intent.last_error = type(exc).__name__[:255]
    intent.updated_at = utcnow()


def process_storage_delete_intents(
    *, limit: int = 100, allow_unverified: bool = False
) -> DeleteIntentResult:
    """Consume committed intents idempotently, preserving mismatched objects."""
    completed = pending = blocked = 0
    backend = get_backend()
    now = utcnow()
    with get_session_factory().scoped_session() as session:
        intents = session.exec(
            select(StorageDeleteIntent)
            .where(
                StorageDeleteIntent.status.in_(["pending", "retry"]),  # type: ignore[attr-defined]
                (StorageDeleteIntent.next_attempt_at == None)  # noqa: E711
                | (StorageDeleteIntent.next_attempt_at <= now),  # pyright: ignore[reportOptionalOperand]
            )
            .order_by(StorageDeleteIntent.id.asc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
        for intent in intents:
            if intent.backend != getattr(backend, "backend_name", intent.backend):
                intent.status = "blocked"
                intent.last_error = "storage_backend_mismatch"
                intent.updated_at = utcnow()
                blocked += 1
                session.add(intent)
                session.commit()
                continue
            try:
                receipt = _intent_receipt(intent)
                removed = (
                    backend.reclaim_unverified(
                        receipt.key,
                        expected_size=receipt.size,
                        expected_etag=receipt.etag,
                    )
                    if allow_unverified
                    else backend.rollback_create(receipt)
                )
                if not removed and backend.exists(intent.key):
                    intent.status = "blocked"
                    intent.last_error = "storage_receipt_mismatch"
                    blocked += 1
                else:
                    intent.status = "completed"
                    intent.completed_at = utcnow()
                    intent.last_error = None
                    completed += 1
                intent.attempts += 1
                intent.updated_at = utcnow()
            except Exception as exc:
                logger.exception(
                    "storage delete intent retry", extra={"intent_id": intent.id}
                )
                _mark_retry(intent, exc)
                pending += 1
            session.add(intent)
            session.commit()
    return DeleteIntentResult(completed=completed, pending=pending, blocked=blocked)
