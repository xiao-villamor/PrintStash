"""Failure-safe cover publishing uses only backend seams, never local paths."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    BackgroundJob,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    OwnedStorageObject,
    StagingLease,
    StorageDeleteIntent,
    StorageObjectState,
)
from app.db.session import SQLiteSessionFactory, _set_sqlite_pragmas
from app.services import inbox, source_covers, staging_leases, trash
from app.services.source_cover_processing import process_source_cover_upload
from app.services.storage_backend import (
    CreationReceipt,
    StorageBackend,
    StorageObjectInfo,
    get_backend,
)
from app.services.storage_ownership import provider_ref_for_backend, record_creation
from tests.factories import build_model, build_user


def _png(color: str = "navy") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="PNG")
    return output.getvalue()


def _source(session: Session) -> ModelProvenanceSource:
    ident = uuid.uuid4().hex
    model = build_model(
        session,
        name=f"Cover model {ident}",
        slug=f"cover-model-{ident}",
        hash=ident * 2,
    )
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="test",
        canonical_url="https://example.test/cover",
        identity_key=uuid.uuid4().hex * 2,
    )
    session.add(source)
    session.flush()
    return source


def _backend() -> MagicMock:
    backend = MagicMock(spec=StorageBackend)
    # Keep the fake's locator identity as explicit as a production adapter's;
    # publication helpers now bind receipts to this destination.
    backend.backend_name = "fake"
    backend.namespace = "test"
    backend.provider_id = "fake"
    backend.transport = "fake"
    backend.namespace_for.side_effect = lambda _key: "test"
    backend.source_cover_key.side_effect = lambda ident: f"opaque/covers/{ident}.webp"
    return backend


def _receipt(key: str = "opaque/covers/1.webp", token: str = "new") -> CreationReceipt:
    return CreationReceipt(
        key=key,
        size=10,
        token=token,
        backend="fake",
        namespace="test",
        provider_ref=provider_ref_for_backend(
            SimpleNamespace(backend_name="fake", namespace="test"),
            namespace="test",
        ),
    )


def _put_cover(session, backend, source, colour: str):
    """Publish a cover of *colour* for *source* — the arrange step of a replacement.

    Three of these in a row is what "successive replacements" means, so it lives
    here rather than being written out per test.
    """
    return source_covers.put(
        session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png(colour),
        content_type="image/png",
    )


class TestPut:
    def test_persists_the_publication_provider_ref_after_an_active_switch(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _source(db_session)
        backend = _backend()
        receipt = _receipt(f"opaque/covers/{source.id}.webp")
        backend.create_bytes.return_value = receipt
        switched = _backend()
        switched.backend_name = "s3"
        switched.provider_id = "s3"
        switched.transport = "s3"
        switched_ref = provider_ref_for_backend(switched, namespace="test")
        monkeypatch.setattr(
            staging_leases,
            "provider_ref_for_backend",
            lambda *_args, **_kwargs: switched_ref,
        )

        source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png(),
            content_type="image/png",
        )

        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert proof.provider_ref == receipt.provider_ref

    def test_create_rolls_back_published_bytes_when_recording_the_receipt_fails(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _source(db_session)
        backend = _backend()
        receipt = _receipt(f"opaque/covers/{source.id}.webp")
        backend.create_bytes.return_value = receipt
        monkeypatch.setattr(
            staging_leases,
            "record_cover_receipt",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("proof failed")),
        )

        with pytest.raises(RuntimeError, match="proof failed"):
            source_covers.put(
                db_session,
                backend,
                provenance_source_id=source.id,
                actor_id=None,
                data=_png(),
                content_type="image/png",
            )

        backend.rollback_create.assert_called_once_with(receipt)
        assert db_session.exec(select(ModelSourceCover)).all() == []
        assert db_session.exec(select(StagingLease)).all() == []
        assert db_session.exec(select(OwnedStorageObject)).all() == []

    def test_leaves_the_old_cover_untouched_when_the_replace_fails(
        self, db_session: Session
    ) -> None:
        source = _source(db_session)
        cover = ModelSourceCover(
            provenance_source_id=source.id,
            storage_key="opaque/covers/1.webp",
            size_bytes=3,
        )
        db_session.add(cover)
        old = _receipt(token="old")
        record_creation(db_session, old, object_kind="model_source_cover")
        db_session.flush()
        backend = _backend()
        backend.read_bytes.return_value = b"old"
        backend.creation_matches.return_value = True
        backend.replace_bytes.side_effect = RuntimeError("replace failed")

        with pytest.raises(RuntimeError, match="replace failed"):
            source_covers.put(
                db_session,
                backend,
                provenance_source_id=source.id,
                actor_id=None,
                data=_png(),
                content_type="image/png",
            )

        assert cover.size_bytes == 3
        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert proof.token == "old"

    def test_publishes_the_newest_bytes_after_repeated_replacements(
        self, db_session: Session
    ) -> None:
        source = _source(db_session)
        backend = get_backend()
        first = _put_cover(db_session, backend, source, "navy")
        db_session.commit()
        _put_cover(db_session, backend, source, "maroon")
        db_session.commit()

        latest = _put_cover(db_session, backend, source, "gold")
        db_session.commit()

        assert latest.cover.id == first.cover.id
        expected = process_source_cover_upload(_png("gold"), "image/png").data
        assert backend.read_bytes(latest.cover.storage_key) == expected
        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert proof.provider_ref == provider_ref_for_backend(
            backend, namespace=proof.namespace
        )

    def test_leaves_no_staging_lease_behind_after_a_replacement(
        self, db_session: Session
    ) -> None:
        source = _source(db_session)
        backend = get_backend()
        _put_cover(db_session, backend, source, "navy")
        db_session.commit()

        _put_cover(db_session, backend, source, "maroon")

        assert db_session.exec(select(StagingLease)).all() == []

    def test_new_replacement_supersedes_a_crashed_prior_generation(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        backend = get_backend()
        first = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png("navy"),
            content_type="image/png",
        )
        db_session.commit()

        source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png("maroon"),
            content_type="image/png",
        )
        # Simulate a process crash after publication but before the caller's
        # transaction commits its replacement metadata and lease release.
        db_session.rollback()
        assert db_session.exec(select(StagingLease)).one()

        latest = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png("gold"),
            content_type="image/png",
        )
        db_session.commit()

        expected = process_source_cover_upload(_png("gold"), "image/png").data
        assert latest.cover.id == first.cover.id
        assert backend.read_bytes(latest.cover.storage_key) == expected
        assert db_session.exec(select(StagingLease)).all() == []

    def test_create_publish_failure_leaves_only_a_recovery_proof(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        backend = _backend()
        backend.create_bytes.side_effect = RuntimeError("publish failed")

        with pytest.raises(RuntimeError, match="publish failed"):
            source_covers.put(
                db_session,
                backend,
                provenance_source_id=source.id,
                actor_id=None,
                data=_png(),
                content_type="image/png",
            )

        assert db_session.exec(select(ModelSourceCover)).all() == []
        assert db_session.exec(select(StagingLease)).all() == []
        ownership = db_session.exec(select(OwnedStorageObject)).all()
        assert len(ownership) == 1
        assert ownership[0].state is StorageObjectState.PENDING

    def test_deleting_a_cover_cascades_to_its_lease(self, db_session: Session) -> None:
        source = _source(db_session)
        cover = ModelSourceCover(
            provenance_source_id=source.id,
            storage_key="opaque/covers/cascade.webp",
            size_bytes=3,
        )
        db_session.add(cover)
        db_session.flush()
        assert cover.id is not None
        lease = staging_leases.create_cover_lease(
            db_session,
            model_source_cover_id=cover.id,
            owner_user_id=None,
            destination_key=cover.storage_key,
            size_bytes=3,
            sha256="a" * 64,
        )
        lease_id = lease.id
        db_session.commit()

        db_session.delete(cover)
        db_session.commit()

        assert db_session.get(StagingLease, lease_id) is None

    def test_refuses_a_cover_lease_that_also_names_a_background_job(
        self, db_session: Session
    ) -> None:
        source = _source(db_session)
        cover = ModelSourceCover(
            provenance_source_id=source.id,
            storage_key="opaque/covers/conflict.webp",
            size_bytes=1,
        )
        job = BackgroundJob(id="cover-owner-conflict")
        db_session.add_all([cover, job])
        db_session.commit()
        assert cover.id is not None

        db_session.add(
            StagingLease(
                id="cover-owner-conflict",
                path="cover:invalid",
                background_job_id=job.id,
                model_source_cover_id=cover.id,
                size_bytes=1,
                sha256="a" * 64,
                expires_at=cover.created_at,
            )
        )

        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_commit_failure_rolls_back_new_publish_with_exact_receipt(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        backend = _backend()
        receipt = _receipt(f"opaque/covers/{source.id}.webp")
        backend.create_bytes.return_value = receipt
        result = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png(),
            content_type="image/png",
        )
        db_session.rollback()

        source_covers.rollback_after_commit_failure(db_session, backend, result)

        backend.rollback_create.assert_called_once_with(receipt)
        assert db_session.exec(select(ModelSourceCover)).all() == []
        assert db_session.exec(select(StagingLease)).all() == []

    def test_restores_the_previous_cover_when_the_commit_fails(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        backend = get_backend()
        first = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png("navy"),
            content_type="image/png",
        )
        db_session.commit()
        old_bytes = backend.read_bytes(first.cover.storage_key)

        replacement = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png("maroon"),
            content_type="image/png",
        )
        db_session.rollback()
        source_covers.rollback_after_commit_failure(db_session, backend, replacement)

        assert backend.read_bytes(first.cover.storage_key) == old_bytes
        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert backend.creation_matches(
            CreationReceipt(
                key=proof.key,
                size=proof.size_bytes,
                token=proof.token,
                backend=proof.backend,
                namespace=proof.namespace,
                etag=proof.etag,
                version_id=proof.version_id,
                device=proof.device,
                inode=proof.inode,
                ctime_ns=proof.ctime_ns,
            )
        )

    def test_restart_reconciles_cover_published_before_receipt_commit(
        self,
        db_session: Session,
    ) -> None:
        """A crash after create-only publication leaves a recoverable cover intent."""
        source = _source(db_session)
        backend = get_backend()
        data = _png("navy")
        result = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=data,
            content_type="image/png",
        )
        assert result.cover.id is not None
        # The caller's transaction is the receipt/proof commit boundary. A
        # process crash here rolls back that transaction, while the precommitted
        # cover + lease and published bytes survive.
        db_session.rollback()

        assert backend.exists(result.cover.storage_key)
        assert db_session.exec(select(StagingLease)).all()
        assert source_covers.reconcile_pending(db_session, backend) == 1
        db_session.commit()

        assert db_session.exec(select(StagingLease)).all() == []
        assert (
            db_session.exec(select(OwnedStorageObject)).one().key
            == result.cover.storage_key
        )
        assert backend.read_bytes(result.cover.storage_key)

    def test_cover_intent_does_not_commit_callers_unrelated_transaction(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _source(db_session)
        db_session.commit()
        caller_commit = db_session.commit
        commits: list[object] = []
        monkeypatch.setattr(
            db_session,
            "commit",
            lambda: commits.append(object()),
        )
        backend = get_backend()
        result = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png(),
            content_type="image/png",
        )

        assert result.cover.id is not None
        assert commits == []
        monkeypatch.setattr(db_session, "commit", caller_commit)
        db_session.rollback()

    def test_publishes_a_cover_in_exactly_two_sqlite_commits(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cover publication must not open a second writer behind Inbox flushes.

        Two commits and no more: the cover's durable intent (its own engine-bound
        transaction, which has to land *before* any byte is published so a crash is
        recoverable) and the final Inbox terminalization. A third would mean cover
        publication opened a writer while the terminalizing transaction was still
        open, which on SQLite is a `database is locked` under any concurrency.

        The manifest below carries a full `source` block — provider, item id and
        canonical URL. That is load-bearing rather than incidental: the cover is
        attached by matching the manifest against exactly one
        `ModelProvenanceSource`, so a manifest missing any of the three makes the
        attach refuse, and the test would then be counting the commits of a code
        path that never published a cover at all.
        """
        engine = create_engine(
            f"sqlite:///{tmp_path / 'finish-import.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        commits: list[object] = []

        @event.listens_for(engine, "commit")
        def _count_commit(_connection) -> None:
            commits.append(object())

        factory = SQLiteSessionFactory(engine)
        data = _png()
        with Session(engine) as setup:
            owner = build_user(setup, "finish-owner")
            model = build_model(
                setup,
                name="finish-model",
                slug=f"finish-{uuid.uuid4().hex}",
                hash=uuid.uuid4().hex * 2,
            )
            source = ModelProvenanceSource(
                model_id=model.id,
                provider="test",
                source_item_id="finish-item",
                canonical_url="https://example.test/finish",
                identity_key=uuid.uuid4().hex * 2,
            )
            row = InboxItem(
                owner_user_id=owner.id,
                source_kind=InboxSourceKind.BROWSER,
                source_url=source.canonical_url,
                state="importing",
                manifest_json=json.dumps(
                    {
                        "source": {
                            "provider": source.provider,
                            "source_item_id": source.source_item_id,
                            "canonical_url": source.canonical_url,
                        }
                    }
                ),
            )
            setup.add_all([source, row])
            setup.flush()
            slot = CaptureUploadSlot(
                id="finish-cover",
                inbox_item_id=row.id,
                role="cover",
                filename="cover.png",
                media_type="image/png",
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                state=CaptureUploadSlotState.UPLOADED,
                storage_key="opaque/slot-cover",
            )
            setup.add(slot)
            setup.commit()
            row_id = row.id
            model_id = model.id
            source_id = source.id
        commits.clear()

        backend = _backend()
        backend.source_cover_key.side_effect = lambda ident: f"opaque/cover/{ident}"
        backend.read_bytes.return_value = data
        processed = process_source_cover_upload(data, "image/png")
        backend.create_bytes.return_value = CreationReceipt(
            key=f"opaque/cover/{source_id}",
            size=len(processed.data),
            token="finish-cover",
            backend="fake",
            namespace="test",
        )
        job = type(
            "Job",
            (),
            {"state": "completed", "model_id": model_id, "result": None},
        )()
        monkeypatch.setattr(inbox.registry, "get", lambda _job_id: job)
        monkeypatch.setattr(inbox, "get_backend", lambda: backend)
        monkeypatch.setattr(inbox, "_record_v2_results", lambda *_args: (True, 1, 0))
        monkeypatch.setattr(inbox, "_cleanup_capture_slots", lambda *_args: True)

        inbox._finish_import(row_id, "finish-job", factory)

        # Cover, publication intent, proof finalization, then Inbox terminalization.
        assert len(commits) == 4
        with Session(engine) as check:
            finished = check.get(InboxItem, row_id)
            assert finished is not None
            assert finished.state.value == "completed"
            assert (
                check.exec(select(ModelSourceCover))
                .one()
                .storage_key.startswith("opaque/cover/")
            )
        engine.dispose()

    def test_a_trash_round_trip_leaves_the_cover_published(
        self, db_session: Session
    ) -> None:
        source = _source(db_session)
        model = db_session.get(Model, source.model_id)
        assert model is not None
        backend = get_backend()
        result = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png(),
            content_type="image/png",
        )
        db_session.commit()

        trash.soft_delete_model(db_session, model)
        assert db_session.get(ModelSourceCover, result.cover.id) is not None
        assert (
            db_session.exec(select(OwnedStorageObject)).one().key
            == result.cover.storage_key
        )
        trash.restore_model(db_session, model)
        assert db_session.get(ModelSourceCover, result.cover.id) is not None
        assert backend.read_bytes(result.cover.storage_key)

    def test_hard_delete_enqueues_one_required_proof_intent_for_cover(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        model = db_session.get(Model, source.model_id)
        assert model is not None
        backend = get_backend()
        result = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png(),
            content_type="image/png",
        )
        db_session.commit()
        # Read these before the delete. The cover row cascades away with its
        # provenance source, so reaching for `result.cover` afterwards raises
        # ObjectDeletedError rather than returning the values to compare against —
        # which is the point of the intent: it is the only thing that outlives the row.
        cover_key = result.cover.storage_key
        cover_id = result.cover.id

        trash.soft_delete_model(db_session, model)
        trash.hard_delete_model(db_session, model)
        db_session.commit()

        intents = db_session.exec(
            select(StorageDeleteIntent).where(
                StorageDeleteIntent.resource_kind == "model_source_cover"
            )
        ).all()
        assert len(intents) == 1
        assert intents[0].key == cover_key
        assert intents[0].resource_id == str(cover_id)


class TestReconcilePending:
    def test_discard_absent_cover_only_removes_current_provider_receipt(
        self, db_session: Session
    ) -> None:
        source = _source(db_session)
        db_session.commit()
        backend = get_backend()
        cover = ModelSourceCover(
            provenance_source_id=source.id,
            storage_key=backend.source_cover_key(source.id),
            size_bytes=4,
        )
        db_session.add(cover)
        db_session.flush()
        lease = staging_leases.create_cover_lease(
            db_session,
            model_source_cover_id=cover.id or 0,
            owner_user_id=None,
            destination_key=cover.storage_key,
            size_bytes=4,
            sha256="a" * 64,
        )
        namespace = backend.namespace_for(cover.storage_key)
        current_ref = provider_ref_for_backend(backend, namespace=namespace)
        for ref in (current_ref, "foreign-provider"):
            db_session.add(
                OwnedStorageObject(
                    backend=backend.backend_name,
                    namespace=namespace,
                    key=cover.storage_key,
                    object_kind="model_source_cover",
                    state=StorageObjectState.COMMITTED,
                    token=f"token-{ref}",
                    size_bytes=4,
                    provider_ref=ref,
                )
            )
        db_session.commit()

        assert (
            source_covers._discard_cover_if_absent(  # noqa: SLF001
                db_session, backend, cover=cover, lease=lease
            )
            is True
        )
        db_session.commit()
        remaining = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == cover.storage_key
            )
        ).all()
        assert [row.provider_ref for row in remaining] == ["foreign-provider"]

    def test_restart_reconciles_replacement_without_restoring_old_bytes(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        backend = get_backend()
        first = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png("navy"),
            content_type="image/png",
        )
        db_session.commit()
        replacement_data = _png("maroon")
        source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=replacement_data,
            content_type="image/png",
        )
        db_session.rollback()

        normalized = process_source_cover_upload(replacement_data, "image/png").data
        assert backend.read_bytes(first.cover.storage_key) == normalized
        assert source_covers.reconcile_pending(db_session, backend) == 1
        db_session.commit()

        cover = db_session.get(ModelSourceCover, first.cover.id)
        assert cover is not None
        assert cover.size_bytes == len(normalized)
        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert backend.creation_matches(
            CreationReceipt(
                key=proof.key,
                size=proof.size_bytes,
                token=proof.token,
                backend=proof.backend,
                namespace=proof.namespace,
                etag=proof.etag,
                version_id=proof.version_id,
                device=proof.device,
                inode=proof.inode,
                ctime_ns=proof.ctime_ns,
            )
        )

    def test_restart_reconcile_discards_unpublished_cover_intent(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        db_session.commit()
        cover = ModelSourceCover(
            provenance_source_id=source.id,
            storage_key="opaque/covers/restart-missing.webp",
            size_bytes=4,
        )
        db_session.add(cover)
        db_session.flush()
        lease = staging_leases.create_cover_lease(
            db_session,
            model_source_cover_id=cover.id or 0,
            owner_user_id=None,
            destination_key=cover.storage_key,
            size_bytes=4,
            sha256="d" * 64,
        )
        db_session.commit()
        backend = _backend()
        backend.adopt_existing.side_effect = NotImplementedError
        backend.object_info.return_value = None

        assert source_covers.reconcile_pending(db_session, backend) == 1
        db_session.commit()
        assert db_session.get(ModelSourceCover, cover.id) is None
        assert db_session.get(StagingLease, lease.id) is None


def _expired_cover_lease(session: Session):
    """A cover intent whose lease has already expired, ready for the reaper.

    The arrange step of every `expire_pending` case: a cover row, a lease pointing at
    its destination key, and an `expires_at` already in the past.
    """
    source = _source(session)
    session.commit()
    cover = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/pending.webp",
        size_bytes=4,
    )
    session.add(cover)
    session.flush()
    lease = staging_leases.create_cover_lease(
        session,
        model_source_cover_id=cover.id or 0,
        owner_user_id=None,
        destination_key=cover.storage_key,
        size_bytes=4,
        sha256="a" * 64,
    )
    lease.expires_at = lease.created_at
    session.commit()
    return cover, lease


class TestExpirePending:
    """Deciding an expired cover intent, when the backend is the only witness.

    Three outcomes, and getting them the wrong way round either deletes a cover a
    user can see or leaves an intent that never clears. The rule is that only an
    *explicit* answer from storage is allowed to be destructive: an object that is
    provably there is adopted, an object that is provably absent is discarded, and
    anything the backend will not answer for is left alone for the next sweep.
    """

    def test_adopts_a_cover_whose_bytes_were_published(
        self, db_session: Session
    ) -> None:
        cover, lease = _expired_cover_lease(db_session)
        backend = _backend()
        # The size has to match the lease: `_recover_pending_cover` compares them and
        # treats any disagreement as "this is not the object we staged", which is the
        # whole point of adopting by receipt rather than by key.
        backend.adopt_existing.return_value = CreationReceipt(
            key=cover.storage_key,
            size=lease.size_bytes,
            token="published",
            backend="fake",
            namespace="test",
        )

        assert source_covers.expire_pending(db_session, backend, lease=lease) is True

        db_session.flush()
        assert db_session.get(ModelSourceCover, cover.id) is not None
        assert db_session.get(StagingLease, lease.id) is None
        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert proof.key == cover.storage_key
        assert proof.token == "published"

    def test_local_legacy_receipt_remains_recoverable(
        self, db_session: Session
    ) -> None:
        cover, lease = _expired_cover_lease(db_session)
        lease.receipt_json = json.dumps(
            {
                "key": cover.storage_key,
                "size": lease.size_bytes,
                "token": "legacy-local",
                "backend": "fake",
                "namespace": "test",
            }
        )
        db_session.add(lease)
        db_session.commit()
        backend = _backend()
        backend.backend_name = "local"
        backend.provider_id = "local"
        backend.transport = "local"
        backend.creation_matches.return_value = True

        assert source_covers.expire_pending(db_session, backend, lease=lease) is True

        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert proof.provider_ref == provider_ref_for_backend(backend, namespace="test")

    def test_remote_legacy_receipt_fails_closed_without_a_storage_probe(
        self, db_session: Session
    ) -> None:
        cover, lease = _expired_cover_lease(db_session)
        lease.receipt_json = json.dumps(
            {
                "key": cover.storage_key,
                "size": lease.size_bytes,
                "token": "legacy-remote",
                "backend": "s3",
                "namespace": "bucket/prefix",
            }
        )
        db_session.add(lease)
        db_session.commit()
        backend = MagicMock(spec=StorageBackend)
        backend.backend_name = "s3"
        backend.provider_id = "s3"
        backend.transport = "s3"
        backend.namespace_for.return_value = "bucket/prefix"

        assert source_covers.expire_pending(db_session, backend, lease=lease) is False
        backend.creation_matches.assert_not_called()
        backend.adopt_existing.assert_not_called()
        backend.object_info.assert_not_called()

    def test_foreign_receipt_fails_closed_without_a_storage_probe(
        self, db_session: Session
    ) -> None:
        cover, lease = _expired_cover_lease(db_session)
        lease.receipt_json = json.dumps(
            {
                "key": cover.storage_key,
                "size": lease.size_bytes,
                "token": "foreign",
                "backend": "fake",
                "namespace": "test",
                "provider_ref": "foreign-provider",
            }
        )
        db_session.add(lease)
        db_session.commit()
        backend = _backend()

        assert source_covers.expire_pending(db_session, backend, lease=lease) is False
        backend.creation_matches.assert_not_called()
        backend.adopt_existing.assert_not_called()
        backend.object_info.assert_not_called()

    def test_leaves_everything_alone_when_the_backend_will_not_answer(
        self, db_session: Session
    ) -> None:
        # An unreachable object store is a retryable state, not evidence of absence.
        # Treating it as absence is how a sweep deletes a cover that *was* published.
        #
        # The fault goes through `creation_matches` rather than `adopt_existing`:
        # `_recover_pending_cover` catches the storage errors `adopt_existing` raises
        # and reports "absent", so only an exception escaping the recovery reaches the
        # retryable branch under test.
        cover, lease = _expired_cover_lease(db_session)
        lease.receipt_json = json.dumps(
            {
                "key": cover.storage_key,
                "size": lease.size_bytes,
                "token": "staged",
                "backend": "fake",
                "namespace": "test",
            }
        )
        db_session.add(lease)
        db_session.commit()
        backend = _backend()
        backend.creation_matches.side_effect = RuntimeError("object store unreachable")

        assert source_covers.expire_pending(db_session, backend, lease=lease) is False

        assert db_session.get(ModelSourceCover, cover.id) is not None
        assert db_session.get(StagingLease, lease.id) is not None


class TestPruneExpired:
    """The staging sweep meeting a cover intent it did not create.

    `staging_leases.prune_expired` is a generic reaper, and a cover's lease is
    only one of the things it walks. Its judgement about somebody else's bytes is
    what these rows pin: an intent with nothing published loses its cover and
    lease, and an intent whose storage does not match — or whose state it cannot
    be sure about — is left entirely alone, because a wrong guess here deletes a
    cover a user can see."""

    def test_prunes_a_cover_intent_whose_bytes_were_never_published(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        db_session.commit()
        cover = ModelSourceCover(
            provenance_source_id=source.id,
            storage_key="opaque/covers/not-published.webp",
            size_bytes=4,
        )
        db_session.add(cover)
        db_session.flush()
        lease = staging_leases.create_cover_lease(
            db_session,
            model_source_cover_id=cover.id or 0,
            owner_user_id=None,
            destination_key=cover.storage_key,
            size_bytes=4,
            sha256="a" * 64,
        )
        lease.expires_at = lease.created_at
        db_session.commit()
        backend = _backend()
        backend.adopt_existing.side_effect = NotImplementedError
        backend.object_info.return_value = None

        assert staging_leases.prune_expired(
            db_session, now=lease.expires_at, backend=backend
        ) == (1, 0)
        assert db_session.get(ModelSourceCover, cover.id) is None
        assert db_session.get(StagingLease, lease.id) is None
        backend.rollback_create.assert_not_called()

    @pytest.mark.parametrize(
        "object_info",
        [StorageObjectInfo(size=7), RuntimeError("storage unavailable")],
    )
    def test_expired_cover_intent_never_deletes_mismatched_or_uncertain_storage(
        self, db_session: Session, object_info: object
    ) -> None:
        source = _source(db_session)
        db_session.commit()
        cover = ModelSourceCover(
            provenance_source_id=source.id,
            storage_key="opaque/covers/foreign.webp",
            size_bytes=4,
        )
        db_session.add(cover)
        db_session.flush()
        lease = staging_leases.create_cover_lease(
            db_session,
            model_source_cover_id=cover.id or 0,
            owner_user_id=None,
            destination_key=cover.storage_key,
            size_bytes=4,
            sha256="b" * 64,
        )
        lease.expires_at = lease.created_at
        db_session.commit()
        backend = _backend()
        backend.adopt_existing.side_effect = NotImplementedError
        if isinstance(object_info, Exception):
            backend.object_info.side_effect = object_info
        else:
            backend.object_info.return_value = object_info

        assert staging_leases.prune_expired(
            db_session, now=lease.expires_at, backend=backend
        ) == (0, 0)
        assert db_session.get(ModelSourceCover, cover.id) is not None
        assert db_session.get(StagingLease, lease.id) is not None
        backend.rollback_create.assert_not_called()

    def test_expired_replacement_intent_keeps_existing_cover_when_old_bytes_remain(
        self,
        db_session: Session,
    ) -> None:
        source = _source(db_session)
        db_session.commit()
        cover = ModelSourceCover(
            provenance_source_id=source.id,
            storage_key="opaque/covers/replacement.webp",
            size_bytes=3,
        )
        db_session.add(cover)
        db_session.flush()
        old = _receipt(key=cover.storage_key, token="old")
        record_creation(db_session, old, object_kind="model_source_cover")
        lease = staging_leases.create_cover_lease(
            db_session,
            model_source_cover_id=cover.id or 0,
            owner_user_id=None,
            destination_key=cover.storage_key,
            size_bytes=5,
            sha256="c" * 64,
        )
        lease.expires_at = lease.created_at
        db_session.commit()
        backend = _backend()
        backend.adopt_existing.side_effect = NotImplementedError
        backend.object_info.return_value = StorageObjectInfo(size=3)

        assert staging_leases.prune_expired(
            db_session, now=lease.expires_at, backend=backend
        ) == (0, 0)
        assert db_session.get(ModelSourceCover, cover.id) is not None
        assert db_session.get(StagingLease, lease.id) is not None
