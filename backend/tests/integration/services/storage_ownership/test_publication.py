"""Storage publication integration tests.

These tests defend reservation-before-publication, durable failure intents, and
the transaction boundary that joins ownership to the caller's domain write.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import FileType, OwnedStorageObject, StorageObjectState, User
from app.db.session import _set_sqlite_pragmas, get_session_factory
from app.services.ingestion import _resolve_committed_artifact
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    StorageCollisionError,
    get_backend,
)
from app.services.storage_ownership import (
    complete_publication,
    publish_bytes,
    publish_file,
    publish_stream,
    record_creation,
    reserve_creation,
)
from tests.factories import build_file, build_model, build_user


class _FailingLocalStorageBackend(LocalStorageBackend):
    def create_stream(self, src: BytesIO, key: str):
        del src, key
        raise OSError("disk full")


class TestPublishBytes:
    def test_fresh_session_resolves_a_committed_artifact_after_ack_failure(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        model = build_model(db_session, "Ack proof")
        assert model.id is not None
        key = backend.blob_key("commit-proof", 1, "part.stl")
        payload = b"committed-after-ack-loss"
        receipt = backend.create_bytes(payload, key)
        record_creation(
            db_session,
            receipt,
            object_kind="artifact",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        build_file(
            db_session,
            model,
            path=key,
            filename="part.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        db_session.commit()

        resolved = _resolve_committed_artifact(
            backend=backend,
            key=key,
            model_id=model.id,
            blob_hash=hashlib.sha256(payload).hexdigest(),
            ingestion_key=None,
        )

        assert resolved is not None
        assert resolved.path == key

    @pytest.mark.parametrize(
        ("object_kind", "key_method", "arguments"),
        [
            ("artifact", "blob_key", ("managed", 1, "part.stl")),
            ("thumbnail", "thumbnail_key", (701,)),
            ("model_source_cover", "source_cover_key", (702,)),
            ("capture_upload_slot", "capture_upload_slot_key", ("slot-703",)),
            ("derived_stl_cache", "stl_cache_key", ("a" * 64,)),
            ("collection_image", "collection_image_key", (704, "image.png")),
            ("document_file", "document_file_key", (705, "manual.pdf")),
            ("document_image", "document_image_key", (706, "image.png")),
        ],
    )
    def test_publishes_every_managed_key_kind_through_the_ledger(
        self,
        db_session: Session,
        object_kind: str,
        key_method: str,
        arguments: tuple[object, ...],
    ) -> None:
        backend = get_backend()
        key = getattr(backend, key_method)(*arguments)

        publish_bytes(db_session, backend, key, b"managed", object_kind=object_kind)
        db_session.commit()

        row = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert row.object_kind == object_kind
        assert row.state is StorageObjectState.COMMITTED

    def test_reserves_the_key_durably_before_storage_publication(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(900)

        reservation_id = reserve_creation(
            db_session,
            backend,
            key,
            object_kind="thumbnail",
            expected_size=5,
        )

        with get_session_factory().session() as independent:
            row = independent.get(OwnedStorageObject, reservation_id)
            assert row is not None
            assert row.state is StorageObjectState.PENDING
        assert not backend.exists(key)

    def test_rejects_a_duplicate_pending_reservation(self, db_session: Session) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(899)
        first_id = reserve_creation(db_session, backend, key, object_kind="thumbnail")

        with pytest.raises(StorageCollisionError):
            reserve_creation(db_session, backend, key, object_kind="thumbnail")

        with get_session_factory().session() as independent:
            assert independent.get(OwnedStorageObject, first_id) is not None

    def test_commits_ownership_with_the_callers_transaction(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(901)

        publish_bytes(db_session, backend, key, b"thumbnail", object_kind="thumbnail")
        db_session.commit()

        row = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert row.state is StorageObjectState.COMMITTED

    def test_leaves_pending_intent_when_storage_fails(
        self, db_session: Session
    ) -> None:
        backend = _FailingLocalStorageBackend()
        key = backend.thumbnail_key(902)

        with pytest.raises(OSError, match="disk full"):
            publish_bytes(
                db_session, backend, key, b"thumbnail", object_kind="thumbnail"
            )

        with get_session_factory().session() as independent:
            row = independent.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            assert row.state is StorageObjectState.PENDING
            assert row.last_error == "OSError"

    def test_keeps_pending_intent_when_the_domain_transaction_rolls_back(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(903)

        publish_bytes(db_session, backend, key, b"thumbnail", object_kind="thumbnail")
        db_session.rollback()

        with get_session_factory().session() as independent:
            row = independent.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            assert row.state is StorageObjectState.PENDING

    def test_flushed_caller_dml_rolls_back_after_publication(self, tmp_path) -> None:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'publication-atomicity.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        backend = get_backend()
        key = backend.thumbnail_key(904)

        with Session(engine) as caller:
            user = build_user(caller, "rolled-back-before-publication")
            user.email = "must-rollback@example.test"
            caller.add(user)
            caller.flush()

            with pytest.raises(
                RuntimeError,
                match="storage_publication_requires_clean_sqlite_transaction",
            ):
                publish_bytes(
                    caller, backend, key, b"thumbnail", object_kind="thumbnail"
                )
            caller.rollback()

        with Session(engine) as independent:
            persisted = independent.exec(
                select(User).where(User.username == "rolled-back-before-publication")
            ).one()
            assert persisted.email is None
        engine.dispose()


class TestPublishStream:
    def test_publishes_stream_content_with_supplied_hash(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(920)
        digest = hashlib.sha256(b"streamed").hexdigest()

        receipt = publish_stream(
            db_session,
            backend,
            key,
            BytesIO(b"streamed"),
            object_kind="thumbnail",
            expected_size=8,
            sha256=digest,
        )
        db_session.commit()

        assert backend.read_bytes(key) == b"streamed"
        row = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert receipt.size == 8
        assert row.state is StorageObjectState.COMMITTED
        assert row.sha256 == digest

    def test_leaves_a_pending_intent_when_stream_publication_fails(
        self, db_session: Session
    ) -> None:
        backend = _FailingLocalStorageBackend()
        key = backend.thumbnail_key(921)

        with pytest.raises(OSError, match="disk full"):
            publish_stream(
                db_session,
                backend,
                key,
                BytesIO(b"streamed"),
                object_kind="thumbnail",
            )

        row = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert row.state is StorageObjectState.PENDING
        assert row.last_error == "OSError"


class TestPublishFile:
    def test_publishes_a_staged_file_without_removing_the_source(
        self, db_session: Session, tmp_path
    ) -> None:
        backend = get_backend()
        source = tmp_path / "staged.stl"
        source.write_bytes(b"staged")
        key = backend.thumbnail_key(922)

        receipt = publish_file(
            db_session, backend, key, source, object_kind="thumbnail"
        )
        db_session.commit()

        assert source.exists()
        assert backend.read_bytes(key) == b"staged"
        assert receipt.size == 6

    def test_moves_a_staged_file_into_storage_when_requested(
        self, db_session: Session, tmp_path
    ) -> None:
        backend = get_backend()
        source = tmp_path / "staged.stl"
        source.write_bytes(b"staged")
        key = backend.thumbnail_key(923)

        publish_file(
            db_session, backend, key, source, object_kind="thumbnail", move=True
        )
        db_session.commit()

        assert not source.exists()
        assert backend.read_bytes(key) == b"staged"

    def test_leaves_a_pending_intent_when_staged_file_publication_fails(
        self, db_session: Session, tmp_path
    ) -> None:
        backend = _FailingLocalStorageBackend()
        source = tmp_path / "staged.stl"
        source.write_bytes(b"staged")
        key = backend.thumbnail_key(924)

        with pytest.raises(OSError, match="disk full"):
            publish_file(db_session, backend, key, source, object_kind="thumbnail")

        row = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert row.state is StorageObjectState.PENDING
        assert row.last_error == "OSError"
        assert not backend.exists(key)


class TestReserveCreation:
    def test_reuses_a_stale_committed_locator_when_bytes_are_absent(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(930)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.COMMITTED,
            token="old-token",
            size_bytes=5,
            etag="old-etag",
        )
        db_session.add(row)
        db_session.commit()
        original_id = row.id

        reservation_id = reserve_creation(
            db_session,
            backend,
            key,
            object_kind="thumbnail",
            expected_size=8,
            sha256="new-hash",
        )

        db_session.refresh(row)
        assert reservation_id == original_id
        assert row.state is StorageObjectState.PENDING
        assert row.token is None
        assert row.size_bytes == 8
        assert row.sha256 == "new-hash"

    def test_rejects_completion_when_the_reservation_is_missing(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(931)

        creation = CreationReceipt(
            key=key,
            size=4,
            token="token",
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
        )

        with pytest.raises(RuntimeError, match="storage_reservation_lost"):
            complete_publication(
                db_session,
                999999,
                creation,
                object_kind="thumbnail",
                sha256=None,
            )
