"""Private storage lifecycle for representative provenance-source covers."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Iterator

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    ModelProvenanceSource,
    ModelSourceCover,
    OwnedStorageObject,
    StagingLease,
    StorageObjectState,
)
from app.db.transactions import begin_write
from app.modules.ingestion import staging_leases
from app.modules.media.source_cover_processing import process_source_cover_upload
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StorageBackend,
    StorageCollisionError,
    StorageObjectInfo,
)
from app.modules.storage.storage_deletion import (
    PreparedOwnedDeletion,
    enqueue_prepared_owned_deletion,
    enqueue_prevalidated_receipt,
    prepare_owned_key_deletion,
)
from app.modules.storage.storage_ownership import (
    abandon_publication,
    abandon_verified_publication,
    adopt_publication,
    prepare_bytes,
    prepare_owned_replacement,
    provider_ref_for_backend,
    record_creation,
)
from app.modules.storage.storage_publication import (
    PendingPublication,
    PublicationReservation,
    VerifiedPublication,
    lock_publication_locator,
    same_creation,
)


@dataclass(frozen=True)
class SourceCoverWrite:
    cover: ModelSourceCover
    created: bool
    publication: PendingPublication | VerifiedPublication
    creation_receipt: CreationReceipt | None = None
    replacement_receipt: CreationReceipt | None = None
    replaced_bytes: bytes | None = None


@dataclass(frozen=True)
class SourceCoverCandidate:
    """Immutable bytes awaiting the caller's source and completion authority."""

    provenance_source_id: int
    actor_id: int | None
    content_type: str
    publication: PendingPublication
    previous: PreparedOwnedDeletion | None


class SourceCoverChangedError(RuntimeError):
    """The observed cover no longer belongs to the preparation generation."""


def prepare_candidate(
    session: Session,
    backend: StorageBackend,
    *,
    provenance_source_id: int,
    actor_id: int | None,
    data: bytes,
    content_type: str | None,
) -> SourceCoverCandidate:
    """Publish private bytes without inserting a cover or changing visible bytes.

    The shared publication reservation owns process-kill recovery. A candidate
    can become visible only through attach_candidate followed by adoption in
    the caller's fenced transaction. Storage probes happen entirely here.
    """
    processed = process_source_cover_upload(data, content_type)
    with session.no_autoflush:
        existing = get(session, provenance_source_id)
        previous = (
            prepare_owned_key_deletion(
                session,
                backend,
                existing.storage_key,
                resource_kind="model_source_cover",
                resource_id=existing.id,
                required_proof=True,
            )
            if existing is not None
            else None
        )
    canonical = backend.source_cover_key(provenance_source_id)
    stem, separator, extension = canonical.rpartition(".")
    key = (
        f"{stem}.{uuid.uuid4().hex}.{extension}"
        if separator
        else f"{canonical}.{uuid.uuid4().hex}"
    )
    publication = prepare_bytes(
        session,
        backend,
        key,
        processed.data,
        object_kind="model_source_cover",
        sha256=hashlib.sha256(processed.data).hexdigest(),
    )
    return SourceCoverCandidate(
        provenance_source_id,
        actor_id,
        processed.content_type,
        publication,
        previous,
    )


def attach_candidate(
    session: Session, candidate: SourceCoverCandidate
) -> ModelSourceCover:
    """Lock source then cover and switch its pointer; do not acquire anchors yet."""
    begin_write(session, immediate=True)
    source = session.exec(
        select(ModelProvenanceSource)
        .where(ModelProvenanceSource.id == candidate.provenance_source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if source is None:
        raise SourceCoverChangedError("source_cover_source_removed")
    cover = session.exec(
        select(ModelSourceCover)
        .where(ModelSourceCover.provenance_source_id == candidate.provenance_source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    expected_key = candidate.previous.receipt.key if candidate.previous else None
    if (cover.storage_key if cover else None) != expected_key:
        raise SourceCoverChangedError("source_cover_changed")
    receipt = candidate.publication.receipt
    if cover is None:
        cover = ModelSourceCover(
            provenance_source_id=candidate.provenance_source_id,
            storage_key=receipt.key,
            created_by=candidate.actor_id,
            size_bytes=receipt.size,
            content_type=candidate.content_type,
        )
    else:
        cover.storage_key = receipt.key
        cover.size_bytes = receipt.size
        cover.content_type = candidate.content_type
        cover.updated_at = utcnow()
    session.add(cover)
    if cover.id is not None:
        for lease in session.exec(
            select(StagingLease)
            .where(StagingLease.model_source_cover_id == cover.id)
            .with_for_update()
        ).all():
            session.delete(lease)
    session.flush()
    return cover


def adopt_candidate(session: Session, candidate: SourceCoverCandidate) -> None:
    """Attach and retire exact receipts after every caller domain lock/mutation."""
    operations = [(candidate.publication.receipt.key, candidate.publication)]
    if candidate.previous is not None:
        operations.append((candidate.previous.receipt.key, candidate.previous))
    for _key, operation in sorted(operations, key=lambda item: item[0]):
        if isinstance(operation, PreparedOwnedDeletion):
            enqueue_prepared_owned_deletion(session, operation, required_proof=True)
        else:
            adopt_publication(session, operation)


class _ReceiptBindingError(RuntimeError):
    """Persisted receipt cannot be safely used by the active backend."""


def _bind_receipt(backend: StorageBackend, receipt: CreationReceipt) -> CreationReceipt:
    """Attach the active destination identity to a newly returned receipt."""
    expected_ref = provider_ref_for_backend(backend, namespace=receipt.namespace)
    if receipt.provider_ref not in (None, expected_ref):
        raise _ReceiptBindingError("storage_receipt_provider_identity_mismatch")
    return replace(receipt, provider_ref=receipt.provider_ref or expected_ref)


def _backend_scope(backend: StorageBackend, key: str) -> tuple[str, str, str]:
    """Resolve a backend scope while retaining compatibility with test fakes."""
    try:
        name = str(backend.backend_name)
    except AttributeError:
        name = "unknown"
    try:
        namespace = str(backend.namespace_for(key))
    except AttributeError, NotImplementedError, TypeError, ValueError:
        try:
            namespace = str(backend.namespace)
        except AttributeError:
            namespace = "unknown"
    return name, namespace, provider_ref_for_backend(backend, namespace=namespace)


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
    backend: StorageBackend,
    cover_id: int,
    storage_key: str,
    preserve_ownership_intent: bool = False,
) -> None:
    """Remove a failed new-cover intent in its own committed transaction."""
    backend_name, namespace, provider_ref = _backend_scope(backend, storage_key)
    for instance in tuple(caller.identity_map.values()):
        if (
            isinstance(instance, OwnedStorageObject)
            and instance.key == storage_key
            and (
                backend_name == "unknown"
                or (
                    instance.backend == backend_name
                    and instance.namespace == namespace
                    and instance.provider_ref == provider_ref
                )
            )
        ):
            caller.expunge(instance)
    with _intent_session(caller) as intent:
        observed_cover = intent.get(ModelSourceCover, cover_id)
        if observed_cover is not None:
            intent.exec(
                select(ModelProvenanceSource)
                .where(ModelProvenanceSource.id == observed_cover.provenance_source_id)
                .with_for_update()
            ).all()
        cover = intent.exec(
            select(ModelSourceCover)
            .where(ModelSourceCover.id == cover_id)
            .with_for_update()
        ).first()
        if cover is not None and cover.storage_key != storage_key:
            return
        leases = intent.exec(
            select(StagingLease)
            .where(StagingLease.model_source_cover_id == cover_id)
            .with_for_update()
        ).all()
        lock_publication_locator(
            intent, backend=backend_name, namespace=namespace, key=storage_key
        )
        proofs = intent.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == backend_name,
                OwnedStorageObject.namespace == namespace,
                OwnedStorageObject.provider_ref == provider_ref,
                OwnedStorageObject.key == storage_key,
            )
        ).all()
        if any(proof.state is StorageObjectState.COMMITTED for proof in proofs):
            return
        if not preserve_ownership_intent:
            for proof in proofs:
                proof.state = StorageObjectState.RETIRING
                proof.next_recovery_at = None
                intent.add(proof)
                if proof.token is not None and proof.size_bytes is not None:
                    enqueue_prevalidated_receipt(
                        intent,
                        _owned_receipt(proof),
                        object_kind=proof.object_kind,
                        sha256=proof.sha256,
                    )
        for lease in leases:
            intent.delete(lease)
        intent.flush()
        if cover is not None:
            intent.delete(cover)
        intent.commit()


def _finish_replacement_rollback(
    caller: Session,
    backend: StorageBackend,
    *,
    cover_id: int,
    replaced_bytes: bytes,
    replacement_receipt: CreationReceipt,
) -> None:
    """Restore bytes, then revalidate their domain pointer before adoption."""
    with _intent_session(caller) as reader:
        observed = reader.get(ModelSourceCover, cover_id)
        source_id = observed.provenance_source_id if observed is not None else None
    try:
        restored = backend.replace_bytes(replaced_bytes, replacement_receipt)
    except Exception:
        # The durable lease remains available for restart reconciliation.
        return
    restored = _bind_receipt(backend, restored)
    with _intent_session(caller) as intent:
        begin_write(intent, immediate=True)
        if source_id is not None:
            intent.exec(
                select(ModelProvenanceSource)
                .where(ModelProvenanceSource.id == source_id)
                .with_for_update()
            ).all()
        cover = intent.exec(
            select(ModelSourceCover)
            .where(ModelSourceCover.id == cover_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if (
            cover is None
            or cover.provenance_source_id != source_id
            or cover.storage_key != restored.key
        ):
            # A deletion or a newer pointer wins over stale compensation.
            # Retain exact cleanup authority without recreating ownership.
            enqueue_prevalidated_receipt(
                intent,
                restored,
                object_kind="model_source_cover",
                sha256=hashlib.sha256(replaced_bytes).hexdigest(),
            )
            intent.commit()
            return
        for lease in intent.exec(
            select(StagingLease)
            .where(StagingLease.model_source_cover_id == cover_id)
            .with_for_update()
        ).all():
            intent.delete(lease)
        intent.flush()
        record_creation(
            intent,
            restored,
            object_kind="model_source_cover",
            provider_ref=provider_ref_for_backend(
                backend, namespace=restored.namespace
            ),
        )
        intent.commit()


def _receipt_json(
    receipt: CreationReceipt, backend: StorageBackend | None = None
) -> str:
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
            # Publication already bound this receipt to one configured
            # destination. Recomputing from the active backend would make a
            # provider switch look like a valid ownership transition.
            "provider_ref": receipt.provider_ref,
        },
        sort_keys=True,
    )


def _receipt_from_json(value: str | None) -> CreationReceipt | None:
    try:
        raw = json.loads(value or "")
        if not isinstance(raw, dict):
            return None
        return CreationReceipt(**raw)
    except TypeError, ValueError:
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
        provider_ref=row.provider_ref,
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
        recovered_publication: VerifiedPublication | None = None
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
                recovered_publication = VerifiedPublication(
                    recovered, "model_source_cover", lease.sha256
                )
                intent.delete(lease)
            else:
                current_owned = False
                for proof in intent.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.backend
                        == _backend_scope(backend, persisted.storage_key)[0],
                        OwnedStorageObject.namespace
                        == _backend_scope(backend, persisted.storage_key)[1],
                        OwnedStorageObject.provider_ref
                        == _backend_scope(backend, persisted.storage_key)[2],
                        OwnedStorageObject.key == persisted.storage_key,
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
        # Finish every lease/domain mutation before locator adoption.
        intent.flush()
        if recovered_publication is not None:
            adopt_publication(intent, recovered_publication)
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
            expected_ref = provider_ref_for_backend(
                backend, namespace=receipt.namespace
            )
            backend_name, _namespace, _provider_ref = _backend_scope(
                backend, receipt.namespace
            )
            if receipt.provider_ref is None and backend_name not in {
                "local",
                "unknown",
            }:
                raise _ReceiptBindingError("storage_receipt_provider_identity_missing")
            if receipt.provider_ref not in (None, expected_ref):
                raise _ReceiptBindingError("storage_receipt_provider_identity_mismatch")
            if backend.creation_matches(receipt):
                return replace(
                    receipt, provider_ref=receipt.provider_ref or expected_ref
                )
        except Exception:
            raise
    try:
        receipt = backend.adopt_existing(
            lease.destination_key or cover.storage_key,
            expected_size=lease.size_bytes,
            expected_sha256=lease.sha256,
        )
    except FileNotFoundError, OSError, RuntimeError, ValueError, NotImplementedError:
        return None
    if not isinstance(receipt, CreationReceipt):
        return None
    if (
        receipt.key != (lease.destination_key or cover.storage_key)
        or receipt.size != lease.size_bytes
    ):
        return None
    return _bind_receipt(backend, receipt)


@dataclass(frozen=True)
class _CoverRecoveryIdentity:
    cover_id: int
    source_id: int
    lease_id: str
    key: str
    backend: str
    namespace: str
    provider_ref: str
    receipt_json: str | None
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class _ObservedCoverProof:
    reservation: PublicationReservation
    state: StorageObjectState
    receipt: CreationReceipt | None


@dataclass(frozen=True)
class _AbsentCoverRecovery:
    identity: _CoverRecoveryIdentity
    proofs: tuple[_ObservedCoverProof, ...]


@dataclass(frozen=True)
class _PublishedCoverRecovery:
    identity: _CoverRecoveryIdentity
    publication: VerifiedPublication


_CoverRecovery = _AbsentCoverRecovery | _PublishedCoverRecovery


def _cover_recovery_identity(backend, cover, lease) -> _CoverRecoveryIdentity:
    if cover.id is None:
        raise RuntimeError("cover_pending_intent_missing")
    key = lease.destination_key or cover.storage_key
    name, namespace, provider_ref = _backend_scope(backend, key)
    return _CoverRecoveryIdentity(
        cover.id,
        cover.provenance_source_id,
        lease.id,
        key,
        name,
        namespace,
        provider_ref,
        lease.receipt_json,
        lease.size_bytes,
        lease.sha256,
    )


def _cover_proofs(session: Session, identity: _CoverRecoveryIdentity):
    return session.exec(
        select(OwnedStorageObject)
        .where(
            OwnedStorageObject.backend == identity.backend,
            OwnedStorageObject.namespace == identity.namespace,
            OwnedStorageObject.provider_ref == identity.provider_ref,
            OwnedStorageObject.key == identity.key,
            OwnedStorageObject.state != StorageObjectState.RETIRING,
        )
        .execution_options(populate_existing=True)
    ).all()


def _prepare_absent_cover(session, backend, *, cover, lease):
    identity = _cover_recovery_identity(backend, cover, lease)
    proofs = tuple(
        _ObservedCoverProof(
            PublicationReservation.of(proof),
            proof.state,
            _owned_receipt(proof)
            if proof.token is not None and proof.size_bytes is not None
            else None,
        )
        for proof in _cover_proofs(session, identity)
    )
    try:
        info = backend.object_info(identity.key)
    except Exception:
        return None
    return _AbsentCoverRecovery(identity, proofs) if info is None else None


def _apply_cover_recoveries(session: Session, recoveries: list[_CoverRecovery]) -> int:
    """All domain locks first, then all locator anchors, followed by SQL only."""
    if not recoveries:
        return 0
    begin_write(session, immediate=True)
    identities = [recovery.identity for recovery in recoveries]
    for source_id in sorted({identity.source_id for identity in identities}):
        session.exec(
            select(ModelProvenanceSource)
            .where(ModelProvenanceSource.id == source_id)
            .with_for_update()
        ).all()
    for cover_id in sorted({identity.cover_id for identity in identities}):
        session.exec(
            select(ModelSourceCover)
            .where(ModelSourceCover.id == cover_id)
            .with_for_update()
        ).all()
    for lease_id in sorted({identity.lease_id for identity in identities}):
        session.exec(
            select(StagingLease).where(StagingLease.id == lease_id).with_for_update()
        ).all()
    for identity in sorted(
        identities, key=lambda item: (item.backend, item.namespace, item.key)
    ):
        lock_publication_locator(
            session,
            backend=identity.backend,
            namespace=identity.namespace,
            key=identity.key,
        )
    recovered = 0
    for recovery in recoveries:
        identity = recovery.identity
        cover = session.get(ModelSourceCover, identity.cover_id, populate_existing=True)
        lease = session.get(StagingLease, identity.lease_id, populate_existing=True)
        if (
            cover is None
            or lease is None
            or cover.storage_key != identity.key
            or lease.model_source_cover_id != cover.id
            or lease.receipt_json != identity.receipt_json
            or lease.size_bytes != identity.size_bytes
            or lease.sha256 != identity.sha256
        ):
            continue
        if isinstance(recovery, _AbsentCoverRecovery):
            current = _cover_proofs(session, identity)
            observed = {proof.reservation: proof for proof in recovery.proofs}
            if len(current) != len(observed):
                continue
            unchanged = True
            for proof in current:
                previous = observed.get(PublicationReservation.of(proof))
                receipt = (
                    _owned_receipt(proof)
                    if proof.token is not None and proof.size_bytes is not None
                    else None
                )
                if (
                    previous is None
                    or previous.state is not proof.state
                    or (
                        (previous.receipt is None) != (receipt is None)
                        or (
                            receipt is not None
                            and (
                                previous.receipt is None
                                or not same_creation(previous.receipt, receipt)
                            )
                        )
                    )
                ):
                    unchanged = False
                    break
            if not unchanged:
                continue
            for proof in current:
                proof.state = StorageObjectState.RETIRING
                proof.next_recovery_at = None
                session.add(proof)
                if proof.token is not None and proof.size_bytes is not None:
                    enqueue_prevalidated_receipt(
                        session,
                        _owned_receipt(proof),
                        object_kind=proof.object_kind,
                        sha256=proof.sha256,
                    )
            session.delete(lease)
            session.flush()
            session.delete(cover)
        else:
            cover.size_bytes = identity.size_bytes
            cover.updated_at = utcnow()
            session.add(cover)
            session.delete(lease)
            session.flush()
            adopt_publication(session, recovery.publication)
        recovered += 1
    session.flush()
    return recovered


def _discard_cover_if_absent(session, backend, *, cover, lease) -> bool:
    prepared = _prepare_absent_cover(session, backend, cover=cover, lease=lease)
    return bool(prepared is not None and _apply_cover_recoveries(session, [prepared]))


def expire_pending_many(
    session: Session, backend: StorageBackend, *, leases: list[StagingLease]
) -> int:
    """Probe the complete batch before locking domain rows or storage anchors."""
    recoveries: list[_CoverRecovery] = []
    with session.no_autoflush:
        for lease in leases:
            if lease.model_source_cover_id is None:
                continue
            cover = session.get(ModelSourceCover, lease.model_source_cover_id)
            if cover is None:
                continue
            identity = _cover_recovery_identity(backend, cover, lease)
            try:
                receipt = _recover_pending_cover(
                    session, backend, cover=cover, lease=lease
                )
            except Exception:
                continue
            if receipt is None:
                prepared = _prepare_absent_cover(
                    session, backend, cover=cover, lease=lease
                )
                if prepared is not None:
                    recoveries.append(prepared)
            else:
                recoveries.append(
                    _PublishedCoverRecovery(
                        identity,
                        VerifiedPublication(
                            receipt, "model_source_cover", lease.sha256
                        ),
                    )
                )
    return _apply_cover_recoveries(session, recoveries)


def expire_pending(
    session: Session, backend: StorageBackend, *, lease: StagingLease
) -> bool:
    return bool(expire_pending_many(session, backend, leases=[lease]))


def reconcile_pending(session: Session, backend: StorageBackend) -> int:
    """Recover legacy cover leases; immutable candidates remain private orphans."""
    leases = list(
        session.exec(
            select(StagingLease).where(StagingLease.model_source_cover_id.is_not(None))
        ).all()
    )
    return expire_pending_many(session, backend, leases=leases)


def get(session: Session, provenance_source_id: int) -> ModelSourceCover | None:
    return session.exec(
        select(ModelSourceCover).where(
            ModelSourceCover.provenance_source_id == provenance_source_id
        )
    ).first()


def delete(
    session: Session, backend: StorageBackend, provenance_source_id: int
) -> bool:
    """Prepare storage evidence, then delete under source/cover authority locks."""
    observed = get(session, provenance_source_id)
    if observed is None:
        return False
    prepared = prepare_owned_key_deletion(
        session,
        backend,
        observed.storage_key,
        required_proof=True,
        resource_kind="model_source_cover",
        resource_id=observed.id,
    )
    assert prepared is not None
    begin_write(session, immediate=True)
    session.exec(
        select(ModelProvenanceSource)
        .where(ModelProvenanceSource.id == provenance_source_id)
        .with_for_update()
    ).all()
    cover = session.exec(
        select(ModelSourceCover)
        .where(ModelSourceCover.provenance_source_id == provenance_source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if (
        cover is None
        or cover.id != prepared.resource_id
        or cover.storage_key != prepared.receipt.key
    ):
        raise SourceCoverChangedError("source_cover_changed")
    session.delete(cover)
    session.flush()
    enqueue_prepared_owned_deletion(session, prepared, required_proof=True)
    return True


def prepare_put(
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
            lease.receipt_json = _receipt_json(recovered, backend)
            session.delete(lease)
            session.add(existing)
            return SourceCoverWrite(
                cover=existing,
                created=False,
                replacement_receipt=recovered,
                replaced_bytes=None,
                publication=VerifiedPublication(
                    recovered, "model_source_cover", lease.sha256
                ),
            )
        try:
            publication = prepare_owned_replacement(
                session,
                backend,
                existing.storage_key,
                processed.data,
                object_kind="model_source_cover",
            )
            replacement = _bind_receipt(backend, publication.receipt)
        except _ReceiptBindingError:
            raise
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
            publication=publication,
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
    publication: PendingPublication | VerifiedPublication
    try:
        recovered = _recover_pending_cover(session, backend, cover=cover, lease=lease)
        if recovered is not None:
            receipt = recovered
            publication = VerifiedPublication(
                receipt, "model_source_cover", lease.sha256
            )
        else:
            try:
                publication = prepare_bytes(
                    session,
                    backend,
                    key,
                    processed.data,
                    object_kind="model_source_cover",
                    sha256=lease.sha256,
                )
                receipt = publication.receipt
            except StorageCollisionError:
                # A create-only collision may be the object's own publication
                # after a crash, but only exact key/size/content adoption is
                # allowed to claim it.
                receipt = _recover_pending_cover(
                    session, backend, cover=cover, lease=lease
                )
                if receipt is None:
                    raise
                publication = VerifiedPublication(
                    receipt, "model_source_cover", lease.sha256
                )
        staging_leases.record_cover_receipt(session, lease=lease, receipt=receipt)
        staging_leases.release_cover_lease(session, model_source_cover_id=cover.id)
    except _ReceiptBindingError:
        # A mismatched or legacy remote receipt is not evidence of absence.
        # Keep the durable intent without even probing the active provider.
        raise
    except Exception:
        if receipt is not None:
            # Roll back only an object positively matched by its receipt. If
            # that proof cannot be established, keep the durable intent for
            # restart reconciliation instead of risking another owner's bytes.
            retired = (
                abandon_publication(session, publication)
                if isinstance(publication, PendingPublication)
                else abandon_verified_publication(session, publication)
            )
            removed = retired and backend.rollback_create(receipt)
            if removed:
                _delete_durable_cover_intent(
                    session,
                    backend=backend,
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
                    backend=backend,
                    cover_id=cover.id or 0,
                    storage_key=key,
                    preserve_ownership_intent=True,
                )
        raise
    return SourceCoverWrite(
        cover=cover, created=True, creation_receipt=receipt, publication=publication
    )


def adopt_write(session: Session, result: SourceCoverWrite) -> None:
    """Attach already-prepared cover evidence after all caller domain mutations."""
    adopt_publication(session, result.publication)


def put(
    session: Session,
    backend: StorageBackend,
    *,
    provenance_source_id: int,
    actor_id: int | None,
    data: bytes,
    content_type: str | None,
) -> SourceCoverWrite:
    result = prepare_put(
        session,
        backend,
        provenance_source_id=provenance_source_id,
        actor_id=actor_id,
        data=data,
        content_type=content_type,
    )
    session.flush()
    adopt_write(session, result)
    return result


def rollback_after_commit_failure(
    session: Session, backend: StorageBackend, result: SourceCoverWrite
) -> None:
    """Undo publish after a rolled-back caller transaction, proof-first."""
    if result.creation_receipt is not None:
        if isinstance(result.publication, PendingPublication):
            retired = abandon_publication(session, result.publication)
        else:
            retired = abandon_verified_publication(session, result.publication)
        if not retired:
            return
        removed = backend.rollback_create(result.creation_receipt)
        if removed:
            cover = result.cover
            if cover.id is not None:
                _delete_durable_cover_intent(
                    session,
                    backend=backend,
                    cover_id=cover.id,
                    storage_key=cover.storage_key,
                )
        return
    if result.replacement_receipt is None or result.replaced_bytes is None:
        return
    if result.cover.id is None:
        return
    if not isinstance(result.publication, VerifiedPublication):
        raise RuntimeError("source_cover_replacement_requires_verified_receipt")
    if not abandon_verified_publication(session, result.publication):
        return
    _finish_replacement_rollback(
        session,
        backend,
        cover_id=result.cover.id,
        replaced_bytes=result.replaced_bytes,
        replacement_receipt=result.replacement_receipt,
    )
