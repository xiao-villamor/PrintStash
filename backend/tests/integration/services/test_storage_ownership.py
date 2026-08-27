"""Storage ownership is positive operation proof, never a path-name guess.

The ledger must refresh only after create-only publication and every replace or
delete must fail closed when current bytes no longer match persisted proof.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import OwnedStorageObject
from app.services.storage_backend import CreationReceipt, get_backend
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    delete_owned_key,
    record_creation,
    replace_owned_bytes,
    require_or_adopt_legacy_artifact,
    require_owned_key,
)


def _created_receipt(
    name: str = "owned.stl", data: bytes = b"owned"
) -> CreationReceipt:
    backend = get_backend()
    key = backend.blob_key("ownership", 1, name)
    return backend.create_bytes(data, key)


def _replace_out_of_band(
    receipt: CreationReceipt, data: bytes = b"replacement"
) -> Path:
    direct = get_backend().direct_path(receipt.key)
    assert direct is not None
    direct.unlink()
    direct.write_bytes(data)
    return direct


class TestRecordCreation:
    def test_records_every_backend_native_creation_field(
        self, db_session: Session
    ) -> None:
        receipt = _created_receipt()

        row = record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()
        db_session.refresh(row)

        assert (
            row.backend,
            row.namespace,
            row.key,
            row.object_kind,
            row.token,
            row.size_bytes,
            row.etag,
            row.version_id,
            row.device,
            row.inode,
            row.ctime_ns,
        ) == (
            receipt.backend,
            receipt.namespace,
            receipt.key,
            "artifact",
            receipt.token,
            receipt.size,
            receipt.etag,
            receipt.version_id,
            receipt.device,
            receipt.inode,
            receipt.ctime_ns,
        )

    def test_refreshes_one_stale_row_after_create_only_republication(
        self, db_session: Session
    ) -> None:
        first_receipt = _created_receipt()
        first = record_creation(db_session, first_receipt, object_kind="artifact")
        db_session.commit()
        first_id = first.id
        direct = _replace_out_of_band(first_receipt, b"second")
        direct.unlink()
        second_receipt = get_backend().create_bytes(b"second", first_receipt.key)

        refreshed = record_creation(db_session, second_receipt, object_kind="thumbnail")
        db_session.commit()

        assert refreshed.id == first_id
        assert refreshed.token == second_receipt.token
        assert refreshed.object_kind == "thumbnail"
        assert db_session.exec(select(OwnedStorageObject)).all() == [refreshed]


class TestRequireOwnedKey:
    def test_accepts_current_positive_proof(self, db_session: Session) -> None:
        receipt = _created_receipt()
        record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()

        result = require_owned_key(db_session, get_backend(), receipt.key)

        assert result is None
        assert get_backend().read_bytes(receipt.key) == b"owned"

    def test_rejects_an_unclaimed_key(self, db_session: Session) -> None:
        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            require_owned_key(db_session, get_backend(), "unclaimed.stl")

        assert db_session.exec(select(OwnedStorageObject)).all() == []

    def test_rejects_proof_after_the_object_is_replaced(
        self, db_session: Session
    ) -> None:
        receipt = _created_receipt()
        record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()
        direct = _replace_out_of_band(receipt)

        with pytest.raises(UnsafeStorageDeleteError, match="no_longer_matches_receipt"):
            require_owned_key(db_session, get_backend(), receipt.key)

        assert direct.read_bytes() == b"replacement"

    def test_translates_a_backend_verification_failure(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = get_backend()
        receipt = _created_receipt()
        record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()

        def fail_verification(_receipt: CreationReceipt) -> bool:
            raise OSError("storage unavailable")

        monkeypatch.setattr(backend, "creation_matches", fail_verification)

        with pytest.raises(UnsafeStorageDeleteError, match="verification_failed"):
            require_owned_key(db_session, backend, receipt.key)

        assert Path(receipt.key).read_bytes() == b"owned"


class TestRequireOrAdoptLegacyArtifact:
    def test_adopts_matching_pre_ledger_bytes(self, db_session: Session) -> None:
        backend = get_backend()
        content = b"legacy"
        receipt = _created_receipt(data=content)

        require_or_adopt_legacy_artifact(
            db_session,
            backend,
            receipt.key,
            expected_size=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
        )
        db_session.commit()

        row = db_session.exec(select(OwnedStorageObject)).one()
        assert row.object_kind == "legacy_artifact"
        assert row.key == receipt.key
        assert backend.read_bytes(receipt.key) == content

    def test_rejects_legacy_bytes_with_the_wrong_digest(
        self, db_session: Session
    ) -> None:
        receipt = _created_receipt(data=b"legacy")

        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            require_or_adopt_legacy_artifact(
                db_session,
                get_backend(),
                receipt.key,
                expected_size=6,
                expected_sha256="0" * 64,
            )

        assert db_session.exec(select(OwnedStorageObject)).all() == []
        assert get_backend().read_bytes(receipt.key) == b"legacy"

    def test_never_replaces_an_existing_stale_claim_by_adoption(
        self, db_session: Session
    ) -> None:
        receipt = _created_receipt(data=b"legacy")
        row = record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()
        original_token = row.token
        direct = _replace_out_of_band(receipt, b"replacement")

        with pytest.raises(UnsafeStorageDeleteError, match="no_longer_matches_receipt"):
            require_or_adopt_legacy_artifact(
                db_session,
                get_backend(),
                receipt.key,
                expected_size=11,
                expected_sha256=hashlib.sha256(b"replacement").hexdigest(),
            )

        db_session.refresh(row)
        assert row.token == original_token
        assert direct.read_bytes() == b"replacement"


class TestReplaceOwnedBytes:
    def test_replaces_bytes_and_refreshes_persisted_proof(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        receipt = _created_receipt()
        row = record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()

        replacement = replace_owned_bytes(
            db_session,
            backend,
            receipt.key,
            b"new-bytes",
            object_kind="thumbnail",
        )
        db_session.commit()
        db_session.refresh(row)

        assert backend.read_bytes(receipt.key) == b"new-bytes"
        assert (row.token, row.size_bytes, row.object_kind) == (
            replacement.token,
            len(b"new-bytes"),
            "thumbnail",
        )

    def test_rejects_replacement_without_a_claim(self, db_session: Session) -> None:
        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            replace_owned_bytes(
                db_session,
                get_backend(),
                "unclaimed.bin",
                b"new",
                object_kind="thumbnail",
            )

        assert db_session.exec(select(OwnedStorageObject)).all() == []

    def test_preserves_replacement_bytes_when_persisted_proof_is_stale(
        self, db_session: Session
    ) -> None:
        receipt = _created_receipt()
        row = record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()
        original_proof = (row.token, row.object_kind)
        direct = _replace_out_of_band(receipt)

        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            replace_owned_bytes(
                db_session,
                get_backend(),
                receipt.key,
                b"new",
                object_kind="thumbnail",
            )

        db_session.refresh(row)
        assert direct.read_bytes() == b"replacement"
        assert (row.token, row.object_kind) == original_proof


class TestDeleteOwnedKey:
    def test_deletes_matching_bytes_and_consumes_their_claim(
        self, db_session: Session
    ) -> None:
        receipt = _created_receipt()
        record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()

        removed = delete_owned_key(
            db_session, get_backend(), receipt.key, required_proof=True
        )
        db_session.commit()

        assert removed is True
        assert get_backend().exists(receipt.key) is False
        assert db_session.exec(select(OwnedStorageObject)).all() == []

    def test_treats_an_unclaimed_optional_key_as_a_noop(
        self, db_session: Session
    ) -> None:
        removed = delete_owned_key(db_session, get_backend(), "unclaimed.bin")

        assert removed is False
        assert db_session.exec(select(OwnedStorageObject)).all() == []

    def test_rejects_an_unclaimed_required_key(self, db_session: Session) -> None:
        with pytest.raises(UnsafeStorageDeleteError, match="ownership_unverified"):
            delete_owned_key(
                db_session,
                get_backend(),
                "unclaimed.bin",
                required_proof=True,
            )

        assert db_session.exec(select(OwnedStorageObject)).all() == []

    def test_rejects_required_deletion_after_object_replacement(
        self, db_session: Session
    ) -> None:
        receipt = _created_receipt()
        row = record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()
        original_proof = (row.token, row.object_kind)
        direct = _replace_out_of_band(receipt)

        with pytest.raises(UnsafeStorageDeleteError, match="no_longer_matches_receipt"):
            delete_owned_key(
                db_session, get_backend(), receipt.key, required_proof=True
            )

        db_session.refresh(row)
        assert direct.read_bytes() == b"replacement"
        assert (row.token, row.object_kind) == original_proof

    def test_returns_false_when_optional_backend_deletion_fails(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = get_backend()
        receipt = _created_receipt()
        record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()

        def fail_delete(_receipt: CreationReceipt) -> bool:
            raise OSError("storage unavailable")

        monkeypatch.setattr(backend, "rollback_create", fail_delete)

        removed = delete_owned_key(db_session, backend, receipt.key)

        assert removed is False
        assert Path(receipt.key).read_bytes() == b"owned"

    def test_raises_when_required_backend_deletion_fails(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = get_backend()
        receipt = _created_receipt()
        record_creation(db_session, receipt, object_kind="artifact")
        db_session.commit()

        def fail_delete(_receipt: CreationReceipt) -> bool:
            raise OSError("storage unavailable")

        monkeypatch.setattr(backend, "rollback_create", fail_delete)

        with pytest.raises(UnsafeStorageDeleteError, match="storage_delete_failed"):
            delete_owned_key(db_session, backend, receipt.key, required_proof=True)

        assert Path(receipt.key).read_bytes() == b"owned"
