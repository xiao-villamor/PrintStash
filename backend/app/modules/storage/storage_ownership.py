"""Persist and consume exact, operation-proven storage ownership."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import OwnedStorageObject, StorageDeleteIntent, StorageObjectState
from app.db.publication import require_clean_publication_transaction
from app.modules.storage.remote_io import RemoteIO
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StagedRemoteObject,
    StorageBackend,
    StorageCollisionError,
    StorageTier,
)
from app.modules.storage.storage_publication import (
    PendingPublication,
    PublicationReservation,
    VerifiedPublication,
    lock_publication_locator,
    same_creation,
)
from app.modules.storage.storage_receipts import (
    UnsafeStorageDeleteError,
    owned_receipt,
    provider_ref_for_backend,
)

__all__ = ["UnsafeStorageDeleteError", "provider_ref_for_backend"]

if TYPE_CHECKING:
    from app.modules.storage.storage_deletion import PreparedOwnedDeletion


logger = get_logger(__name__)


def _release_staged_copy(source: Path, key: str) -> None:
    """Drop the local copy once the store published its own, as a move would."""
    try:
        source.unlink(missing_ok=True)
    except OSError:
        # The object is published; a leftover staging file is the
        # data-preserving failure, reclaimed with its staging lease.
        logger.warning(
            "server-side publication left the staged local copy",
            extra={"source": str(source), "destination": key},
        )


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


@dataclass(frozen=True)
class OrphanSweepResult:
    examined: int = 0
    cleared: int = 0
    reclaimed: int = 0
    blocked: int = 0
    pending: int = 0
    deferred: int = 0


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


def reserve_creation(
    session: Session,
    backend: StorageBackend | RemoteIO,
    key: str,
    *,
    object_kind: str,
    expected_size: int | None = None,
    sha256: str | None = None,
    provider_ref: str | None = None,
) -> PublicationReservation:
    """Reserve a fresh generation; storage observations precede SQL exclusion."""
    backend_name = _backend_name(backend)
    namespace = _namespace_for(backend, key)
    effective_ref = provider_ref or provider_ref_for_backend(
        backend, namespace=namespace
    )
    with _publication_session(session) as (reader, _independent):
        observed = _locator_rows(
            reader,
            backend,
            key,
            states=(
                StorageObjectState.PENDING,
                StorageObjectState.COMMITTED,
                StorageObjectState.BLOCKED,
            ),
        )
        prior = PublicationReservation.of(observed[0]) if observed else None
        prior_state = observed[0].state if observed else None
        reader.rollback()
    absent = prior_state is StorageObjectState.COMMITTED and not backend.exists(key)
    with _publication_session(session) as (writer, independent):
        lock_publication_locator(
            writer, backend=backend_name, namespace=namespace, key=key
        )
        rows = _locator_rows(
            writer,
            backend,
            key,
            states=(
                StorageObjectState.PENDING,
                StorageObjectState.COMMITTED,
                StorageObjectState.BLOCKED,
            ),
        )
        existing = next(iter(rows), None)
        if existing is not None:
            if (
                not absent
                or prior is None
                or PublicationReservation.of(existing) != prior
                or existing.state is not StorageObjectState.COMMITTED
                or existing.provider_ref not in (None, effective_ref)
            ):
                raise StorageCollisionError(key)
            existing.state = StorageObjectState.RETIRING
            existing.next_recovery_at = None
            writer.add(existing)
            writer.flush()
        reservation = reserve_publication(
            writer,
            backend=backend_name,
            namespace=namespace,
            key=key,
            provider_ref=effective_ref,
            object_kind=object_kind,
            expected_size=expected_size,
            sha256=sha256,
        )
        _commit_if_independent(writer, independent)
        return reservation


def reserve_publication(
    session: Session,
    *,
    backend: str,
    namespace: str,
    key: str,
    provider_ref: str,
    object_kind: str,
    expected_size: int | None = None,
    sha256: str | None = None,
    token: str | None = None,
    previous: PublicationReservation | None = None,
) -> PublicationReservation:
    """SQL-only fresh reservation after a caller has proven its target absent.

    A retry captures its previous immutable handle before external absence
    checks. Under the shared locator anchor it may retire only that exact
    generation; it never resets an old row or lends its authority to a late
    creator. A first publication supplies no previous handle and requires no
    current active claim. The caller commits before starting storage I/O.
    """
    if previous is not None and (
        previous.backend != backend
        or previous.namespace != namespace
        or previous.key != key
        or previous.provider_ref not in (None, provider_ref)
    ):
        raise UnsafeStorageDeleteError("storage_locator_mismatch")
    lock_publication_locator(session, backend=backend, namespace=namespace, key=key)
    rows = session.exec(
        select(OwnedStorageObject)
        .where(
            OwnedStorageObject.backend == backend,
            OwnedStorageObject.namespace == namespace,
            OwnedStorageObject.key == key,
            (OwnedStorageObject.provider_ref == provider_ref)
            | OwnedStorageObject.provider_ref.is_(None),
            OwnedStorageObject.state != StorageObjectState.RETIRING,
        )
        .execution_options(populate_existing=True)
    ).all()
    if rows:
        if (
            len(rows) != 1
            or previous is None
            or PublicationReservation.of(rows[0]) != previous
        ):
            raise StorageCollisionError(key)
        old = rows[0]
        old.state = StorageObjectState.RETIRING
        old.next_recovery_at = None
        session.add(old)
        session.flush()
    elif previous is not None:
        # A retired previous generation is still valid historical evidence of
        # this retry's observation, but an absent/reused id is not.
        old = _reservation_row(session, previous)
        if old is None or old.state is not StorageObjectState.RETIRING:
            raise StorageCollisionError(key)
    row = OwnedStorageObject(
        backend=backend,
        namespace=namespace,
        key=key,
        object_kind=object_kind,
        state=StorageObjectState.PENDING,
        size_bytes=expected_size,
        sha256=sha256,
        provider_ref=provider_ref,
        token=token,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        raise StorageCollisionError(key) from exc
    return PublicationReservation.of(row)


def _reservation_row(
    session: Session, reservation: PublicationReservation
) -> OwnedStorageObject | None:
    row = session.get(OwnedStorageObject, reservation.id, populate_existing=True)
    return (
        row
        if row is not None and PublicationReservation.of(row) == reservation
        else None
    )


def fail_publication(
    session: Session, reservation: PublicationReservation, exc: Exception
) -> None:
    with _publication_session(session) as (writer, independent):
        lock_publication_locator(
            writer,
            backend=reservation.backend,
            namespace=reservation.namespace,
            key=reservation.key,
        )
        row = _reservation_row(writer, reservation)
        if row is None or row.state is not StorageObjectState.PENDING:
            return
        row.last_error = exc.__class__.__name__[:255]
        writer.add(row)
        _commit_if_independent(writer, independent)


def settle_observed_publication(
    session: Session,
    reservation: PublicationReservation,
    *,
    observed_state: StorageObjectState,
    state: StorageObjectState,
    last_error: str | None,
) -> bool:
    """SQL-only reconciliation of the generation/state observed before I/O.

    A stale success or error cannot revive a retirement or overwrite a newer
    adopter. Reconciliation callers commit this terminal SQL phase before
    probing their next object.
    """
    if state not in (
        StorageObjectState.PENDING,
        StorageObjectState.BLOCKED,
        StorageObjectState.RETIRING,
    ):
        raise ValueError("reconciliation_cannot_adopt_publication")
    with session.no_autoflush:
        lock_publication_locator(
            session,
            backend=reservation.backend,
            namespace=reservation.namespace,
            key=reservation.key,
        )
        row = _reservation_row(session, reservation)
        if row is None or row.state is not observed_state:
            return False
        row.state = state
        row.last_error = last_error
        row.next_recovery_at = None
        session.add(row)
        session.flush()
        if (
            state is StorageObjectState.RETIRING
            and row.token is not None
            and row.size_bytes is not None
        ):
            _queue_retired_receipt(
                session, _receipt(row), object_kind=row.object_kind, sha256=row.sha256
            )
        return True


def _write_receipt(
    row: OwnedStorageObject, receipt: CreationReceipt, *, sha256: str | None
) -> None:
    row.token, row.size_bytes = receipt.token, receipt.size
    row.sha256 = sha256 if sha256 is not None else row.sha256
    row.etag, row.version_id = receipt.etag, receipt.version_id
    row.device, row.inode, row.ctime_ns = (
        receipt.device,
        receipt.inode,
        receipt.ctime_ns,
    )
    row.last_error = None


def _live_creation_owner(
    session: Session, receipt: CreationReceipt
) -> OwnedStorageObject | None:
    rows = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.backend == receipt.backend,
            OwnedStorageObject.namespace == receipt.namespace,
            OwnedStorageObject.key == receipt.key,
            (
                (OwnedStorageObject.provider_ref == receipt.provider_ref)
                | OwnedStorageObject.provider_ref.is_(None)
            )
            if receipt.backend == "local"
            else OwnedStorageObject.provider_ref == receipt.provider_ref,
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).all()
    return next((row for row in rows if same_creation(_receipt(row), receipt)), None)


def _queue_retired_receipt(
    session: Session, receipt: CreationReceipt, *, object_kind: str, sha256: str | None
) -> None:
    if _live_creation_owner(session, receipt) is not None:
        return
    from app.modules.storage.storage_deletion import enqueue_prevalidated_receipt

    enqueue_prevalidated_receipt(
        session, receipt, object_kind=object_kind, sha256=sha256
    )


def prepare_receipt(
    session: Session,
    reservation: PublicationReservation,
    receipt: CreationReceipt,
    *,
    object_kind: str,
    sha256: str | None,
    provider_ref: str | None = None,
) -> PendingPublication:
    if (reservation.backend, reservation.namespace, reservation.key) != (
        receipt.backend,
        receipt.namespace,
        receipt.key,
    ):
        raise StorageCollisionError("storage_locator_mismatch")
    effective_ref = provider_ref if provider_ref is not None else receipt.provider_ref
    if effective_ref is None:
        effective_ref = reservation.provider_ref
    if reservation.provider_ref not in (None, effective_ref):
        raise StorageCollisionError("storage_provider_mismatch")
    receipt = replace(receipt, provider_ref=effective_ref)
    retired = False
    with _publication_session(session) as (writer, independent):
        lock_publication_locator(
            writer,
            backend=reservation.backend,
            namespace=reservation.namespace,
            key=reservation.key,
        )
        row = _reservation_row(writer, reservation)
        if row is None or row.state is StorageObjectState.RETIRING:
            if row is not None:
                _write_receipt(row, receipt, sha256=sha256)
                row.next_recovery_at = None
                writer.add(row)
            _queue_retired_receipt(
                writer, receipt, object_kind=object_kind, sha256=sha256
            )
            retired = True
        elif row.state not in (
            StorageObjectState.PENDING,
            StorageObjectState.COMMITTED,
        ):
            raise RuntimeError("storage_reservation_retired")
        elif row.token is not None:
            physical_identity = (
                row.device is not None
                and row.inode is not None
                and row.ctime_ns is not None
                if row.backend == "local"
                else row.etag is not None or row.version_id is not None
            )
            if (physical_identity and not same_creation(_receipt(row), receipt)) or (
                # Historical empty tokens carry no creation authority. A
                # captured reservation generation still fences this verified
                # completion, just as for a missing operation receipt.
                not physical_identity and bool(row.token) and row.token != receipt.token
            ):
                raise StorageCollisionError("storage_reservation_receipt_mismatch")
            _write_receipt(row, receipt, sha256=sha256)
            row.provider_ref = effective_ref
            writer.add(row)
            reservation = PublicationReservation.of(row)
        else:
            _write_receipt(row, receipt, sha256=sha256)
            row.provider_ref = effective_ref
            writer.add(row)
            reservation = PublicationReservation.of(row)
        _commit_if_independent(writer, independent)
    if retired:
        raise RuntimeError("storage_reservation_retired")
    return PendingPublication(reservation, receipt, object_kind, sha256)


def abandon_publication(session: Session, candidate: PendingPublication) -> bool:
    """Durably retire an unadopted candidate after caller rollback, SQL only.

    A concurrent owner may already have committed the same exact physical
    generation. In that case its adoption wins and this cleanup queues nothing.
    Otherwise retirement and exact receipt revocation commit before any worker
    performs storage I/O. The caller must finish its transaction first.
    """
    with _publication_session(session) as (writer, independent):
        reservation = candidate.reservation
        lock_publication_locator(
            writer,
            backend=reservation.backend,
            namespace=reservation.namespace,
            key=reservation.key,
        )
        if _live_creation_owner(writer, candidate.receipt) is not None:
            return False
        row = _reservation_row(writer, reservation)
        if row is not None:
            if row.state not in (
                StorageObjectState.PENDING,
                StorageObjectState.RETIRING,
            ):
                return False
            row.state = StorageObjectState.RETIRING
            _write_receipt(row, candidate.receipt, sha256=candidate.sha256)
            writer.add(row)
        _queue_retired_receipt(
            writer,
            candidate.receipt,
            object_kind=candidate.object_kind,
            sha256=candidate.sha256,
        )
        _commit_if_independent(writer, independent)
        return True


def abandon_verified_publication(
    session: Session, candidate: VerifiedPublication
) -> bool:
    """Revoke an unadopted legacy replacement before any restoration I/O.

    A committed physical owner wins. Otherwise the durable exact outbox prevents
    late adoption of this receipt while the intentional replacement API restores
    its preceding bytes. An independently restored generation gets its own proof.
    """
    with _publication_session(session) as (writer, independent):
        receipt = candidate.receipt
        lock_publication_locator(
            writer,
            backend=receipt.backend,
            namespace=receipt.namespace,
            key=receipt.key,
        )
        if _live_creation_owner(writer, receipt) is not None:
            return False
        _queue_retired_receipt(
            writer, receipt, object_kind=candidate.object_kind, sha256=candidate.sha256
        )
        _commit_if_independent(writer, independent)
        return True


def complete_publication(
    session: Session,
    reservation: PublicationReservation,
    receipt: CreationReceipt,
    *,
    object_kind: str,
    sha256: str | None,
    provider_ref: str | None = None,
) -> None:
    """Compatibility join; guarded owners prepare then adopt after domain locks."""
    candidate = prepare_receipt(
        session,
        reservation,
        receipt,
        object_kind=object_kind,
        sha256=sha256,
        provider_ref=provider_ref,
    )
    adopt_publication(session, candidate)


def adopt_publication(
    session: Session, candidate: PendingPublication | VerifiedPublication
) -> OwnedStorageObject:
    """SQL-only adoption after domain authority/output locks; no backend I/O."""
    with session.no_autoflush:
        return record_creation(
            session,
            candidate.receipt,
            object_kind=candidate.object_kind,
            sha256=candidate.sha256,
            reservation=(
                candidate.reservation
                if isinstance(candidate, PendingPublication)
                else None
            ),
            provider_ref=candidate.receipt.provider_ref,
        )


def finish_publication_batch(
    session: Session,
    *,
    publications: Sequence[PendingPublication | VerifiedPublication],
    retirements: Sequence["PreparedOwnedDeletion"],
) -> None:
    """Attach prepared receipts after all domain work, in one locator order."""
    from app.modules.storage.storage_deletion import enqueue_prepared_owned_deletion

    receipts = [publication.receipt for publication in publications]
    receipts.extend(retirement.receipt for retirement in retirements)
    for receipt in sorted(
        receipts, key=lambda item: (item.backend, item.namespace, item.key)
    ):
        lock_publication_locator(
            session,
            backend=receipt.backend,
            namespace=receipt.namespace,
            key=receipt.key,
        )
    for publication in publications:
        adopt_publication(session, publication)
    for retirement in retirements:
        enqueue_prepared_owned_deletion(session, retirement, required_proof=True)


def prepare_bytes(
    session: Session,
    backend: StorageBackend,
    key: str,
    data: bytes,
    *,
    object_kind: str,
    sha256: str | None = None,
) -> PendingPublication:
    """Reserve/create bytes and keep durable PENDING evidence without adoption."""
    require_clean_publication_transaction(session)
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
    return prepare_receipt(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=digest,
        provider_ref=provider_ref,
    )


def prepare_stream(
    session: Session,
    backend: StorageBackend,
    key: str,
    source: BinaryIO,
    *,
    object_kind: str,
    expected_size: int | None = None,
    sha256: str | None = None,
) -> PendingPublication:
    """Publish a caller-owned stream without buffering it in memory."""
    require_clean_publication_transaction(session)
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
    return prepare_receipt(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=sha256,
        provider_ref=provider_ref,
    )


def prepare_file(
    session: Session,
    backend: StorageBackend,
    key: str,
    source: Path,
    *,
    object_kind: str,
    sha256: str | None = None,
    move: bool = False,
    provider_ref: str | None = None,
    remote_source: StagedRemoteObject | None = None,
) -> PendingPublication:
    """Publish a staged file with evidence known before storage mutation.

    *remote_source* is the same bytes already sitting in *backend*, such as a
    browser's direct upload. The backend copies it server-side when it can,
    so the bytes are not uploaded a second time; otherwise *source* is
    published as usual. Either way *source* is consumed when *move* is set.
    """
    require_clean_publication_transaction(session)
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
        receipt = (
            backend.copy_in(remote_source, key)
            if remote_source is not None and remote_source.size == size
            else None
        )
        if receipt is not None:
            if move:
                _release_staged_copy(source, key)
        elif move:
            receipt = backend.move_in(source, key)
        else:
            with source.open("rb") as handle:
                receipt = backend.create_stream(handle, key)
    except Exception as exc:
        fail_publication(session, reservation_id, exc)
        raise
    receipt = replace(receipt, provider_ref=effective_provider_ref)
    return prepare_receipt(
        session,
        reservation_id,
        receipt,
        object_kind=object_kind,
        sha256=digest,
        provider_ref=effective_provider_ref,
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
    """Compatibility publication whose remaining caller work must be SQL-only.

    Guarded domain owners use prepare_bytes, take their authority/output locks,
    then call adopt_publication as the final SQL phase.
    """
    candidate = prepare_bytes(
        session, backend, key, data, object_kind=object_kind, sha256=sha256
    )
    adopt_publication(session, candidate)
    return candidate.receipt


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
    candidate = prepare_stream(
        session,
        backend,
        key,
        source,
        object_kind=object_kind,
        expected_size=expected_size,
        sha256=sha256,
    )
    adopt_publication(session, candidate)
    return candidate.receipt


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
    remote_source: StagedRemoteObject | None = None,
) -> CreationReceipt:
    candidate = prepare_file(
        session,
        backend,
        key,
        source,
        object_kind=object_kind,
        sha256=sha256,
        move=move,
        provider_ref=provider_ref,
        remote_source=remote_source,
    )
    adopt_publication(session, candidate)
    return candidate.receipt


def _orphan_policy_error(
    row: OwnedStorageObject, backend: StorageBackend
) -> str | None:
    if row.backend != backend.backend_name:
        return "storage_backend_mismatch"
    expected = provider_ref_for_backend(backend, namespace=row.namespace)
    transport = str(getattr(backend, "transport", "")).lower()
    bound = backend.backend_name in {
        "s3",
        "backup-s3",
        "opendal",
        "webdav",
        "nextcloud",
        "sftp",
    } or transport in {"s3", "webdav", "sftp"}
    if row.provider_ref != expected and not (row.provider_ref is None and not bound):
        return (
            "storage_provider_identity_missing"
            if row.provider_ref is None
            else "storage_provider_mismatch"
        )
    if backend.capabilities.tier is not StorageTier.VERIFIED:
        return "storage_reclaim_unsupported"
    return None


def _retire_orphan(
    session: Session, handle: PublicationReservation, *, error: str | None = None
) -> OwnedStorageObject | None:
    """Claim the generation in independent SQL before any collector I/O."""
    lock_publication_locator(
        session, backend=handle.backend, namespace=handle.namespace, key=handle.key
    )
    row = _reservation_row(session, handle)
    if row is None or row.state not in (
        StorageObjectState.PENDING,
        StorageObjectState.RETIRING,
    ):
        return None
    if error is not None:
        row.state = StorageObjectState.BLOCKED
        row.last_error = error
    else:
        row.state = StorageObjectState.RETIRING
    session.add(row)
    session.flush()
    return row


def sweep_orphaned_publications(
    session: Session,
    backend: StorageBackend,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> OrphanSweepResult:
    """Retire a generation and persist exact deletion before touching its bytes.

    Receiptless retirement preserves recovery evidence. It permits a new
    reservation at the canonical locator, but can infer old ownership only
    while that locator has no newer active generation. Unknown evidence is
    explicitly deferred and retried fairly rather than discarded.
    """
    require_clean_publication_transaction(session)
    current = now or utcnow()
    cutoff = current - _ORPHAN_GRACE
    with _publication_session(session) as (reader, _):
        rows = reader.exec(
            select(OwnedStorageObject)
            .where(
                (
                    (OwnedStorageObject.state == StorageObjectState.PENDING)
                    & (OwnedStorageObject.created_at < cutoff)
                )
                | (
                    (OwnedStorageObject.state == StorageObjectState.RETIRING)
                    & OwnedStorageObject.token.is_(None)
                    & (
                        (OwnedStorageObject.next_recovery_at.is_(None))
                        | (OwnedStorageObject.next_recovery_at <= current)
                    )
                ),  # noqa: E711
            )
            .order_by(
                OwnedStorageObject.next_recovery_at.asc(), OwnedStorageObject.id.asc()
            )
            .limit(limit)
        ).all()
        snapshots = [(PublicationReservation.of(row), row) for row in rows]
        reader.expunge_all()
        reader.rollback()
    cleared = reclaimed = blocked = pending = deferred = 0
    queued = []
    for handle, observed in snapshots:
        if observed.object_kind in {
            "backup",
            "backup-legacy",
            "backup-cache",
            "backup-cloud-cache",
        }:
            pending += 1
            continue
        error = _orphan_policy_error(observed, backend)
        if error is not None:
            with _publication_session(session) as (writer, _):
                row = _retire_orphan(writer, handle, error=error)
                writer.commit()
            blocked += row is not None
            continue

        # A complete receipt needs no inferred ownership. The preflight is
        # outside SQL exclusion; a concurrent adopter is allowed to win here.
        if observed.token is not None:
            try:
                backend.object_info(observed.key)
            except Exception as exc:
                with _publication_session(session) as (writer, _):
                    lock_publication_locator(
                        writer,
                        backend=handle.backend,
                        namespace=handle.namespace,
                        key=handle.key,
                    )
                    row = _reservation_row(writer, handle)
                    if row is not None and row.state is StorageObjectState.PENDING:
                        row.last_error = type(exc).__name__[:255]
                        writer.add(row)
                    writer.commit()
                pending += 1
                continue
        with _publication_session(session) as (writer, _):
            row = _retire_orphan(writer, handle)
            if row is None:
                writer.commit()
                continue
            if row.token is not None:
                from app.modules.storage.storage_deletion import (
                    enqueue_prevalidated_receipt,
                )

                intent = enqueue_prevalidated_receipt(
                    writer,
                    replace(
                        _receipt(row),
                        provider_ref=row.provider_ref
                        or provider_ref_for_backend(backend, namespace=row.namespace),
                    ),
                    object_kind=row.object_kind,
                    sha256=row.sha256,
                )
                queued.append(intent.id)
                row.next_recovery_at = None
                writer.add(row)
                writer.commit()
                continue
            row.next_recovery_at = current + timedelta(hours=1)
            writer.add(row)
            writer.commit()

        # Retired incomplete creators can still finish later. Retain their
        # nonce even when the object is absent; a late receipt owns cleanup.
        evidence = None
        info = None
        try:
            info = backend.object_info(observed.key)
            if (
                info is not None
                and observed.size_bytes == info.size
                and observed.sha256 is not None
                and observed.object_kind in _SMALL_HASH_KINDS
                and info.size <= _SMALL_HASH_LIMIT
            ):
                evidence = backend.adopt_existing(
                    observed.key,
                    expected_size=info.size,
                    expected_sha256=observed.sha256,
                )
        except Exception as exc:
            logger.warning(
                "storage orphan recovery deferred",
                extra={"object_id": handle.id, "error": type(exc).__name__},
            )
        with _publication_session(session) as (writer, _):
            lock_publication_locator(
                writer,
                backend=handle.backend,
                namespace=handle.namespace,
                key=handle.key,
            )
            row = _reservation_row(writer, handle)
            if (
                row is None
                or row.state is not StorageObjectState.RETIRING
                or row.token is not None
            ):
                writer.commit()
                continue
            active = writer.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.backend == handle.backend,
                    OwnedStorageObject.namespace == handle.namespace,
                    OwnedStorageObject.key == handle.key,
                    OwnedStorageObject.state != StorageObjectState.RETIRING,
                )
            ).first()
            if evidence is not None and active is None:
                evidence = replace(
                    evidence,
                    provider_ref=handle.provider_ref
                    or provider_ref_for_backend(backend, namespace=handle.namespace),
                )
                _write_receipt(row, evidence, sha256=row.sha256)
                row.provider_ref = evidence.provider_ref
                row.next_recovery_at = None
                from app.modules.storage.storage_deletion import (
                    enqueue_prevalidated_receipt,
                )

                intent = enqueue_prevalidated_receipt(
                    writer, evidence, object_kind=row.object_kind, sha256=row.sha256
                )
                queued.append(intent.id)
            else:
                row.last_error = "storage_orphan_recovery_deferred"
                pending += 1
                deferred += 1
                if info is None:
                    cleared += 1
            writer.add(row)
            writer.commit()

    # Every intent and revocation is durable before the processor performs I/O.
    # Count only this sweep's intents, while preserving the public processor.
    if queued:
        from app.modules.storage.storage_deletion import process_storage_delete_intents

        process_storage_delete_intents(limit=max(limit, len(queued)), backend=backend)
        with _publication_session(session) as (reader, _):
            intents = reader.exec(
                select(StorageDeleteIntent).where(StorageDeleteIntent.id.in_(queued))
            ).all()
            reclaimed += sum(intent.status == "completed" for intent in intents)
            blocked += sum(intent.status == "blocked" for intent in intents)
            pending += sum(intent.status in {"pending", "retry"} for intent in intents)
            reader.rollback()
    session.expire_all()
    return OrphanSweepResult(
        examined=len(snapshots),
        cleared=cleared,
        reclaimed=reclaimed,
        blocked=blocked,
        pending=pending,
        deferred=deferred,
    )


def record_creation(
    session: Session,
    receipt: CreationReceipt,
    *,
    object_kind: str,
    sha256: str | None = None,
    reservation: PublicationReservation | None = None,
    provider_ref: str | None = None,
    upgrade_provider_ref: bool = False,
) -> OwnedStorageObject:
    """Join exact evidence to domain SQL after its authority locks, never I/O.

    Retired generations cannot be adopted again. Legacy creators may omit a
    reservation, but still serialize against retirement and exact outbox
    revocation. Replacements get new generations instead of rebinding old handles.
    """
    effective_ref = provider_ref if provider_ref is not None else receipt.provider_ref
    receipt = replace(receipt, provider_ref=effective_ref)
    lock_publication_locator(
        session, backend=receipt.backend, namespace=receipt.namespace, key=receipt.key
    )
    with session.no_autoflush:
        proofs = session.exec(
            select(OwnedStorageObject)
            .where(
                OwnedStorageObject.backend == receipt.backend,
                OwnedStorageObject.namespace == receipt.namespace,
                OwnedStorageObject.key == receipt.key,
            )
            .order_by(OwnedStorageObject.id)
            .execution_options(populate_existing=True)
        ).all()
        intents = session.exec(
            select(StorageDeleteIntent).where(
                StorageDeleteIntent.backend == receipt.backend,
                StorageDeleteIntent.namespace == receipt.namespace,
                StorageDeleteIntent.key == receipt.key,
                (
                    (StorageDeleteIntent.provider_ref == effective_ref)
                    | StorageDeleteIntent.provider_ref.is_(None)
                    | (effective_ref is None)
                )
                if receipt.backend == "local"
                else StorageDeleteIntent.provider_ref == effective_ref,
            )
        ).all()
        if any(same_creation(_intent_receipt(intent), receipt) for intent in intents):
            raise RuntimeError("storage_receipt_revoked")
        eligible = [
            row
            for row in proofs
            if row.provider_ref == effective_ref
            or (receipt.backend == "local" and effective_ref is None)
            or (upgrade_provider_ref and row.provider_ref is None)
        ]
        active = next(
            (row for row in eligible if row.state is not StorageObjectState.RETIRING),
            None,
        )
        if active is not None and effective_ref is None and receipt.backend == "local":
            effective_ref = active.provider_ref
            receipt = replace(receipt, provider_ref=effective_ref)
        if reservation is not None:
            active = next(
                (
                    row
                    for row in eligible
                    if PublicationReservation.of(row) == reservation
                ),
                None,
            )
            if active is None or active.state is StorageObjectState.RETIRING:
                raise RuntimeError("storage_reservation_retired")
            if active.token is None or not same_creation(_receipt(active), receipt):
                raise UnsafeStorageDeleteError("storage_reservation_receipt_mismatch")
        if (
            active is not None
            and active.state is StorageObjectState.COMMITTED
            and same_creation(_receipt(active), receipt)
        ):
            if upgrade_provider_ref:
                active.provider_ref = effective_ref
            active.object_kind = object_kind
            if sha256 is not None:
                active.sha256 = sha256
            active.last_error = None
            session.add(active)
            return active
        if any(
            row.token is not None and same_creation(_receipt(row), receipt)
            for row in eligible
            if row.state is StorageObjectState.RETIRING
        ):
            raise RuntimeError("storage_receipt_retired")
        if (
            active is not None
            and active.state is StorageObjectState.BLOCKED
            and upgrade_provider_ref
            and active.provider_ref is None
        ):
            # Explicit legacy reauthorization preserves its quarantined history.
            active.state = StorageObjectState.RETIRING
            active.next_recovery_at = None
            session.add(active)
            session.flush()
            active = None
        if active is not None:
            if active.state not in (
                StorageObjectState.PENDING,
                StorageObjectState.COMMITTED,
            ):
                raise RuntimeError("storage_reservation_retired")
            if active.state is StorageObjectState.PENDING:
                if active.token is not None and not same_creation(
                    _receipt(active), receipt
                ):
                    raise UnsafeStorageDeleteError(
                        "storage_reservation_receipt_mismatch"
                    )
                changed = session.execute(
                    update(OwnedStorageObject)
                    .where(
                        OwnedStorageObject.id == active.id,
                        OwnedStorageObject.publication_generation
                        == active.publication_generation,
                        OwnedStorageObject.state == StorageObjectState.PENDING,
                    )
                    .values(
                        state=StorageObjectState.COMMITTED,
                        token=receipt.token,
                        size_bytes=receipt.size,
                        sha256=sha256 if sha256 is not None else active.sha256,
                        etag=receipt.etag,
                        version_id=receipt.version_id,
                        device=receipt.device,
                        inode=receipt.inode,
                        ctime_ns=receipt.ctime_ns,
                        provider_ref=effective_ref,
                        object_kind=object_kind,
                        committed_at=utcnow(),
                        last_error=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    raise RuntimeError("storage_reservation_retired")
                session.refresh(active)
                return active
            active.state = StorageObjectState.RETIRING
            active.next_recovery_at = None
            session.add(active)
            session.flush()
        elif not eligible and proofs and not upgrade_provider_ref:
            raise UnsafeStorageDeleteError("storage_provider_identity_mismatch")
        row = OwnedStorageObject(
            backend=receipt.backend,
            namespace=receipt.namespace,
            key=receipt.key,
            object_kind=object_kind,
            state=StorageObjectState.COMMITTED,
            provider_ref=effective_ref,
            committed_at=utcnow(),
        )
        _write_receipt(row, receipt, sha256=sha256)
        session.add(row)
        session.flush()
        return row


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


def _receipt(row: OwnedStorageObject) -> CreationReceipt:
    return owned_receipt(row)


def matching_creation_receipt(
    session: Session, backend: StorageBackend, key: str
) -> CreationReceipt | None:
    """Return existing exact ownership proof without adopting or changing bytes."""
    for row in _locator_rows(
        session, backend, key, states=(StorageObjectState.COMMITTED,)
    ):
        receipt = _receipt(row)
        if backend.creation_matches(receipt):
            return receipt
    return None


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


def prepare_owned_replacement(
    session: Session,
    backend: StorageBackend,
    key: str,
    data: bytes,
    *,
    object_kind: str,
) -> VerifiedPublication:
    """Replace exact owned bytes before the caller's domain/anchor transaction."""
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
                replacement.provider_ref is not None
                and replacement.provider_ref != expected_ref
            )
        ):
            raise UnsafeStorageDeleteError("storage_locator_mismatch")
        return VerifiedPublication(
            receipt=replace(replacement, provider_ref=expected_ref),
            object_kind=object_kind,
            sha256=hashlib.sha256(data).hexdigest(),
        )
    raise UnsafeStorageDeleteError("storage_ownership_unverified")


def replace_owned_bytes(
    session: Session,
    backend: StorageBackend,
    key: str,
    data: bytes,
    *,
    object_kind: str,
) -> CreationReceipt:
    prepared = prepare_owned_replacement(
        session, backend, key, data, object_kind=object_kind
    )
    adopt_publication(session, prepared)
    return prepared.receipt


def delete_owned_key(
    session: Session,
    backend: StorageBackend,
    key: str,
    *,
    required_proof: bool = False,
) -> bool:
    """Immediate owner: durably retire exact evidence, commit, then reclaim.

    This compatibility operation is used by backup/cache owners with a clean
    caller transaction. Domain purges use prepare/enqueue instead, keeping
    logical deletion and authorization in their own transaction.
    """
    from app.db.publication import require_clean_publication_transaction
    from app.modules.storage.storage_deletion import (
        enqueue_prepared_owned_deletion,
        enqueue_prevalidated_receipt,
        prepare_owned_key_deletion,
        process_storage_delete_intents,
    )

    require_clean_publication_transaction(session)
    prepared = prepare_owned_key_deletion(
        session, backend, key, required_proof=required_proof
    )
    if prepared is None:
        return False
    if not enqueue_prepared_owned_deletion(
        session, prepared, required_proof=required_proof
    ):
        session.rollback()
        return False
    intent = enqueue_prevalidated_receipt(
        session,
        prepared.receipt,
        object_kind=prepared.object_kind,
        sha256=prepared.sha256,
    )
    intent_id = intent.id
    assert intent_id is not None
    session.commit()
    result = process_storage_delete_intents(backend=backend, intent_ids=(intent_id,))
    if required_proof and result.blocked:
        raise UnsafeStorageDeleteError("storage_object_no_longer_matches_receipt")
    if required_proof and result.pending:
        raise UnsafeStorageDeleteError("storage_delete_failed")
    return result.completed == 1
