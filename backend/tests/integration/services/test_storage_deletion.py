"""The deletion outbox separates committed DB intent from exact byte removal.

This boundary prevents rollback, retry, or replaced-object races from deleting
bytes that PrintStash can no longer prove it created.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import OwnedStorageObject, StorageDeleteIntent
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    get_backend,
)
from app.services.storage_deletion import (
    enqueue_creation_receipt,
    enqueue_owned_key,
    process_storage_delete_intents,
)
from app.services.storage_ownership import UnsafeStorageDeleteError, record_creation


def _create_owned_object(
    session: Session, backend: LocalStorageBackend, name: str = "owned.bin"
) -> CreationReceipt:
    key = backend.blob_key("deletion", 1, name)
    receipt = backend.create_bytes(b"owned-bytes", key)
    record_creation(session, receipt, object_kind="artifact")
    session.commit()
    return receipt


class TestEnqueueOwnedKey:
    def test_moves_proof_into_an_intent_without_deleting_bytes(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        receipt = _create_owned_object(db_session, backend)

        enqueued = enqueue_owned_key(
            db_session,
            backend,
            receipt.key,
            required_proof=True,
            resource_kind="file",
            resource_id=42,
        )
        db_session.commit()

        intent = db_session.exec(select(StorageDeleteIntent)).one()
        assert enqueued is True
        assert backend.read_bytes(receipt.key) == b"owned-bytes"
        assert db_session.exec(select(OwnedStorageObject)).all() == []
        assert (intent.key, intent.token, intent.resource_kind, intent.resource_id) == (
            receipt.key,
            receipt.token,
            "file",
            "42",
        )

    def test_returns_false_for_an_unclaimed_optional_key(
        self, db_session: Session
    ) -> None:
        backend = get_backend()

        enqueued = enqueue_owned_key(db_session, backend, "missing.bin")

        assert enqueued is False
        assert db_session.exec(select(StorageDeleteIntent)).all() == []

    def test_rejects_an_unclaimed_required_key(self, db_session: Session) -> None:
        backend = get_backend()

        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            enqueue_owned_key(db_session, backend, "missing.bin", required_proof=True)

        assert db_session.exec(select(StorageDeleteIntent)).all() == []

    def test_rejects_required_proof_after_the_object_is_replaced(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        receipt = _create_owned_object(db_session, backend)
        direct = backend.direct_path(receipt.key)
        assert direct is not None
        direct.unlink()
        direct.write_bytes(b"replacement")

        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            enqueue_owned_key(db_session, backend, receipt.key, required_proof=True)

        assert direct.read_bytes() == b"replacement"
        assert db_session.exec(select(StorageDeleteIntent)).all() == []


class TestEnqueueCreationReceipt:
    def test_creates_one_idempotent_intent_for_a_short_lived_receipt(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.capture_upload_slot_key("slot-1")
        receipt = backend.create_bytes(b"capture", key)

        first = enqueue_creation_receipt(
            db_session,
            backend,
            receipt,
            resource_kind="capture_upload_slot",
            resource_id="slot-1",
        )
        db_session.commit()
        second = enqueue_creation_receipt(db_session, backend, receipt)

        assert second.id == first.id
        assert db_session.exec(select(StorageDeleteIntent)).all() == [first]
        assert backend.read_bytes(key) == b"capture"

    def test_rolls_back_the_intent_with_its_callers_transaction(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.capture_upload_slot_key("slot-rollback")
        receipt = backend.create_bytes(b"capture", key)

        enqueue_creation_receipt(db_session, backend, receipt)
        db_session.rollback()

        assert db_session.exec(select(StorageDeleteIntent)).all() == []
        assert backend.read_bytes(key) == b"capture"


class TestProcessStorageDeleteIntents:
    def test_completes_a_matching_intent_and_removes_exact_bytes(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        receipt = _create_owned_object(db_session, backend)
        enqueue_owned_key(db_session, backend, receipt.key, required_proof=True)
        db_session.commit()

        result = process_storage_delete_intents()

        db_session.expire_all()
        intent = db_session.exec(select(StorageDeleteIntent)).one()
        assert result.completed == 1
        assert (result.pending, result.blocked) == (0, 0)
        assert intent.status == "completed"
        assert backend.exists(receipt.key) is False

    def test_completes_idempotently_when_the_exact_object_is_already_absent(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        receipt = _create_owned_object(db_session, backend)
        enqueue_owned_key(db_session, backend, receipt.key, required_proof=True)
        db_session.commit()
        direct = backend.direct_path(receipt.key)
        assert direct is not None
        direct.unlink()

        result = process_storage_delete_intents()

        db_session.expire_all()
        intent = db_session.exec(select(StorageDeleteIntent)).one()
        assert result.completed == 1
        assert intent.status == "completed"

    def test_blocks_a_replaced_object_and_preserves_its_bytes(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        receipt = _create_owned_object(db_session, backend)
        enqueue_owned_key(db_session, backend, receipt.key, required_proof=True)
        db_session.commit()
        direct = backend.direct_path(receipt.key)
        assert direct is not None
        direct.unlink()
        direct.write_bytes(b"replacement")

        result = process_storage_delete_intents()

        db_session.expire_all()
        intent = db_session.exec(select(StorageDeleteIntent)).one()
        assert result.blocked == 1
        assert intent.status == "blocked"
        assert intent.last_error == "storage_receipt_mismatch"
        assert direct.read_bytes() == b"replacement"

    def test_retries_after_a_backend_delete_failure(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = get_backend()
        receipt = _create_owned_object(db_session, backend)
        enqueue_owned_key(db_session, backend, receipt.key, required_proof=True)
        db_session.commit()

        def fail_delete(_receipt: CreationReceipt) -> bool:
            raise OSError("storage unavailable")

        monkeypatch.setattr(backend, "rollback_create", fail_delete)

        result = process_storage_delete_intents()

        db_session.expire_all()
        intent = db_session.exec(select(StorageDeleteIntent)).one()
        assert result.pending == 1
        assert intent.status == "retry"
        assert intent.last_error == "OSError"
        assert Path(receipt.key).read_bytes() == b"owned-bytes"

    def test_respects_the_processing_limit(self, db_session: Session) -> None:
        backend = get_backend()
        first = _create_owned_object(db_session, backend, "first.bin")
        second = _create_owned_object(db_session, backend, "second.bin")
        enqueue_owned_key(db_session, backend, first.key, required_proof=True)
        enqueue_owned_key(db_session, backend, second.key, required_proof=True)
        db_session.commit()

        result = process_storage_delete_intents(limit=1)

        db_session.expire_all()
        statuses = db_session.exec(
            select(StorageDeleteIntent.status).order_by(StorageDeleteIntent.id)
        ).all()
        assert result.completed == 1
        assert statuses == ["completed", "pending"]
