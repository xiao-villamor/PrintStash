"""Direct safety coverage for the storage ownership ledger seam."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.time import utcnow
from app.db.models import OwnedStorageObject, StorageDeleteIntent, StorageObjectState
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    ObjectIdentity,
    StorageCapabilities,
)
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_ownership import (
    UnsafeStorageDeleteError,
    delete_owned_key,
    provider_ref_for_backend,
    record_creation,
    replace_owned_bytes,
    require_or_adopt_legacy_artifact,
    require_owned_key,
    sweep_orphaned_publications,
)


def _receipt(key: str = "files/model.stl") -> CreationReceipt:
    return CreationReceipt(
        key=key,
        size=4,
        token="token-1",
        backend="local",
        namespace="data:/tmp/vault",
        etag="etag-1",
        device=1,
        inode=2,
        ctime_ns=3,
        provider_ref=provider_ref_for_backend(
            _LedgerBackend(), namespace="data:/tmp/vault"
        ),
    )


class _LedgerBackend:
    backend_name = "local"
    capabilities = StorageCapabilities(
        conditional_create=True,
        object_identity=ObjectIdentity.INODE,
        verified_delete=True,
        conditional_replace=True,
        namespace_ownership=True,
        direct_path=True,
    )

    def namespace_for(self, key: str) -> str:
        return "data:/tmp/vault"

    def exists(self, key: str) -> bool:
        return not self.rollback

    def __init__(self) -> None:
        self.matches = True
        self.adopted = False
        self.fail_adopt = False
        self.replaced: list[tuple[bytes, CreationReceipt]] = []
        self.matched: list[CreationReceipt] = []
        self.rollback_calls: list[CreationReceipt] = []
        self.rollback: bool | Exception = True
        self.replacement: CreationReceipt | None = None

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        self.matched.append(receipt)
        return self.matches

    def adopt_existing(
        self, key: str, *, expected_size: int, expected_sha256: str
    ) -> CreationReceipt:
        del expected_size, expected_sha256
        if self.fail_adopt:
            raise OSError("cannot prove legacy bytes")
        self.adopted = True
        return replace(_receipt(key), token="adopted-token")

    def replace_bytes(self, data: bytes, receipt: CreationReceipt) -> CreationReceipt:
        self.replaced.append((data, receipt))
        return self.replacement or replace(
            receipt,
            token="replacement-token",
            size=len(data),
            etag="replacement-etag",
            version_id="replacement-version",
            device=11,
            inode=22,
            ctime_ns=33,
        )

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        self.rollback_calls.append(receipt)
        if isinstance(self.rollback, Exception):
            raise self.rollback
        return self.rollback


class TestRecordCreation:
    def test_record_creation_refreshes_an_existing_locator(self, db_session) -> None:
        initial = _receipt()
        first = record_creation(db_session, initial, object_kind="artifact")
        db_session.commit()
        db_session.refresh(first)

        refreshed_receipt = replace(
            initial,
            token="refreshed-token",
            size=9,
            etag="refreshed-etag",
            version_id="refreshed-version",
            device=101,
            inode=202,
            ctime_ns=303,
        )
        refreshed = record_creation(
            db_session,
            refreshed_receipt,
            object_kind="thumbnail",
        )
        db_session.commit()
        db_session.refresh(refreshed)

        assert refreshed.id != first.id
        assert refreshed.publication_generation != first.publication_generation
        db_session.refresh(first)
        assert first.state is StorageObjectState.RETIRING
        assert first.token == initial.token
        assert refreshed.backend == refreshed_receipt.backend
        assert refreshed.namespace == refreshed_receipt.namespace
        assert refreshed.key == refreshed_receipt.key
        assert refreshed.object_kind == "thumbnail"
        assert refreshed.token == refreshed_receipt.token
        assert refreshed.size_bytes == refreshed_receipt.size
        assert refreshed.etag == refreshed_receipt.etag
        assert refreshed.version_id == refreshed_receipt.version_id
        assert refreshed.device == refreshed_receipt.device
        assert refreshed.inode == refreshed_receipt.inode
        assert refreshed.ctime_ns == refreshed_receipt.ctime_ns

    def test_receipt_upgrade_reuses_exact_quarantined_row_without_overwrite(
        self, db_session, make_owned_storage_object
    ) -> None:
        digest = hashlib.sha256(b"legacy backup").hexdigest()
        row = make_owned_storage_object(
            backend="backup-s3",
            namespace="archive-bucket/nexus3d-backups/",
            key="nexus3d-backups/nexus3d-backup-20260101-legacy.tar.gz",
            object_kind="backup-legacy",
            state=StorageObjectState.BLOCKED,
            token="legacy-digest-token",
            size_bytes=len(b"legacy backup"),
            sha256=None,
            provider_ref=None,
            etag=None,
            version_id=None,
            last_error="backup_s3_adoption_required",
        )
        db_session.commit()
        row_id = row.id
        receipt = CreationReceipt(
            key=row.key,
            size=row.size_bytes or 0,
            token="immutable-digest-token",
            backend=row.backend,
            namespace=row.namespace,
            etag='"legacy-etag"',
            version_id="legacy-version",
        )

        upgraded = record_creation(
            db_session,
            receipt,
            object_kind="backup-legacy",
            sha256=digest,
            provider_ref="p" * 64,
            upgrade_provider_ref=True,
        )
        db_session.commit()
        db_session.refresh(upgraded)

        assert upgraded.id != row_id
        db_session.refresh(row)
        assert row.state is StorageObjectState.RETIRING
        assert row.token == "legacy-digest-token"
        assert upgraded.state is StorageObjectState.COMMITTED
        assert upgraded.key == row.key
        assert upgraded.namespace == row.namespace
        assert upgraded.sha256 == digest
        assert upgraded.provider_ref == "p" * 64
        assert upgraded.etag == '"legacy-etag"'
        assert upgraded.version_id == "legacy-version"

    def test_receipt_recording_does_not_overwrite_a_locator_from_another_provider(
        self, db_session, make_owned_storage_object
    ) -> None:
        row = make_owned_storage_object(
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key="printstash-backups/exact.tar.gz",
            object_kind="backup",
            provider_ref="a" * 64,
            sha256="a" * 64,
            etag='"a"',
            version_id="version-a",
        )
        db_session.commit()
        replacement = CreationReceipt(
            key=row.key,
            size=1,
            token="replacement",
            backend=row.backend,
            namespace=row.namespace,
            etag='"b"',
            version_id="version-b",
        )

        with pytest.raises(
            UnsafeStorageDeleteError, match="provider_identity_mismatch"
        ):
            record_creation(
                db_session,
                replacement,
                object_kind="backup",
                sha256="b" * 64,
                provider_ref="b" * 64,
            )

        db_session.rollback()
        untouched = db_session.get(OwnedStorageObject, row.id)
        assert untouched is not None
        assert untouched.provider_ref == "a" * 64
        assert untouched.sha256 == "a" * 64
        assert untouched.version_id == "version-a"


class TestRequireOwnedKey:
    def test_require_owned_key_reports_each_failure_distinctly(
        self,
        db_session,
    ) -> None:
        backend = _LedgerBackend()
        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            require_owned_key(db_session, backend, "unclaimed")

        stored = _receipt()
        record_creation(db_session, stored, object_kind="artifact")
        db_session.commit()
        backend.matches = True
        require_owned_key(db_session, backend, stored.key)
        assert backend.matched[-1] == stored

        backend.matches = False
        with pytest.raises(UnsafeStorageDeleteError, match="no_longer_matches_receipt"):
            require_owned_key(db_session, backend, stored.key)
        assert backend.matched[-1] == stored

        class ExplodingBackend(_LedgerBackend):
            def creation_matches(self, receipt: CreationReceipt) -> bool:
                del receipt
                raise RuntimeError("probe failed")

        with pytest.raises(UnsafeStorageDeleteError, match="verification_failed"):
            require_owned_key(db_session, ExplodingBackend(), _receipt().key)

    def test_require_owned_key_rejects_an_incomplete_creation_receipt(
        self, db_session
    ) -> None:
        backend = _LedgerBackend()
        incomplete = replace(_receipt(), token=None)
        record_creation(db_session, incomplete, object_kind="artifact")
        db_session.commit()

        with pytest.raises(UnsafeStorageDeleteError, match="verification_failed"):
            require_owned_key(db_session, backend, incomplete.key)


class TestRequireOrAdoptLegacyArtifact:
    def test_require_or_adopt_legacy_artifact_only_claims_untracked_matching_bytes(
        self,
        db_session,
    ) -> None:
        backend = _LedgerBackend()
        require_or_adopt_legacy_artifact(
            db_session,
            backend,
            "files/legacy.stl",
            expected_size=4,
            expected_sha256="sha256",
        )
        db_session.commit()
        assert backend.adopted is True
        row = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == "files/legacy.stl"
            )
        ).one()
        assert row.object_kind == "legacy_artifact"

        # Existing rows are verified, never silently replaced by adoption.
        backend.matches = True
        require_or_adopt_legacy_artifact(
            db_session,
            backend,
            "files/legacy.stl",
            expected_size=4,
            expected_sha256="different-sha",
        )

        backend.fail_adopt = True
        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            require_or_adopt_legacy_artifact(
                db_session,
                backend,
                "files/unverifiable.stl",
                expected_size=4,
                expected_sha256="sha256",
            )


class TestReplaceOwnedBytes:
    def test_replace_owned_bytes_swaps_the_proof_with_the_bytes(
        self,
        db_session,
    ) -> None:
        backend = _LedgerBackend()
        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            replace_owned_bytes(
                db_session, backend, "unclaimed", b"bytes", object_kind="thumbnail"
            )

        stored = _receipt()
        record_creation(db_session, stored, object_kind="artifact")
        db_session.commit()
        backend.matches = False
        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            replace_owned_bytes(
                db_session, backend, stored.key, b"bytes", object_kind="thumbnail"
            )

        backend.matches = True
        replacement_receipt = replace(
            stored,
            token="replacement-token",
            size=9,
            etag="replacement-etag",
            version_id="replacement-version",
            device=11,
            inode=22,
            ctime_ns=33,
        )
        backend.replacement = replacement_receipt
        replacement = replace_owned_bytes(
            db_session, backend, stored.key, b"new-bytes", object_kind="thumbnail"
        )
        db_session.commit()
        assert backend.matched[-1] == stored
        assert backend.replaced[0][1] == stored
        assert replacement == replacement_receipt
        assert backend.replaced[0][0] == b"new-bytes"
        row = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == stored.key,
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
            )
        ).one()
        assert row.object_kind == "thumbnail"
        assert row.backend == replacement_receipt.backend
        assert row.namespace == replacement_receipt.namespace
        assert row.key == replacement_receipt.key
        assert row.size_bytes == replacement_receipt.size
        assert row.token == replacement_receipt.token
        assert row.etag == replacement_receipt.etag
        assert row.version_id == replacement_receipt.version_id
        assert row.device == replacement_receipt.device
        assert row.inode == replacement_receipt.inode
        assert row.ctime_ns == replacement_receipt.ctime_ns
        assert row.sha256 == hashlib.sha256(b"new-bytes").hexdigest()


class TestDeleteOwnedKey:
    @pytest.mark.parametrize(
        ("required", "rollback", "expected"),
        [
            (False, True, True),
            (False, False, False),
            (True, True, True),
        ],
    )
    def test_delete_owned_key_distinguishes_a_real_delete_from_a_no_op(
        self, db_session, required: bool, rollback: bool, expected: bool
    ) -> None:
        backend = _LedgerBackend()
        stored = _receipt()
        record_creation(db_session, stored, object_kind="artifact")
        db_session.commit()
        backend.rollback = rollback

        assert (
            delete_owned_key(db_session, backend, stored.key, required_proof=required)
            is expected
        )
        db_session.commit()
        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert proof.key == stored.key
        assert proof.state is StorageObjectState.RETIRING
        assert proof.token == stored.token
        intent = db_session.exec(select(StorageDeleteIntent)).one()
        assert intent.token == stored.token
        assert intent.status == ("completed" if expected else "blocked")
        assert backend.rollback_calls[-1] == stored

    def test_delete_owned_key_fails_closed_only_when_proof_is_required(
        self, db_session
    ) -> None:
        backend = _LedgerBackend()
        stored = _receipt()
        record_creation(db_session, stored, object_kind="artifact")
        db_session.commit()
        backend.rollback = OSError("delete failed")

        assert delete_owned_key(db_session, backend, stored.key) is False
        required = _receipt("files/required.stl")
        record_creation(db_session, required, object_kind="artifact")
        db_session.commit()
        with pytest.raises(UnsafeStorageDeleteError, match="storage_delete_failed"):
            delete_owned_key(db_session, backend, required.key, required_proof=True)
        assert backend.rollback_calls[-1] == required
        intents = db_session.exec(select(StorageDeleteIntent)).all()
        assert len(intents) == 2
        assert all(intent.status == "retry" for intent in intents)
        assert all(intent.last_error == "OSError" for intent in intents)

        assert delete_owned_key(db_session, backend, "unclaimed") is False
        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            delete_owned_key(db_session, backend, "unclaimed", required_proof=True)

    def test_delete_owned_key_rejects_a_nonmatching_required_proof(
        self, db_session
    ) -> None:
        backend = _LedgerBackend()
        stored = _receipt()
        record_creation(db_session, stored, object_kind="artifact")
        db_session.commit()
        backend.rollback = False

        with pytest.raises(UnsafeStorageDeleteError, match="no_longer_matches_receipt"):
            delete_owned_key(db_session, backend, stored.key, required_proof=True)
        assert backend.rollback_calls[-1] == stored


class TestSweepOrphanedPublications:
    @pytest.mark.parametrize(
        "object_kind",
        [
            pytest.param("backup", id="backup"),
            pytest.param("backup-legacy", id="backup-legacy"),
            pytest.param("backup-cloud-cache", id="backup-cloud-cache"),
        ],
    )
    def test_sweep_leaves_backup_publications_for_their_owner(
        self, db_session, make_owned_storage_object, object_kind: str
    ) -> None:
        row = make_owned_storage_object(
            backend="local",
            namespace="local/test",
            key=f"backups/{object_kind}.tar.gz",
            object_kind=object_kind,
            state=StorageObjectState.PENDING,
            created_at=utcnow() - timedelta(days=2),
        )
        db_session.commit()

        result = sweep_orphaned_publications(
            db_session,
            LocalStorageBackend(),
            now=utcnow(),
        )

        assert result.examined == 1
        assert result.pending == 1
        assert result.reclaimed == 0
        db_session.refresh(row)
        assert row.state is StorageObjectState.PENDING


class TestOrphanAdoptionProbe:
    def test_preserves_a_concurrently_adopted_thumbnail(
        self, db_session, make_model, make_file, monkeypatch
    ) -> None:
        from sqlmodel import Session

        from app.db.models import File
        from app.modules.media.thumbnail_publication import point_at
        from app.modules.storage.storage_backend.runtime import get_backend
        from app.modules.storage.storage_ownership import publish_bytes

        artifact = make_file(make_model(), filename="pending-thumbnail.stl")
        artifact_id = artifact.id
        backend = get_backend()
        data = b"immutable pending thumbnail bytes"
        key = backend.thumbnail_variant_key(artifact_id, artifact.sha256, "a" * 64)
        receipt = publish_bytes(db_session, backend, key, data, object_kind="thumbnail")
        # Crash/paused publication leaves the independently durable receipt pending.
        db_session.rollback()
        proof = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        proof.created_at = utcnow() - timedelta(days=2)
        db_session.add(proof)
        db_session.commit()
        db_session.expunge_all()
        original_info = backend.object_info
        adopted = False

        def adopt_after_sweep_selection(candidate_key):
            nonlocal adopted
            info = original_info(candidate_key)
            if candidate_key == key and not adopted:
                adopted = True
                with Session(db_session.get_bind()) as writer:
                    file = writer.get(File, artifact_id)
                    assert file is not None
                    record_creation(writer, receipt, object_kind="thumbnail")
                    point_at(writer, file, key)
                    writer.commit()
            return info

        monkeypatch.setattr(backend, "object_info", adopt_after_sweep_selection)
        sweep_orphaned_publications(db_session, backend, now=utcnow())
        db_session.commit()
        assert adopted
        db_session.expire_all()
        stored = db_session.get(File, artifact_id)
        assert stored is not None
        assert stored.thumbnail_path == key
        assert backend.read_bytes(key) == data
        current = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert current.state is StorageObjectState.COMMITTED
