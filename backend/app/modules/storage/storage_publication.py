"""Publication identities and SQL exclusion shared by adopters and collectors.

Prepare bytes and receipt evidence before entering a domain transaction. Hold
its authority/output row locks first, then take the publication locator anchor
last. No backend I/O is permitted after taking this anchor and before commit.
The anchor persists when ownership proofs are retired or removed, so even the
first writer and a later no-reservation adopter arbitrate through one SQL row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import OwnedStorageObject, StoragePublicationLocator
from app.db.transactions import begin_write
from app.modules.storage.storage_backend.contracts import CreationReceipt


class ReceiptReclaimResult(str, Enum):
    REMOVED = "removed"
    ABSENT = "absent"
    MISMATCH = "storage_receipt_mismatch"
    UNSUPPORTED = "storage_reclaim_unsupported"
    PROVIDER_MISMATCH = "storage_provider_mismatch"


@dataclass(frozen=True)
class PublicationReservation:
    """Immutable authority for one reservation, independent of database ID reuse."""

    id: int
    generation: str
    backend: str
    namespace: str
    key: str
    provider_ref: str | None

    @classmethod
    def of(cls, row: OwnedStorageObject) -> PublicationReservation:
        if row.id is None or not row.publication_generation:
            raise ValueError("storage_reservation_identity_missing")
        return cls(
            row.id,
            row.publication_generation,
            row.backend,
            row.namespace,
            row.key,
            row.provider_ref,
        )


@dataclass(frozen=True)
class PendingPublication:
    """Durable receipt prepared for later SQL-only, domain-authorized adoption."""

    reservation: PublicationReservation
    receipt: CreationReceipt
    object_kind: str
    sha256: str | None


@dataclass(frozen=True)
class VerifiedPublication:
    """Exact legacy receipt validated during preparation, before domain SQL."""

    receipt: CreationReceipt
    object_kind: str
    sha256: str | None


def same_creation(first: CreationReceipt, second: CreationReceipt) -> bool:
    """Compare exact physical generations, including historical local token aliases."""
    if (first.backend, first.namespace, first.key, first.size) != (
        second.backend,
        second.namespace,
        second.key,
        second.size,
    ):
        return False
    if first.backend == "local" and all(
        value is not None for value in (first.device, first.inode, first.ctime_ns)
    ):
        return (first.device, first.inode, first.ctime_ns) == (
            second.device,
            second.inode,
            second.ctime_ns,
        )
    if first.backend != "local" and (
        first.version_id not in (None, "", "null")
        and second.version_id not in (None, "", "null")
    ):
        # Immutable provider versions identify the physical generation. A
        # legacy adopter may reconstruct another logical token for that same
        # version; this cannot lend new authority to a revoked generation.
        return first.version_id == second.version_id
    return (
        first.token,
        first.etag,
        first.version_id,
        first.device,
        first.inode,
        first.ctime_ns,
    ) == (
        second.token,
        second.etag,
        second.version_id,
        second.device,
        second.inode,
        second.ctime_ns,
    )


def lock_publication_locator(
    session: Session, *, backend: str, namespace: str, key: str
) -> None:
    """Take durable SQL exclusion after domain locks, without storage I/O.

    Providers sharing a backend/namespace/key use the same exclusion row, while
    ownership validation still requires the exact provider identity. The coarse
    anchor also serializes an explicit legacy provider upgrade with collectors.
    PostgreSQL INSERT ON CONFLICT waits for the first uncommitted anchor; its
    subsequent UPDATE takes the row lock. SQLite reserves its writer before the
    lookup. An empty SELECT FOR UPDATE is never treated as exclusion.
    """
    begin_write(session, immediate=True)
    identifier = hashlib.sha256(
        json.dumps([backend, namespace, key], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        insert = sqlite_insert
    elif dialect == "postgresql":
        insert = pg_insert
    else:
        raise ValueError(f"storage_publication_dialect_unsupported:{dialect}")
    with session.no_autoflush:
        session.execute(
            insert(StoragePublicationLocator)
            .values(
                id=identifier,
                backend=backend,
                namespace=namespace,
                key=key,
                created_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        session.execute(
            update(StoragePublicationLocator)
            .where(StoragePublicationLocator.id == identifier)
            .values(id=StoragePublicationLocator.id)
            .execution_options(synchronize_session=False)
        )
        anchor = session.get(
            StoragePublicationLocator, identifier, populate_existing=True
        )
        if anchor is None or (anchor.backend, anchor.namespace, anchor.key) != (
            backend,
            namespace,
            key,
        ):
            raise RuntimeError("storage_publication_locator_identity_corrupt")
