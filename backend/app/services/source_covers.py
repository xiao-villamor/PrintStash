"""Private storage lifecycle for representative provenance-source covers."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import ModelSourceCover, OwnedStorageObject, StagingLease
from app.services import staging_leases
from app.services.source_cover_processing import process_source_cover_upload
from app.services.storage_backend import (
    CreationReceipt,
    StorageBackend,
    StorageCollisionError,
    StorageObjectInfo,
)
from app.services.storage_ownership import (
    publish_bytes,
    record_creation,
    replace_owned_bytes,
)


@dataclass(frozen=True)
class SourceCoverWrite:
    cover: ModelSourceCover
    created: bool
    creation_receipt: CreationReceipt | None = None
    replacement_receipt: CreationReceipt | None = None
    replaced_bytes: bytes | None = None


@contextmanager
def _intent_session(caller: Session) -> Iterator[Session]:
    """Open the small transaction that owns cover publication intent.

    Cover publication is an external side effect.  Its recovery row therefore
    has to be committed before bytes are published, but that commit must never
    commit the transaction which is terminalizing an Inbox item.  Binding a
    fresh SQLModel session to the caller's engine gives the intent its own
    transaction while retaining the caller's database configuration.
    """
    bind = caller.get_bind()
    with Session(bind=bind, expire_on_commit=False) as intent:
        yield intent


def _delete_durable_cover_intent(
    caller: Session,
    *,
    cover_id: int,
    storage_key: str,
    preserve_ownership_intent: bool = False,
) -> None:
    """Remove a failed new-cover intent in its own committed transaction."""
    for instance in tuple(caller.identity_map.values()):
        if isinstance(instance, OwnedStorageObject) and instance.key == storage_key:
            caller.expunge(instance)
    with _intent_session(caller) as intent:
        cover = intent.get(ModelSourceCover, cover_id)
        if cover is not None:
            intent.delete(cover)
        # The FK cascade normally removes this row with the cover.  Deleting
        # it explicitly also handles databases where a crash left the child
        # row visible before FK enforcement was enabled.
        for lease in intent.exec(
            select(StagingLease).where(StagingLease.model_source_cover_id == cover_id)
        ).all():
            intent.delete(lease)
        if not preserve_ownership_intent:
            for proof in intent.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == storage_key)
            ).all():
                intent.delete(proof)
        intent.commit()


def _finish_replacement_rollback(
    caller: Session,
    backend: StorageBackend,
    *,
    cover_id: int,
    replaced_bytes: bytes,
    replacement_receipt: CreationReceipt,
) -> None:
    """Restore old bytes and persist their proof without caller commit."""
    try:
        restored = backend.replace_bytes(replaced_bytes, replacement_receipt)
    except Exception:
        # The durable lease remains available for restart reconciliation.
        return
    with _intent_session(caller) as intent:
        record_creation(intent, restored, object_kind="model_source_cover")
        for lease in intent.exec(
            select(StagingLease).where(StagingLease.model_source_cover_id == cover_id)
        ).all():
            intent.delete(lease)
        intent.commit()


def _receipt_json(receipt: CreationReceipt) -> str:
    return json.dumps(
        {
            "key": receipt.key,
            "size": receipt.size,
            "token": receipt.token,
            "backend": receipt.backend,
            "namespace": receipt.namespace,
            "etag": receipt.etag,
            "version_id": receipt.version_id,
            "device": receipt.device,
            "inode": receipt.inode,
            "ctime_ns": receipt.ctime_ns,
        },
        sort_keys=True,
    )


def _receipt_from_json(value: str | None) -> CreationReceipt | None:
    try:
        raw = json.loads(value or "")
        if not isinstance(raw, dict):
            return None
        return CreationReceipt(**raw)
    except (TypeError, ValueError):
        return None


def _cover_lease(session: Session, cover_id: int) -> StagingLease | None:
    return session.exec(
        select(StagingLease).where(StagingLease.model_source_cover_id == cover_id)
    ).first()


def _owned_receipt(row: OwnedStorageObject) -> CreationReceipt:
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


def _durable_pending_cover_intent(
    session: Session,
    backend: StorageBackend,
    *,
    cover: ModelSourceCover,
    actor_id: int | None,
    destination_key: str,
    size_bytes: int,
    sha256: str,
) -> StagingLease:
    if cover.id is None:
        raise RuntimeError("cover_pending_intent_missing")
    with _intent_session(session) as intent:
        persisted = intent.get(ModelSourceCover, cover.id)
        if persisted is None:
            raise RuntimeError("cover_pending_intent_missing")
        lease = _cover_lease(intent, cover.id)
        if lease is not None and (
            lease.size_bytes != size_bytes or lease.sha256 != sha256
        ):
            # A prior replacement may have published its bytes and crashed
            # before the caller committed the receipt/release. Do not reuse
            # that generation for a new payload: first recover it, or prove
            # the old owned bytes are still current and terminalize the stale
            # intent, then allocate a fresh lease below.
            recovered = _recover_pending_cover(
                intent, backend, cover=persisted, lease=lease
            )
            if recovered is not None:
                persisted.size_bytes = lease.size_bytes
                persisted.updated_at = utcnow()
                record_creation(intent, recovered, object_kind="model_source_cover")
                intent.delete(lease)
            else:
                current_owned = False
                for proof in intent.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.key == persisted.storage_key
                    )
                ).all():
                    if backend.creation_matches(_owned_receipt(proof)):
                        current_owned = True
                        break
                if not current_owned:
                    raise RuntimeError("cover_pending_recovery_required")
                intent.delete(lease)
            intent.flush()
            lease = None
        if lease is None:
            staging_leases.create_cover_lease(
                intent,
                model_source_cover_id=cover.id,
                owner_user_id=actor_id,
                destination_key=destination_key,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        # This is the only commit in the pre-publication phase.  It commits
        # the cover intent, never the caller's Inbox/model transaction.
        intent.commit()
    session.expire(cover)
    session.refresh(cover)
    refreshed = _cover_lease(session, cover.id)
    if refreshed is None:
        raise RuntimeError("cover_pending_intent_missing")
    return refreshed


def _create_durable_cover(
    session: Session,
    *,
    provenance_source_id: int,
    actor_id: int | None,
    destination_key: str,
    size_bytes: int,
    sha256: str,
) -> ModelSourceCover:
    """Create and commit a new cover row plus its recovery lease."""
    with _intent_session(session) as intent:
        cover = ModelSourceCover(
            provenance_source_id=provenance_source_id,
            storage_key=destination_key,
            content_type="image/webp",
            size_bytes=size_bytes,
            created_by=actor_id,
        )
        intent.add(cover)
        intent.flush()
        assert cover.id is not None
        staging_leases.create_cover_lease(
            intent,
            model_source_cover_id=cover.id,
            owner_user_id=actor_id,
            destination_key=destination_key,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        intent.commit()
        cover_id = cover.id
    persisted = session.get(ModelSourceCover, cover_id)
    if persisted is None:
        raise RuntimeError("cover_pending_intent_missing")
    return persisted


def _recover_pending_cover(
    session: Session,
    backend: StorageBackend,
    *,
    cover: ModelSourceCover,
    lease: StagingLease,
) -> CreationReceipt | None:
    """Reconcile a cover object published before its receipt was committed."""
    receipt = _receipt_from_json(lease.receipt_json)
    if receipt is not None:
        try:
            if backend.creation_matches(receipt):
                return receipt
        except Exception:
            raise
    try:
        receipt = backend.adopt_existing(
            lease.destination_key or cover.storage_key,
            expected_size=lease.size_bytes,
            expected_sha256=lease.sha256,
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError, NotImplementedError):
        return None
    if (
        receipt.key != (lease.destination_key or cover.storage_key)
        or receipt.size != lease.size_bytes
    ):
        return None
    return receipt


def _discard_cover_if_absent(
    session: Session,
    backend: StorageBackend,
    *,
    cover: ModelSourceCover,
    lease: StagingLease,
) -> bool:
    """Discard an expired intent only after proving its destination is absent.

    ``object_info`` is deliberately the sole existence probe here.  An object
    that exists but cannot be matched to the declared bytes/receipt is not ours
    to delete; an unavailable probe is equally uncertain and remains retryable.
    """
    try:
        info = backend.object_info(lease.destination_key or cover.storage_key)
    except Exception:
        return False
    if info is not None:
        return False
    # A missing destination means a new-cover row is definitely broken.  Any
    # ledger proof for the same key is stale at this point and must not survive
    # as an ownership claim for a later object.
    for proof in session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.key == (lease.destination_key or cover.storage_key)
        )
    ).all():
        session.delete(proof)
    session.delete(lease)
    session.delete(cover)
    session.flush()
    return True


def expire_pending(
    session: Session,
    backend: StorageBackend,
    *,
    lease: StagingLease,
) -> bool:
    """Reconcile one expired cover lease and remove a broken cover safely.

    Returns ``True`` only when the lease/cover were terminalized.  A published
    exact object is recovered into the ownership ledger; mismatched or
    uncertain storage leaves both rows intact for a later retry.
    """
    if lease.model_source_cover_id is None:
        return False
    cover = session.get(ModelSourceCover, lease.model_source_cover_id)
    if cover is None:
        if lease.destination_key:
            for proof in session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == lease.destination_key
                )
            ).all():
                session.delete(proof)
        session.delete(lease)
        session.flush()
        return True
    try:
        recovered = _recover_pending_cover(session, backend, cover=cover, lease=lease)
    except Exception:
        # Backend uncertainty is a retryable state, never an expiry delete.
        return False
    if recovered is not None:
        lease.destination_key = recovered.key
        lease.receipt_json = _receipt_json(recovered)
        cover.size_bytes = lease.size_bytes
        cover.updated_at = utcnow()
        record_creation(session, recovered, object_kind="model_source_cover")
        session.delete(lease)
        session.flush()
        return True
    return _discard_cover_if_absent(session, backend, cover=cover, lease=lease)


def reconcile_pending(session: Session, backend: StorageBackend) -> int:
    """Recover all cover leases left by a publication/DB crash."""
    recovered = 0
    leases = session.exec(
        select(StagingLease).where(StagingLease.model_source_cover_id != None)  # noqa: E711
    ).all()
    for lease in leases:
        if lease.model_source_cover_id is None:
            continue
        cover = session.get(ModelSourceCover, lease.model_source_cover_id)
        if cover is None:
            if lease.destination_key:
                for proof in session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.key == lease.destination_key
                    )
                ).all():
                    session.delete(proof)
            session.delete(lease)
            continue
        try:
            receipt = _recover_pending_cover(session, backend, cover=cover, lease=lease)
        except Exception:
            # Keep the intent until a backend can prove either publication or
            # absence. One unavailable object probe must not block recovery of
            # unrelated cover intents.
            continue
        if receipt is None:
            # Startup reconciliation also cleans up a pre-publication crash.
            # Never infer absence from a failed read: only an explicit empty
            # object_info result is destructive.  An unexpired intent with no
            # object cannot be published by this process anymore, so it is
            # safe to discard immediately and let the Inbox retry recreate it.
            if _discard_cover_if_absent(session, backend, cover=cover, lease=lease):
                recovered += 1
            continue
        lease.destination_key = receipt.key
        lease.receipt_json = _receipt_json(receipt)
        # Replacement metadata may have rolled back with the crashed process;
        # the durable pending lease is the source of truth for the intended
        # bytes until the next request can complete the write.
        cover.size_bytes = lease.size_bytes
        cover.updated_at = utcnow()
        session.add(cover)
        record_creation(session, receipt, object_kind="model_source_cover")
        # A cover row is already durable for new covers; replacement metadata
        # is updated by the original transaction when it can be resumed.
        session.delete(lease)
        recovered += 1
    session.flush()
    return recovered


def get(session: Session, provenance_source_id: int) -> ModelSourceCover | None:
    return session.exec(
        select(ModelSourceCover).where(
            ModelSourceCover.provenance_source_id == provenance_source_id
        )
    ).first()


def put(
    session: Session,
    backend: StorageBackend,
    *,
    provenance_source_id: int,
    actor_id: int | None,
    data: bytes,
    content_type: str | None,
) -> SourceCoverWrite:
    """Publish normalized bytes with a transaction-bound ownership proof."""
    processed = process_source_cover_upload(data, content_type)
    existing = get(session, provenance_source_id)
    if existing is not None:
        # Keep old bytes until commit succeeds: a failed database commit can
        # then restore both the object and a current ownership receipt.
        old_bytes = backend.read_bytes(existing.storage_key)
        lease = _durable_pending_cover_intent(
            session,
            backend,
            cover=existing,
            actor_id=actor_id,
            destination_key=existing.storage_key,
            size_bytes=len(processed.data),
            sha256=hashlib.sha256(processed.data).hexdigest(),
        )
        recovered = _recover_pending_cover(
            session, backend, cover=existing, lease=lease
        )
        if recovered is not None:
            # The replacement was already published by an earlier process.
            # Reconcile its proof and metadata without attempting a second
            # replace, then leave lease release to this transaction.
            existing.content_type = processed.content_type
            existing.size_bytes = len(processed.data)
            existing.updated_at = utcnow()
            lease.receipt_json = _receipt_json(recovered)
            record_creation(session, recovered, object_kind="model_source_cover")
            session.delete(lease)
            session.add(existing)
            return SourceCoverWrite(
                cover=existing,
                created=False,
                replacement_receipt=recovered,
                replaced_bytes=None,
            )
        try:
            replacement = replace_owned_bytes(
                session,
                backend,
                existing.storage_key,
                processed.data,
                object_kind="model_source_cover",
            )
        except Exception:
            # A replacement adapter may fail before publication, or after it
            # has become externally visible. Compare the exact bytes only
            # when the backend can answer; retain the pending lease whenever
            # publication status is uncertain so restart can reconcile it.
            try:
                published_bytes = backend.read_bytes(existing.storage_key)
            except Exception:
                raise
            if published_bytes == old_bytes:
                if existing.id is not None:
                    with _intent_session(session) as intent:
                        pending = _cover_lease(intent, existing.id)
                        if pending is not None:
                            intent.delete(pending)
                        intent.commit()
            raise
        existing.content_type = processed.content_type
        existing.size_bytes = len(processed.data)
        existing.updated_at = utcnow()
        session.add(existing)
        staging_leases.record_cover_receipt(session, lease=lease, receipt=replacement)
        # A replacement lease owns only this publication generation. Once the
        # receipt/proof and metadata are in the caller transaction, terminalize
        # that exact lease so the next replacement must create a fresh intent.
        staging_leases.release_cover_lease(
            session, model_source_cover_id=existing.id or 0
        )
        return SourceCoverWrite(
            cover=existing,
            created=False,
            replacement_receipt=replacement,
            replaced_bytes=old_bytes,
        )

    # Cover-owned leases bind the in-flight publication without interpreting
    # storage keys as local paths. Both rows are committed in the dedicated
    # intent transaction before the first byte is published.
    key = backend.source_cover_key(provenance_source_id)
    cover = _create_durable_cover(
        session,
        provenance_source_id=provenance_source_id,
        actor_id=actor_id,
        destination_key=key,
        size_bytes=len(processed.data),
        sha256=hashlib.sha256(processed.data).hexdigest(),
    )
    assert cover.id is not None
    lease = _cover_lease(session, cover.id)
    if lease is None:
        raise RuntimeError("cover_pending_intent_missing")
    receipt: CreationReceipt | None = None
    try:
        recovered = _recover_pending_cover(session, backend, cover=cover, lease=lease)
        if recovered is not None:
            receipt = recovered
        else:
            try:
                receipt = publish_bytes(
                    session,
                    backend,
                    key,
                    processed.data,
                    object_kind="model_source_cover",
                    sha256=lease.sha256,
                )
            except StorageCollisionError:
                # A create-only collision may be the object's own publication
                # after a crash, but only exact key/size/content adoption is
                # allowed to claim it.
                receipt = _recover_pending_cover(
                    session, backend, cover=cover, lease=lease
                )
                if receipt is None:
                    raise
        staging_leases.record_cover_receipt(session, lease=lease, receipt=receipt)
        record_creation(session, receipt, object_kind="model_source_cover")
        staging_leases.release_cover_lease(session, model_source_cover_id=cover.id)
    except Exception:
        if receipt is not None:
            # Roll back only an object positively matched by its receipt. If
            # that proof cannot be established, keep the durable intent for
            # restart reconciliation instead of risking another owner's bytes.
            removed = backend.rollback_create(receipt)
            if removed:
                _delete_durable_cover_intent(
                    session,
                    cover_id=cover.id or 0,
                    storage_key=key,
                )
        else:
            # A backend failure before a verifiable publication is safe to
            # discard only when the declared destination is absent. If the
            # backend cannot answer, retain the durable intent for recovery.
            try:
                info = backend.object_info(key)
                published = isinstance(info, StorageObjectInfo)
            except Exception:
                published = True
            if not published:
                _delete_durable_cover_intent(
                    session,
                    cover_id=cover.id or 0,
                    storage_key=key,
                    preserve_ownership_intent=True,
                )
        raise
    return SourceCoverWrite(cover=cover, created=True, creation_receipt=receipt)


def rollback_after_commit_failure(
    session: Session, backend: StorageBackend, result: SourceCoverWrite
) -> None:
    """Undo publish after a rolled-back caller transaction, proof-first."""
    if result.creation_receipt is not None:
        removed = backend.rollback_create(result.creation_receipt)
        if removed:
            cover = result.cover
            if cover.id is not None:
                _delete_durable_cover_intent(
                    session, cover_id=cover.id, storage_key=cover.storage_key
                )
        return
    if result.replacement_receipt is None or result.replaced_bytes is None:
        return
    if result.cover.id is None:
        return
    _finish_replacement_rollback(
        session,
        backend,
        cover_id=result.cover.id,
        replaced_bytes=result.replaced_bytes,
        replacement_receipt=result.replacement_receipt,
    )
