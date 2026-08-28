"""The delete outbox: authorize a deletion in the database, remove the bytes later.

Deleting a blob and deleting its row cannot be one atomic act — the database and the
object store are separate systems, and whichever goes first can leave the other wrong. So
PrintStash never deletes bytes inside a request. It writes a **delete intent** that names
the exact receipt it is authorized to remove, inside the caller's transaction, and a
separate pass acts on it afterwards. A rollback therefore takes the authorization with it
and leaves the bytes and their ownership proof intact.

"The exact receipt" is what makes it safe to run later. An intent carries the token, size,
etag, device and inode of the object as it was when ownership was recorded, and the pass
re-verifies all of it before removing anything. If the object has changed since — someone
replaced it, or the key was reused — the intent is **blocked**, not completed, because a
key alone does not prove the bytes are still ours to delete.

The three outcomes are reported separately for that reason: completed, pending (worth
retrying), and blocked (needs a human).
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.db.models import StorageDeleteIntent
from app.services.storage_backend import get_backend
from app.services.storage_deletion import (
    enqueue_creation_receipt,
    enqueue_owned_key,
    process_storage_delete_intents,
)
from app.services.storage_ownership import UnsafeStorageDeleteError, record_creation


@pytest.fixture
def owned(db_session: Session):
    """Write bytes and record ownership of them, the way ingestion does."""
    made = {"n": 0}

    def build(data: bytes = b"owned-bytes") -> tuple[str, object]:
        made["n"] += 1
        backend = get_backend()
        key = backend.blob_key("deletion", made["n"], "object.bin")
        receipt = backend.create_bytes(data, key)
        record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()
        return key, receipt

    return build


def _intents(session: Session) -> list[StorageDeleteIntent]:
    session.expire_all()
    return list(session.exec(select(StorageDeleteIntent)).all())


class TestEnqueueOwnedKey:
    def test_authorizes_the_deletion_of_a_key_it_owns(
        self, db_session: Session, owned
    ) -> None:
        key, _receipt = owned()

        enqueued = enqueue_owned_key(db_session, get_backend(), key)
        db_session.commit()

        assert enqueued is True
        assert [intent.key for intent in _intents(db_session)] == [key]

    def test_leaves_the_bytes_alone(self, db_session: Session, owned) -> None:
        key, _receipt = owned()

        enqueue_owned_key(db_session, get_backend(), key)
        db_session.commit()

        # Authorization is not deletion; the pass removes the bytes later.
        assert get_backend().exists(key)

    def test_reports_a_key_it_does_not_own(self, db_session: Session) -> None:
        get_backend().write_bytes(b"not ours", "deletion/unowned.bin")

        assert (
            enqueue_owned_key(db_session, get_backend(), "deletion/unowned.bin")
            is False
        )

    def test_reports_a_key_whose_bytes_have_changed(
        self, db_session: Session, owned
    ) -> None:
        key, _receipt = owned()
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.write_bytes(b"replaced by somebody else")

        # A key alone does not prove the bytes are still ours.
        assert enqueue_owned_key(db_session, get_backend(), key) is False

    def test_refuses_a_key_it_does_not_own_when_proof_is_required(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.blob_key("unowned-required", 1, "object.bin")
        backend.write_bytes(b"not ours", key)

        # A caller that says "prove it" gets an exception, not a quiet False —
        # a purge must not report success for bytes it never authorized.
        with pytest.raises(
            UnsafeStorageDeleteError, match="storage_ownership_unverified"
        ):
            enqueue_owned_key(db_session, backend, key, required_proof=True)

    def test_refuses_to_delete_without_verifiable_proof(
        self, db_session: Session, owned, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key, _receipt = owned()

        def unverifiable(*_args: object, **_kwargs: object):
            raise OSError("storage unreachable")

        monkeypatch.setattr(get_backend(), "creation_matches", unverifiable)

        with pytest.raises(
            UnsafeStorageDeleteError, match="storage_verification_failed"
        ):
            enqueue_owned_key(db_session, get_backend(), key, required_proof=True)

    def test_reports_a_failed_verification_when_proof_is_not_required(
        self, db_session: Session, owned, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key, _receipt = owned()

        def unverifiable(*_args: object, **_kwargs: object):
            raise OSError("storage unreachable")

        monkeypatch.setattr(get_backend(), "creation_matches", unverifiable)

        # A background sweep must not fail the whole pass over one unreachable
        # object; it just does not authorize that one.
        assert enqueue_owned_key(db_session, get_backend(), key) is False


class TestEnqueueCreationReceipt:
    def test_authorizes_the_deletion_of_an_exact_receipt(
        self, db_session: Session, owned
    ) -> None:
        key, receipt = owned()

        intent = enqueue_creation_receipt(db_session, get_backend(), receipt)
        db_session.commit()

        assert intent.key == key

    def test_returns_the_intent_it_already_wrote(
        self, db_session: Session, owned
    ) -> None:
        _key, receipt = owned()
        first = enqueue_creation_receipt(db_session, get_backend(), receipt)
        db_session.commit()

        second = enqueue_creation_receipt(db_session, get_backend(), receipt)

        # A retried request must not queue the same deletion twice.
        assert second.id == first.id
        assert len(_intents(db_session)) == 1

    def test_refuses_a_receipt_the_object_no_longer_matches(
        self, db_session: Session, owned
    ) -> None:
        key, receipt = owned()
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.write_bytes(b"replaced by somebody else")

        with pytest.raises(
            UnsafeStorageDeleteError, match="storage_object_no_longer_matches_receipt"
        ):
            enqueue_creation_receipt(db_session, get_backend(), receipt)


class TestProcessStorageDeleteIntents:
    def test_removes_the_bytes_it_was_authorized_to_remove(
        self, db_session: Session, owned
    ) -> None:
        key, _receipt = owned()
        enqueue_owned_key(db_session, get_backend(), key)
        db_session.commit()

        result = process_storage_delete_intents()

        assert result.completed == 1
        assert not get_backend().exists(key)

    def test_reports_nothing_to_do_when_the_outbox_is_empty(self) -> None:
        result = process_storage_delete_intents()

        assert result == type(result)()

    def test_blocks_an_intent_written_for_a_different_backend(
        self, db_session: Session, owned
    ) -> None:
        key, _receipt = owned()
        enqueue_owned_key(db_session, get_backend(), key)
        db_session.commit()
        intent = _intents(db_session)[0]
        intent.backend = "s3"
        db_session.add(intent)
        db_session.commit()

        result = process_storage_delete_intents()

        # An intent recorded against S3 must never be executed against the local
        # disk, where the same key means completely different bytes.
        assert result.blocked == 1
        assert get_backend().exists(key)

    def test_says_why_it_blocked_a_backend_mismatch(
        self, db_session: Session, owned
    ) -> None:
        key, _receipt = owned()
        enqueue_owned_key(db_session, get_backend(), key)
        db_session.commit()
        intent = _intents(db_session)[0]
        intent.backend = "s3"
        db_session.add(intent)
        db_session.commit()

        process_storage_delete_intents()

        assert _intents(db_session)[0].last_error == "storage_backend_mismatch"

    def test_blocks_an_object_that_changed_after_it_was_authorized(
        self, db_session: Session, owned
    ) -> None:
        key, _receipt = owned()
        enqueue_owned_key(db_session, get_backend(), key)
        db_session.commit()
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.write_bytes(b"replaced between authorization and deletion")

        result = process_storage_delete_intents()

        assert result.blocked == 1
        assert direct.exists()

    def test_leaves_an_intent_pending_when_the_backend_is_unreachable(
        self, db_session: Session, owned, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key, _receipt = owned()
        enqueue_owned_key(db_session, get_backend(), key)
        db_session.commit()

        def unreachable(*_args: object, **_kwargs: object):
            raise OSError("storage unreachable")

        monkeypatch.setattr(get_backend(), "rollback_create", unreachable)

        result = process_storage_delete_intents()

        # Pending, not blocked: a retry may well work.
        assert result.pending == 1

    def test_stops_at_the_limit_it_was_given(self, db_session: Session, owned) -> None:
        for _ in range(3):
            key, _receipt = owned()
            enqueue_owned_key(db_session, get_backend(), key)
        db_session.commit()

        result = process_storage_delete_intents(limit=2)

        assert result.completed == 2
