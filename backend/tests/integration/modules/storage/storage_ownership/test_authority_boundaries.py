"""Real storage receipts and SQL authority refuse incompatible/stale observations."""

from dataclasses import replace

import pytest
from sqlmodel import select

from app.db.models import OwnedStorageObject, StorageDeleteIntent, StorageObjectState
from app.db.session import get_session_factory
from app.modules.storage import storage_ownership as owner
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_receipts import UnsafeStorageDeleteError


@pytest.fixture
def pending(db_session):
    backend = get_backend()
    key = backend.thumbnail_key(9601)
    candidate = owner.prepare_bytes(
        db_session, backend, key, b"authority proof", object_kind="thumbnail"
    )
    return backend, key, candidate


def snapshot(candidate):
    with get_session_factory().session() as reader:
        row = reader.get(OwnedStorageObject, candidate.reservation.id)
        assert row is not None
        intents = reader.exec(select(StorageDeleteIntent)).all()
        return row.model_dump(), [intent.model_dump() for intent in intents]


class TestReservePublication:
    @pytest.mark.parametrize("field", ["backend", "namespace", "key", "provider_ref"])
    def test_previous_locator_mismatch_preserves_authority(
        self, db_session, pending, field
    ):
        backend, key, candidate = pending
        before = snapshot(candidate)
        previous = replace(candidate.reservation, **{field: "different-authority"})

        with pytest.raises(UnsafeStorageDeleteError, match="storage_locator_mismatch"):
            owner.reserve_publication(
                db_session,
                backend=candidate.reservation.backend,
                namespace=candidate.reservation.namespace,
                key=key,
                provider_ref=candidate.reservation.provider_ref,
                object_kind="thumbnail",
                previous=previous,
            )

        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"


class TestFailPublication:
    def test_blocked_reservation_preserves_verified_error(self, db_session, pending):
        backend, key, candidate = pending
        assert owner.settle_observed_publication(
            db_session,
            candidate.reservation,
            observed_state=StorageObjectState.PENDING,
            state=StorageObjectState.BLOCKED,
            last_error="verified-block",
        )
        db_session.commit()
        before = snapshot(candidate)
        owner.fail_publication(db_session, candidate.reservation, OSError("late error"))
        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"

    @pytest.mark.parametrize("stale", ["missing-id", "generation"])
    def test_stale_failure_cannot_mutate_authority(self, db_session, pending, stale):
        backend, key, candidate = pending
        before = snapshot(candidate)
        reservation = (
            replace(candidate.reservation, id=candidate.reservation.id + 10000)
            if stale == "missing-id"
            else replace(candidate.reservation, generation="stale-generation")
        )
        owner.fail_publication(db_session, reservation, OSError("stale error"))
        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"


class TestSettleObservedPublication:
    @pytest.mark.parametrize("stale", ["missing-id", "generation", "observed-state"])
    def test_stale_observation_cannot_mutate_authority(
        self, db_session, pending, stale
    ):
        backend, key, candidate = pending
        before = snapshot(candidate)
        reservation = candidate.reservation
        observed = StorageObjectState.PENDING
        if stale == "missing-id":
            reservation = replace(reservation, id=reservation.id + 10000)
        elif stale == "generation":
            reservation = replace(reservation, generation="stale-generation")
        else:
            observed = StorageObjectState.BLOCKED

        assert not owner.settle_observed_publication(
            db_session,
            reservation,
            observed_state=observed,
            state=StorageObjectState.BLOCKED,
            last_error="stale-error",
        )
        db_session.commit()

        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"

    def test_reconciliation_cannot_adopt_publication(self, db_session, pending):
        backend, key, candidate = pending
        before = snapshot(candidate)

        with pytest.raises(ValueError, match="reconciliation_cannot_adopt_publication"):
            owner.settle_observed_publication(
                db_session,
                candidate.reservation,
                observed_state=StorageObjectState.PENDING,
                state=StorageObjectState.COMMITTED,
                last_error=None,
            )

        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"


class TestPrepareReceipt:
    @pytest.mark.parametrize("field", ["backend", "namespace", "key"])
    def test_receipt_locator_mismatch_preserves_authority(
        self, db_session, pending, field
    ):
        backend, key, candidate = pending
        before = snapshot(candidate)
        receipt = replace(candidate.receipt, **{field: "different-locator"})

        with pytest.raises(StorageCollisionError, match="storage_locator_mismatch"):
            owner.prepare_receipt(
                db_session,
                candidate.reservation,
                receipt,
                object_kind="thumbnail",
                sha256=candidate.sha256,
            )

        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"

    def test_receipt_provider_mismatch_preserves_authority(self, db_session, pending):
        backend, key, candidate = pending
        before = snapshot(candidate)

        with pytest.raises(StorageCollisionError, match="storage_provider_mismatch"):
            owner.prepare_receipt(
                db_session,
                candidate.reservation,
                candidate.receipt,
                object_kind="thumbnail",
                sha256=candidate.sha256,
                provider_ref="different-provider",
            )

        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"

    @pytest.mark.parametrize("field", ["size", "inode", "ctime_ns"])
    def test_conflicting_physical_receipt_preserves_authority(
        self, db_session, pending, field
    ):
        backend, key, candidate = pending
        before = snapshot(candidate)
        original = getattr(candidate.receipt, field)
        assert original is not None
        receipt = replace(candidate.receipt, **{field: original + 1})

        with pytest.raises(
            StorageCollisionError, match="storage_reservation_receipt_mismatch"
        ):
            owner.prepare_receipt(
                db_session,
                candidate.reservation,
                receipt,
                object_kind="thumbnail",
                sha256=candidate.sha256,
            )

        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"

    def test_blocked_reservation_refuses_completion(self, db_session, pending):
        backend, key, candidate = pending
        assert owner.settle_observed_publication(
            db_session,
            candidate.reservation,
            observed_state=StorageObjectState.PENDING,
            state=StorageObjectState.BLOCKED,
            last_error="verified-block",
        )
        db_session.commit()
        before = snapshot(candidate)

        with pytest.raises(RuntimeError, match="storage_reservation_retired"):
            owner.prepare_receipt(
                db_session,
                candidate.reservation,
                candidate.receipt,
                object_kind="thumbnail",
                sha256=candidate.sha256,
            )

        assert snapshot(candidate) == before
        assert backend.read_bytes(key) == b"authority proof"
