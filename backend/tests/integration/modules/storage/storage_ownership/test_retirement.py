"""Publication retirement must fence adoption without poisoning canonical keys."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.db.models import OwnedStorageObject, StorageDeleteIntent, StorageObjectState
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StorageCollisionError,
)
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_deletion import enqueue_prevalidated_receipt
from app.modules.storage.storage_ownership import (
    abandon_publication,
    adopt_publication,
    complete_publication,
    prepare_bytes,
    provider_ref_for_backend,
    publish_bytes,
    record_creation,
    reserve_creation,
    reserve_publication,
    sweep_orphaned_publications,
)

NOW = datetime(2026, 1, 3, tzinfo=UTC)
STALE = datetime(2026, 1, 1, tzinfo=UTC)


class TestRecordCreation:
    def test_rejects_an_exact_receipt_after_orphan_retirement(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(601)
        receipt = publish_bytes(
            db_session, backend, key, b"retired", object_kind="thumbnail"
        )
        db_session.rollback()
        proof = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        proof.created_at = STALE
        db_session.add(proof)
        db_session.commit()
        sweep_orphaned_publications(db_session, backend, now=NOW)
        db_session.commit()

        assert (
            db_session.exec(
                select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
            )
            .one()
            .status
            == "completed"
        )

        with pytest.raises(RuntimeError, match="retired|revoked"):
            record_creation(db_session, receipt, object_kind="thumbnail")

    def test_preserves_legacy_creation_without_a_reservation(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(602)
        receipt = backend.create_bytes(b"legacy creation", key)

        record_creation(db_session, receipt, object_kind="thumbnail")
        db_session.commit()

        proof = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert proof.state is StorageObjectState.COMMITTED
        assert backend.read_bytes(key) == b"legacy creation"


class TestReserveCreation:
    def test_reuses_a_canonical_locator_with_a_distinct_reservation(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(603)
        old_id = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=3
        )
        proof = db_session.get(OwnedStorageObject, old_id.id)
        assert proof is not None
        proof.created_at = STALE
        db_session.add(proof)
        db_session.commit()
        sweep_orphaned_publications(db_session, backend, now=NOW)
        db_session.commit()

        new_id = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=3
        )

        assert new_id.id != old_id.id
        assert new_id.generation != old_id.generation
        db_session.expire_all()
        old_proof = db_session.get(OwnedStorageObject, old_id.id)
        assert old_proof is not None
        assert old_proof.state.value == "retiring"
        new_proof = db_session.get(OwnedStorageObject, new_id.id)
        assert new_proof is not None
        assert new_proof.state is StorageObjectState.PENDING


class TestCompletePublication:
    def test_retires_a_late_receipt_without_adopting_a_new_reservation(
        self, db_session
    ):
        backend = get_backend()
        key = backend.thumbnail_key(604)
        old_id = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=3
        )
        proof = db_session.get(OwnedStorageObject, old_id.id)
        assert proof is not None
        proof.created_at = STALE
        db_session.add(proof)
        db_session.commit()
        sweep_orphaned_publications(db_session, backend, now=NOW)
        db_session.commit()
        new_id = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=3
        )
        receipt = backend.create_bytes(b"old", key)

        with pytest.raises(RuntimeError, match="retired|revoked"):
            complete_publication(
                db_session,
                old_id,
                receipt,
                object_kind="thumbnail",
                sha256=hashlib.sha256(b"old").hexdigest(),
            )

        db_session.rollback()
        new_proof = db_session.get(OwnedStorageObject, new_id.id)
        assert new_proof is not None
        assert new_proof.state is StorageObjectState.PENDING
        assert new_proof.token is None


class TestIncompleteRetirement:
    def test_retains_evidence_when_receipt_recovery_is_deferred(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(610)
        reservation = reserve_creation(
            db_session,
            backend,
            key,
            object_kind="thumbnail",
            expected_size=4,
            sha256=hashlib.sha256(b"late").hexdigest(),
        )
        row = db_session.get(OwnedStorageObject, reservation.id)
        row.created_at = STALE
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=NOW)

        db_session.refresh(row)
        assert result.deferred == 1
        assert row.state is StorageObjectState.RETIRING
        assert row.publication_generation == reservation.generation
        assert row.sha256 == hashlib.sha256(b"late").hexdigest()
        assert row.next_recovery_at is not None
        assert row.token is None

    def test_does_not_infer_old_ownership_from_new_reservation_bytes(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(611)
        digest = hashlib.sha256(b"same").hexdigest()
        old = reserve_creation(
            db_session,
            backend,
            key,
            object_kind="thumbnail",
            expected_size=4,
            sha256=digest,
        )
        row = db_session.get(OwnedStorageObject, old.id)
        row.created_at = STALE
        db_session.add(row)
        db_session.commit()
        sweep_orphaned_publications(db_session, backend, now=NOW)
        fresh = prepare_bytes(
            db_session, backend, key, b"same", object_kind="thumbnail", sha256=digest
        )

        result = sweep_orphaned_publications(
            db_session, backend, now=datetime(2026, 1, 3, 2, tzinfo=UTC)
        )

        assert result.deferred == 1
        assert backend.read_bytes(key) == b"same"
        assert not db_session.exec(
            select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
        ).all()
        adopt_publication(db_session, fresh)
        db_session.commit()
        db_session.refresh(row)
        assert row.token is None

    def test_legacy_transfer_wins_before_late_creator_cleanup(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(612)
        old = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=4
        )
        row = db_session.get(OwnedStorageObject, old.id)
        row.created_at = STALE
        db_session.add(row)
        db_session.commit()
        sweep_orphaned_publications(db_session, backend, now=NOW)
        receipt = backend.create_bytes(b"late", key)
        record_creation(
            db_session,
            receipt,
            object_kind="thumbnail",
            provider_ref=provider_ref_for_backend(backend, namespace=receipt.namespace),
        )
        db_session.commit()

        with pytest.raises(RuntimeError, match="retired"):
            complete_publication(
                db_session, old, receipt, object_kind="thumbnail", sha256=None
            )

        assert backend.read_bytes(key) == b"late"
        db_session.expire_all()
        proofs = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).all()
        assert sum(row.state is StorageObjectState.COMMITTED for row in proofs) == 1
        assert not db_session.exec(
            select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
        ).all()

    def test_late_creator_cleanup_commits_even_when_caller_rolls_back(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(613)
        old = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=4
        )
        row = db_session.get(OwnedStorageObject, old.id)
        row.created_at = STALE
        db_session.add(row)
        db_session.commit()
        sweep_orphaned_publications(db_session, backend, now=NOW)
        receipt = backend.create_bytes(b"late", key)

        with pytest.raises(RuntimeError, match="retired"):
            complete_publication(
                db_session, old, receipt, object_kind="thumbnail", sha256=None
            )
        db_session.rollback()

        intent = db_session.exec(
            select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
        ).one()
        assert intent.status == "pending"
        with pytest.raises(RuntimeError, match="revoked"):
            record_creation(
                db_session,
                receipt,
                object_kind="thumbnail",
                provider_ref=old.provider_ref,
            )


class TestPreparedPublication:
    def test_adoption_performs_no_backend_io(self, db_session, monkeypatch):
        backend = get_backend()
        candidate = prepare_bytes(
            db_session,
            backend,
            backend.thumbnail_key(614),
            b"ready",
            object_kind="thumbnail",
        )

        def forbidden(*args, **kwargs):
            raise AssertionError("backend I/O during SQL adoption")

        for method in (
            "exists",
            "object_info",
            "creation_matches",
            "adopt_existing",
            "read_bytes",
            "rollback_create",
        ):
            monkeypatch.setattr(backend, method, forbidden)

        proof = adopt_publication(db_session, candidate)
        db_session.commit()

        assert proof.state is StorageObjectState.COMMITTED
        assert proof.publication_generation == candidate.reservation.generation

    def test_old_handle_cannot_adopt_after_database_id_reuse(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(615)
        old = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=3
        )
        row = db_session.get(OwnedStorageObject, old.id)
        db_session.delete(row)
        db_session.commit()
        fresh = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=3
        )
        receipt = backend.create_bytes(b"old", key)
        assert fresh.id == old.id
        assert fresh.generation != old.generation

        with pytest.raises(RuntimeError, match="retired"):
            complete_publication(
                db_session, old, receipt, object_kind="thumbnail", sha256=None
            )

        db_session.expire_all()
        current = db_session.get(OwnedStorageObject, fresh.id)
        assert current.state is StorageObjectState.PENDING
        assert current.token is None


class TestReservePublication:
    def test_retry_retains_the_original_creation_authority(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(620)
        ref = provider_ref_for_backend(backend, namespace=backend.namespace_for(key))
        prior = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=3
        )
        db_session.commit()

        current = reserve_publication(
            db_session,
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            provider_ref=ref,
            object_kind="thumbnail",
            expected_size=3,
            previous=prior,
        )
        db_session.commit()
        late = backend.create_bytes(b"old", key)
        with pytest.raises(RuntimeError, match="retired"):
            complete_publication(
                db_session, prior, late, object_kind="thumbnail", sha256=None
            )
        db_session.rollback()

        old = db_session.get(OwnedStorageObject, prior.id)
        fresh = db_session.get(OwnedStorageObject, current.id)
        assert old.state is StorageObjectState.RETIRING
        assert old.token == late.token
        assert fresh.state is StorageObjectState.PENDING
        assert fresh.token is None
        assert fresh.publication_generation != old.publication_generation
        assert (
            db_session.exec(
                select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
            )
            .one()
            .token
            == late.token
        )

    def test_refuses_a_retry_observation_that_an_adopter_replaced(self, db_session):
        backend = get_backend()
        key = backend.thumbnail_key(621)
        prior = reserve_creation(
            db_session, backend, key, object_kind="thumbnail", expected_size=3
        )
        receipt = backend.create_bytes(b"new", key)
        complete_publication(
            db_session, prior, receipt, object_kind="thumbnail", sha256=None
        )
        db_session.commit()
        backend.rollback_create(receipt)
        candidate = prepare_bytes(
            db_session, backend, key, b"newer", object_kind="thumbnail"
        )
        adopt_publication(db_session, candidate)
        db_session.commit()

        with pytest.raises(StorageCollisionError):
            reserve_publication(
                db_session,
                backend=backend.backend_name,
                namespace=backend.namespace_for(key),
                key=key,
                provider_ref=candidate.reservation.provider_ref,
                object_kind="thumbnail",
                previous=prior,
            )
        db_session.rollback()

        assert backend.read_bytes(key) == b"newer"
        assert (
            db_session.get(OwnedStorageObject, candidate.reservation.id).state
            is StorageObjectState.COMMITTED
        )


class TestAbandonPublication:
    def test_retires_an_unadopted_candidate_before_cleanup(self, db_session):
        from app.modules.storage.storage_deletion import process_storage_delete_intents

        backend = get_backend()
        key = backend.thumbnail_key(622)
        candidate = prepare_bytes(
            db_session, backend, key, b"unused", object_kind="thumbnail"
        )
        db_session.rollback()

        assert abandon_publication(db_session, candidate)
        process_storage_delete_intents(backend=backend)

        db_session.expire_all()
        assert not backend.exists(key)
        assert (
            db_session.get(OwnedStorageObject, candidate.reservation.id).state
            is StorageObjectState.RETIRING
        )
        assert (
            db_session.exec(
                select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
            )
            .one()
            .status
            == "completed"
        )

    def test_preserves_a_candidate_committed_by_a_concurrent_owner(self, db_session):
        from sqlmodel import Session

        backend = get_backend()
        key = backend.thumbnail_key(623)
        candidate = prepare_bytes(
            db_session, backend, key, b"adopted", object_kind="thumbnail"
        )
        db_session.rollback()
        with Session(bind=db_session.get_bind()) as adopter:
            adopt_publication(adopter, candidate)
            adopter.commit()

        assert not abandon_publication(db_session, candidate)

        db_session.expire_all()
        assert backend.read_bytes(key) == b"adopted"
        assert (
            db_session.get(OwnedStorageObject, candidate.reservation.id).state
            is StorageObjectState.COMMITTED
        )
        assert not db_session.exec(
            select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
        ).all()


class TestSweepFairness:
    def test_unresolved_history_does_not_starve_the_next_pending_candidate(
        self, db_session
    ):
        backend = get_backend()
        old = reserve_creation(
            db_session,
            backend,
            backend.thumbnail_key(624),
            object_kind="thumbnail",
            expected_size=3,
        )
        candidate = prepare_bytes(
            db_session,
            backend,
            backend.thumbnail_key(625),
            b"orphan",
            object_kind="thumbnail",
        )
        prior = db_session.get(OwnedStorageObject, old.id)
        orphan = db_session.get(OwnedStorageObject, candidate.reservation.id)
        prior.created_at = STALE
        orphan.created_at = STALE
        db_session.add(prior)
        db_session.add(orphan)
        db_session.commit()
        first = sweep_orphaned_publications(db_session, backend, now=NOW, limit=1)

        second = sweep_orphaned_publications(db_session, backend, now=NOW, limit=1)

        db_session.expire_all()
        assert first.deferred == 1
        assert second.reclaimed == 1
        assert not backend.exists(candidate.receipt.key)
        assert db_session.get(OwnedStorageObject, old.id).token is None
        assert db_session.get(OwnedStorageObject, old.id).next_recovery_at is not None


class TestCanonicalReadoption:
    def test_equal_new_bytes_at_a_revoked_locator_have_independent_authority(
        self, db_session
    ):
        backend = get_backend()
        key = backend.thumbnail_key(626)
        old = prepare_bytes(db_session, backend, key, b"same", object_kind="thumbnail")
        history = db_session.get(OwnedStorageObject, old.reservation.id)
        history.created_at = STALE
        db_session.add(history)
        db_session.commit()
        sweep_orphaned_publications(db_session, backend, now=NOW)
        db_session.commit()
        fresh_receipt = backend.create_bytes(b"same", key)

        record_creation(db_session, fresh_receipt, object_kind="thumbnail")
        db_session.commit()

        current = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == key,
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
            )
        ).one()
        retired = db_session.get(OwnedStorageObject, old.reservation.id)
        intent = db_session.exec(
            select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
        ).one()
        assert current.token == fresh_receipt.token
        assert retired.token == old.receipt.token
        assert current.token != retired.token
        assert intent.token == retired.token
        assert intent.status == "completed"
        assert backend.read_bytes(key) == b"same"


class TestDeleteOwnedKey:
    def test_keeps_exact_revocation_after_immediate_deletion(self, db_session):
        from app.modules.storage.storage_ownership import delete_owned_key

        backend = get_backend()
        key = backend.thumbnail_key(633)
        receipt = publish_bytes(
            db_session, backend, key, b"delete", object_kind="thumbnail"
        )
        db_session.commit()

        assert delete_owned_key(db_session, backend, key)
        db_session.commit()

        proof = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert proof.state is StorageObjectState.RETIRING
        intent = db_session.exec(
            select(StorageDeleteIntent).where(StorageDeleteIntent.key == key)
        ).one()
        assert intent.status == "completed"
        assert not backend.exists(key)
        with pytest.raises(RuntimeError, match="retired|revoked"):
            record_creation(db_session, receipt, object_kind="thumbnail")


class TestReconcileBackupCaches:
    def test_stale_failure_cannot_block_a_concurrent_success(
        self, db_session, monkeypatch
    ):
        from sqlmodel import Session

        from app.core.config import settings
        from app.modules.backups.backup.caches import reconcile_backup_caches
        from app.modules.storage.storage_backend.local import LocalStorageBackend

        backend = LocalStorageBackend()
        key = str(settings.backup_dir / ".cloud-cache" / "race.zip")
        candidate = prepare_bytes(
            db_session,
            backend,
            key,
            b"cache",
            object_kind="backup-cloud-cache",
            sha256=hashlib.sha256(b"cache").hexdigest(),
        )
        db_session.rollback()
        original = LocalStorageBackend.adopt_existing

        def adopted_then_failed(owner, *args, **kwargs):
            original(owner, *args, **kwargs)
            with Session(bind=db_session.get_bind()) as winner:
                adopt_publication(winner, candidate)
                winner.commit()
            raise RuntimeError("late_backup_probe_failure")

        monkeypatch.setattr(LocalStorageBackend, "adopt_existing", adopted_then_failed)
        reconcile_backup_caches()
        db_session.expire_all()
        proof = db_session.get(OwnedStorageObject, candidate.reservation.id)
        assert proof.state is StorageObjectState.COMMITTED
        assert proof.last_error is None
        assert backend.read_bytes(key) == b"cache"


class TestRemotePhysicalGeneration:
    @pytest.mark.parametrize("backend_name", ["backup-s3", "backup-opendal-s3"])
    def test_new_logical_token_cannot_readopt_a_revoked_immutable_version(
        self, db_session, backend_name
    ):
        receipt = CreationReceipt(
            key="backups/archive.tar.gz",
            size=3,
            token="creator-token",
            backend=backend_name,
            namespace="bucket/backups",
            provider_ref="saved-provider",
            etag='"same-content"',
            version_id="v1",
        )
        proof = record_creation(db_session, receipt, object_kind="backup")
        proof.state = StorageObjectState.RETIRING
        db_session.add(proof)
        enqueue_prevalidated_receipt(
            db_session, receipt, object_kind="backup", sha256=None
        )
        db_session.commit()

        with pytest.raises(RuntimeError, match="revoked|retired"):
            record_creation(
                db_session,
                replace(receipt, token="legacy-adoption-token"),
                object_kind="backup",
            )
        db_session.rollback()
        assert (
            db_session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.state == StorageObjectState.COMMITTED,
                    OwnedStorageObject.key == receipt.key,
                )
            ).first()
            is None
        )

    def test_new_immutable_version_can_reuse_the_retired_locator(self, db_session):
        receipt = CreationReceipt(
            key="backups/archive.tar.gz",
            size=3,
            token="creator-token",
            backend="backup-opendal-s3",
            namespace="bucket/backups",
            provider_ref="saved-provider",
            etag='"same-content"',
            version_id="v1",
        )
        old = record_creation(db_session, receipt, object_kind="backup")
        old.state = StorageObjectState.RETIRING
        db_session.add(old)
        enqueue_prevalidated_receipt(
            db_session, receipt, object_kind="backup", sha256=None
        )
        db_session.commit()

        fresh = record_creation(
            db_session, replace(receipt, version_id="v2"), object_kind="backup"
        )
        db_session.commit()
        assert fresh.state is StorageObjectState.COMMITTED
        assert fresh.version_id == "v2"
        assert fresh.publication_generation != old.publication_generation
