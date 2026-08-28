"""Direct safety coverage for the storage ownership ledger seam."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlmodel import select

from app.db.models import OwnedStorageObject
from app.services.storage_backend import CreationReceipt
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    delete_owned_key,
    record_creation,
    replace_owned_bytes,
    require_or_adopt_legacy_artifact,
    require_owned_key,
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
    )


class _LedgerBackend:
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

        assert refreshed.id == first.id
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
            select(OwnedStorageObject).where(OwnedStorageObject.key == stored.key)
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
        if expected:
            assert db_session.exec(select(OwnedStorageObject)).all() == []
        else:
            assert db_session.exec(select(OwnedStorageObject)).one().key == stored.key
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
        with pytest.raises(UnsafeStorageDeleteError, match="storage_delete_failed"):
            delete_owned_key(db_session, backend, stored.key, required_proof=True)
        assert backend.rollback_calls[-1] == stored

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
