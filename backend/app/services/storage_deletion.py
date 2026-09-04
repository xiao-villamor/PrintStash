"""DB-first deletion outbox for exact, positively owned storage objects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import OwnedStorageObject, StorageDeleteIntent, StorageObjectState
from app.db.session import get_session_factory
from app.services import audit
from app.services.storage_backend import (
    CreationReceipt,
    StorageBackend,
    StorageTier,
    get_backend,
)
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    provider_ref_for_backend,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class DeleteIntentResult:
    completed: int = 0
    pending: int = 0
    blocked: int = 0


def cleanup_status(result: DeleteIntentResult) -> str:
    """Return the durable outcome of an exact-delete batch for API callers."""
    if result.blocked:
        return "blocked" if not result.completed and not result.pending else "partial"
    if result.pending:
        return "pending" if not result.completed else "partial"
    return "completed"


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
        provider_ref=row.provider_ref,
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
        provider_ref=row.provider_ref,
    )


def _content_sha256(backend: StorageBackend, key: str) -> str | None:
    """Hash a candidate object through the streaming backend seam."""
    try:
        digest = hashlib.sha256()
        for chunk in backend.stream_chunks(key):
            digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def _authorization(backend: StorageBackend, *, allow_unverified: bool) -> str:
    """Freeze the policy selected by the operation in the delete intent."""
    if allow_unverified:
        return "guarded"
    return backend.capabilities.tier.value


def _authorization_metadata() -> tuple[int | None, datetime]:
    actor_id, _ip = audit.current_audit_context()
    return actor_id, utcnow()


def record_legacy_blocked_intent(
    session: Session,
    backend: StorageBackend,
    *,
    key: str,
    size_bytes: int,
    sha256: str | None,
    object_kind: str,
    resource_id: int | str | None = None,
) -> StorageDeleteIntent:
    """Record an exact, retained legacy object that lacks a creation receipt.

    A confirmed logical purge may remove a pre-ledger catalog row, but it must
    never turn confirmation into permission to delete an object whose ownership
    cannot be proven.  The synthetic token makes this intent idempotent while
    the original path, size, and digest remain available for an administrator to
    recover or adopt explicitly later.
    """
    namespace = backend.namespace_for(key)
    provider_ref = provider_ref_for_backend(backend, namespace=namespace)
    token_material = (
        f"legacy:{backend.backend_name}:{namespace}:{key}:{size_bytes}:{sha256 or ''}"
    )
    token = hashlib.sha256(token_material.encode("utf-8")).hexdigest()
    existing = session.exec(
        select(StorageDeleteIntent).where(
            StorageDeleteIntent.backend == backend.backend_name,
            StorageDeleteIntent.provider_ref == provider_ref,
            StorageDeleteIntent.namespace == namespace,
            StorageDeleteIntent.key == key,
            StorageDeleteIntent.token == token,
        )
    ).first()
    if existing is not None:
        return existing
    actor_id, authorized_at = _authorization_metadata()
    intent = StorageDeleteIntent(
        backend=backend.backend_name,
        namespace=namespace,
        key=key,
        provider_ref=provider_ref,
        object_kind=object_kind,
        token=token,
        size_bytes=size_bytes,
        sha256=sha256,
        authorization_mode="legacy_unknown",
        authorized_actor_id=actor_id,
        authorized_at=authorized_at,
        quarantine_state="none",
        resource_kind=object_kind,
        resource_id=str(resource_id) if resource_id is not None else None,
        status="pending",
    )
    session.add(intent)
    session.flush()
    return intent


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
    try:
        namespace = backend.namespace_for(key)
        provider_ref = provider_ref_for_backend(backend, namespace=namespace)
    except Exception as exc:
        if required_proof:
            raise UnsafeStorageDeleteError("storage_ownership_unverified") from exc
        return False
    rows = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.backend == backend.backend_name,
            OwnedStorageObject.namespace == namespace,
            OwnedStorageObject.key == key,
            OwnedStorageObject.provider_ref == provider_ref,
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).all()
    for owned in rows:
        receipt = _owned_receipt(owned)
        try:
            if allow_unverified:
                if not owned.sha256:
                    if required_proof:
                        raise UnsafeStorageDeleteError("storage_hash_unavailable")
                    continue
                info = backend.object_info(receipt.key)
                matches = info is not None and info.size == receipt.size
                if matches and receipt.etag is not None:
                    matches = info.etag == receipt.etag
                if matches:
                    digest = _content_sha256(backend, receipt.key)
                    if digest is None:
                        raise UnsafeStorageDeleteError("storage_hash_unavailable")
                    matches = digest == owned.sha256.lower()
            else:
                matches = backend.creation_matches(receipt)
        except UnsafeStorageDeleteError:
            raise
        except Exception as exc:
            if required_proof:
                raise UnsafeStorageDeleteError("storage_verification_failed") from exc
            return False
        if not matches:
            continue
        actor_id, authorized_at = _authorization_metadata()
        intent = StorageDeleteIntent(
            backend=owned.backend,
            namespace=owned.namespace,
            key=owned.key,
            provider_ref=owned.provider_ref,
            object_kind=owned.object_kind,
            token=owned.token,
            size_bytes=owned.size_bytes,
            sha256=owned.sha256,
            etag=owned.etag,
            version_id=owned.version_id,
            device=owned.device,
            inode=owned.inode,
            ctime_ns=owned.ctime_ns,
            authorization_mode=_authorization(
                backend, allow_unverified=allow_unverified
            ),
            authorized_actor_id=actor_id,
            authorized_at=authorized_at,
            quarantine_state="none",
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
    expected_provider_ref = provider_ref_for_backend(
        backend, namespace=receipt.namespace
    )
    if receipt.provider_ref is None and backend.backend_name != "local":
        raise UnsafeStorageDeleteError("storage_provider_identity_missing")
    if receipt.provider_ref not in (None, expected_provider_ref):
        raise UnsafeStorageDeleteError("storage_provider_mismatch")
    if not backend.creation_matches(receipt):
        raise UnsafeStorageDeleteError("storage_object_no_longer_matches_receipt")
    digest = _content_sha256(backend, receipt.key)
    if digest is None:
        raise UnsafeStorageDeleteError("storage_hash_unavailable")
    existing = session.exec(
        select(StorageDeleteIntent).where(
            StorageDeleteIntent.backend == receipt.backend,
            StorageDeleteIntent.provider_ref == expected_provider_ref,
            StorageDeleteIntent.namespace == receipt.namespace,
            StorageDeleteIntent.key == receipt.key,
            StorageDeleteIntent.token == receipt.token,
        )
    ).first()
    if existing is not None:
        return existing
    actor_id, authorized_at = _authorization_metadata()
    intent = StorageDeleteIntent(
        backend=receipt.backend,
        namespace=receipt.namespace,
        key=receipt.key,
        provider_ref=provider_ref_for_backend(backend, namespace=receipt.namespace),
        object_kind="capture_upload_slot",
        token=receipt.token,
        size_bytes=receipt.size,
        sha256=digest,
        etag=receipt.etag,
        version_id=receipt.version_id,
        device=receipt.device,
        inode=receipt.inode,
        ctime_ns=receipt.ctime_ns,
        authorization_mode=_authorization(backend, allow_unverified=False),
        authorized_actor_id=actor_id,
        authorized_at=authorized_at,
        quarantine_state="none",
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
    # A backend may have completed a quarantine/delete before reporting an
    # error. Keep this marker until a later session reconciles the receipt.
    intent.quarantine_state = "pending"
    intent.updated_at = utcnow()


def process_storage_delete_intents(
    *, limit: int = 100, allow_unverified: bool = False
) -> DeleteIntentResult:
    """Consume intents, using only the policy persisted on each row.

    ``allow_unverified`` is retained as a compatibility keyword for older
    callers, but cannot change the outcome of an already-authorized intent.
    """
    del allow_unverified
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
                intent.quarantine_state = "blocked"
                intent.updated_at = utcnow()
                blocked += 1
                session.add(intent)
                session.commit()
                continue
            if intent.provider_ref is None:
                intent.status = "blocked"
                intent.last_error = "storage_provider_identity_missing"
                intent.quarantine_state = "blocked"
                intent.updated_at = utcnow()
                blocked += 1
                session.add(intent)
                session.commit()
                continue
            try:
                expected_namespace = backend.namespace_for(intent.key)
                expected_provider_ref = provider_ref_for_backend(
                    backend, namespace=expected_namespace
                )
            except Exception:
                expected_namespace = None
                expected_provider_ref = None
            if (
                expected_namespace != intent.namespace
                or expected_provider_ref != intent.provider_ref
            ):
                intent.status = "blocked"
                intent.last_error = "storage_provider_mismatch"
                intent.quarantine_state = "blocked"
                intent.updated_at = utcnow()
                blocked += 1
                session.add(intent)
                session.commit()
                continue
            if intent.authorization_mode != StorageTier.VERIFIED.value:
                intent.status = "blocked"
                intent.last_error = "storage_guarded_delete_unsupported"
                intent.quarantine_state = "blocked"
                intent.attempts += 1
                intent.updated_at = utcnow()
                blocked += 1
                session.add(intent)
                session.commit()
                continue
            try:
                # Commit before crossing the storage boundary. A worker crash
                # after this point leaves durable evidence for reconciliation.
                intent.quarantine_state = "pending"
                intent.updated_at = utcnow()
                session.add(intent)
                session.commit()
                receipt = _intent_receipt(intent)
                removed = backend.rollback_create(receipt)
                if not removed and backend.exists(intent.key):
                    intent.status = "blocked"
                    intent.last_error = "storage_receipt_mismatch"
                    intent.quarantine_state = "blocked"
                    blocked += 1
                else:
                    intent.status = "completed"
                    intent.completed_at = utcnow()
                    intent.last_error = None
                    intent.quarantine_state = "deleted"
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
