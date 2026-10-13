"""DB-first deletion outbox for exact, positively owned storage objects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import OwnedStorageObject, StorageDeleteIntent, StorageObjectState
from app.db.session import get_session_factory
from app.modules.administration import audit
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StorageBackend,
    StorageTier,
)
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_publication import (
    PublicationReservation,
    ReceiptReclaimResult,
    lock_publication_locator,
    same_creation,
)
from app.modules.storage.storage_receipts import (
    UnsafeStorageDeleteError,
    owned_receipt,
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
    lock_publication_locator(
        session, backend=backend.backend_name, namespace=namespace, key=key
    )
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


def enqueue_prevalidated_receipt(
    session: Session,
    receipt: CreationReceipt,
    *,
    object_kind: str,
    sha256: str | None,
    resource_kind: str | None = None,
    resource_id: int | str | None = None,
) -> StorageDeleteIntent:
    """SQL-only exact-delete authorization from already validated durable evidence.

    Domain authority/output locks precede the locator anchor. The caller owns
    this transaction; the processor performs storage I/O only after its commit.
    Completed intents remain immutable revocation evidence and are not pruned.
    """
    lock_publication_locator(
        session, backend=receipt.backend, namespace=receipt.namespace, key=receipt.key
    )
    with session.no_autoflush:
        existing = session.exec(
            select(StorageDeleteIntent).where(
                StorageDeleteIntent.backend == receipt.backend,
                StorageDeleteIntent.namespace == receipt.namespace,
                StorageDeleteIntent.key == receipt.key,
                StorageDeleteIntent.provider_ref == receipt.provider_ref,
            )
        ).all()
        for intent in existing:
            if same_creation(_intent_receipt(intent), receipt):
                return intent
            if intent.token == receipt.token:
                raise UnsafeStorageDeleteError("storage_receipt_token_collision")
        actor_id, authorized_at = _authorization_metadata()
        intent = StorageDeleteIntent(
            backend=receipt.backend,
            namespace=receipt.namespace,
            key=receipt.key,
            provider_ref=receipt.provider_ref,
            object_kind=object_kind,
            token=receipt.token,
            size_bytes=receipt.size,
            sha256=sha256,
            etag=receipt.etag,
            version_id=receipt.version_id,
            device=receipt.device,
            inode=receipt.inode,
            ctime_ns=receipt.ctime_ns,
            authorization_mode=StorageTier.VERIFIED.value,
            authorized_actor_id=actor_id,
            authorized_at=authorized_at,
            quarantine_state="none",
            resource_kind=resource_kind,
            resource_id=str(resource_id) if resource_id is not None else None,
        )
        session.add(intent)
        session.flush()
        return intent


@dataclass(frozen=True)
class PreparedOwnedDeletion:
    reservation: PublicationReservation
    receipt: CreationReceipt
    object_kind: str
    sha256: str | None
    authorization_mode: str
    resource_kind: str | None
    resource_id: int | str | None


def prepare_owned_key_deletion(
    session: Session,
    backend: StorageBackend,
    key: str,
    *,
    required_proof: bool = False,
    resource_kind: str | None = None,
    resource_id: int | str | None = None,
    allow_unverified: bool = False,
) -> PreparedOwnedDeletion | None:
    """Validate physical evidence before any domain/locator write locks."""
    try:
        namespace = backend.namespace_for(key)
        provider_ref = provider_ref_for_backend(backend, namespace=namespace)
    except Exception as exc:
        if required_proof:
            raise UnsafeStorageDeleteError("storage_ownership_unverified") from exc
        return None
    with session.no_autoflush:
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
        reservation = PublicationReservation.of(owned)
        receipt = owned_receipt(owned)
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
            return None
        if not matches:
            continue
        return PreparedOwnedDeletion(
            reservation=reservation,
            receipt=receipt,
            object_kind=owned.object_kind,
            sha256=owned.sha256,
            authorization_mode=_authorization(
                backend, allow_unverified=allow_unverified
            ),
            resource_kind=resource_kind,
            resource_id=resource_id,
        )
    if required_proof:
        raise UnsafeStorageDeleteError("storage_ownership_unverified")
    return None


def enqueue_prepared_owned_deletion(
    session: Session, prepared: PreparedOwnedDeletion, *, required_proof: bool = False
) -> bool:
    """SQL-only compare-and-retire after caller domain authority is locked."""
    receipt = prepared.receipt
    lock_publication_locator(
        session, backend=receipt.backend, namespace=receipt.namespace, key=receipt.key
    )
    current = session.get(
        OwnedStorageObject, prepared.reservation.id, populate_existing=True
    )
    if (
        current is None
        or PublicationReservation.of(current) != prepared.reservation
        or current.state is not StorageObjectState.COMMITTED
        or not same_creation(owned_receipt(current), receipt)
    ):
        if required_proof:
            raise UnsafeStorageDeleteError("storage_ownership_changed")
        return False
    intent = enqueue_prevalidated_receipt(
        session,
        receipt,
        object_kind=prepared.object_kind,
        sha256=prepared.sha256,
        resource_kind=prepared.resource_kind,
        resource_id=prepared.resource_id,
    )
    intent.authorization_mode = prepared.authorization_mode
    current.state = StorageObjectState.RETIRING
    current.next_recovery_at = None
    session.add(current)
    session.add(intent)
    return True


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
    """Preflight and retire one object; batch callers prepare all objects first."""
    prepared = prepare_owned_key_deletion(
        session,
        backend,
        key,
        required_proof=required_proof,
        resource_kind=resource_kind,
        resource_id=resource_id,
        allow_unverified=allow_unverified,
    )
    if prepared is None:
        return False
    return enqueue_prepared_owned_deletion(
        session, prepared, required_proof=required_proof
    )


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
    receipt = replace(receipt, provider_ref=expected_provider_ref)
    return enqueue_prevalidated_receipt(
        session,
        receipt,
        object_kind="capture_upload_slot",
        sha256=digest,
        resource_kind=resource_kind,
        resource_id=resource_id,
    )


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
    *,
    limit: int = 100,
    allow_unverified: bool = False,
    backend: StorageBackend | None = None,
    intent_ids: tuple[int, ...] | None = None,
) -> DeleteIntentResult:
    """Consume intents, using only the policy persisted on each row.

    ``allow_unverified`` is retained as a compatibility keyword for older
    callers, but cannot change the outcome of an already-authorized intent.
    """
    del allow_unverified
    completed = pending = blocked = 0
    explicit_backend = backend is not None
    if backend is None:
        backend = get_backend()
    now = utcnow()
    with get_session_factory().scoped_session() as session:
        statement = select(StorageDeleteIntent).where(
            StorageDeleteIntent.status.in_(["pending", "retry"]),  # type: ignore[attr-defined]
            (StorageDeleteIntent.next_attempt_at == None)  # noqa: E711
            | (StorageDeleteIntent.next_attempt_at <= now),  # pyright: ignore[reportOptionalOperand]
        )
        if intent_ids is not None:
            statement = statement.where(StorageDeleteIntent.id.in_(intent_ids))
        intents = session.exec(
            statement.order_by(StorageDeleteIntent.id.asc()).limit(limit)
        ).all()
        for intent in intents:
            backup_owner = not explicit_backend and (
                intent.backend == "backup-s3"
                or intent.backend.startswith("backup-opendal-")
            )
            if not backup_owner:
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
                if backup_owner:
                    from app.modules.backups.backup.receipt_cleanup import (
                        reclaim_receipt,
                    )

                    outcome = reclaim_receipt(receipt)
                else:
                    removed = backend.rollback_create(receipt)
                    outcome = (
                        ReceiptReclaimResult.REMOVED
                        if removed
                        else ReceiptReclaimResult.MISMATCH
                        if backend.exists(intent.key)
                        else ReceiptReclaimResult.ABSENT
                    )
                if outcome not in (
                    ReceiptReclaimResult.REMOVED,
                    ReceiptReclaimResult.ABSENT,
                ):
                    intent.status = "blocked"
                    intent.last_error = outcome.value
                    intent.quarantine_state = "blocked"
                    blocked += 1
                else:
                    # The outbox revocation fenced adopters during I/O. Retire
                    # any short-lived committed inventory proof in the same
                    # SQL phase that terminalizes the exact intent.
                    lock_publication_locator(
                        session,
                        backend=receipt.backend,
                        namespace=receipt.namespace,
                        key=receipt.key,
                    )
                    proofs = session.exec(
                        select(OwnedStorageObject)
                        .where(
                            OwnedStorageObject.backend == receipt.backend,
                            OwnedStorageObject.namespace == receipt.namespace,
                            OwnedStorageObject.key == receipt.key,
                            OwnedStorageObject.state != StorageObjectState.RETIRING,
                        )
                        .execution_options(populate_existing=True)
                    ).all()
                    for proof in proofs:
                        if (
                            (
                                proof.provider_ref == receipt.provider_ref
                                or (
                                    receipt.backend == "local"
                                    and proof.provider_ref is None
                                )
                            )
                            and proof.token is not None
                            and proof.size_bytes is not None
                            and same_creation(owned_receipt(proof), receipt)
                        ):
                            proof.state = StorageObjectState.RETIRING
                            proof.next_recovery_at = None
                            session.add(proof)
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
