"""Cleaning up a half-written upload without deleting somebody else's file.

A browser upload writes into a deterministic path in the staging directory, so a
process killed mid-upload leaves a partial file exactly where the next attempt
wants to write. Recovery has to remove that partial — otherwise the slot is stuck
forever — and it must do so on the evidence of the *lease*, not the path.

That distinction is this file. The recovery removes a partial it can prove it owns
by device and inode, and **preserves** a file at the same path that it cannot:
either a foreign file, or a replacement written after the lease was recorded.
Deleting on path alone would make a predictable staging path a way to delete
arbitrary files.

The last row closes the loop: after the owned temp file is set up, a real upload
still publishes. A recovery that left the slot unusable would be safe and useless.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import CaptureUploadSlot, CaptureUploadSlotState, StagingLease
from app.schemas.inbox import CaptureUploadSlotsCreate
from app.services import inbox, staging_leases
from tests.factories import build_background_job, build_user


def _payload(data: bytes) -> CaptureUploadSlotsCreate:
    return CaptureUploadSlotsCreate.model_validate(
        {
            "source_url": "https://makerworld.com/en/models/1234-widget",
            "capture_source": {
                "provider": "makerworld",
                "canonical_url": "https://makerworld.com/en/models/1234-widget",
                "source_item_id": "1234",
                "source_revision": None,
                "adapter_version": "test",
                "fields": {},
                "tags": [],
            },
            "files": [
                {
                    "id": "widget.3mf",
                    "filename": "widget.3mf",
                    "media_type": "application/octet-stream",
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            ],
        }
    )


class TestRecoverCaptureStaging:
    def test_staging_recovery_removes_only_what_it_owns(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-recovery", superuser=True)
        data = b"complete upload bytes"
        _, slots = inbox.create_capture_upload_slots(db_session, owner, _payload(data))
        slot = slots[0]

        owned = staging_leases.prepare_capture_slot_staging(db_session, slot_id=slot.id)
        with owned.open("ab") as stream:
            stream.write(b"partial")
        foreign = owned.parent / "foreign.tmp"
        foreign.write_bytes(b"leave me")

        db_session.expire_all()
        with db_session.begin():
            removed = staging_leases.reconcile_capture_staging(db_session)

        assert removed == 1
        assert not owned.exists()
        assert foreign.read_bytes() == b"leave me"

    def test_capture_staging_recovery_preserves_replaced_path(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-replaced", superuser=True)
        _, slots = inbox.create_capture_upload_slots(
            db_session, owner, _payload(b"upload bytes")
        )
        slot = slots[0]
        owned = staging_leases.prepare_capture_slot_staging(db_session, slot_id=slot.id)
        owned.unlink()
        owned.write_bytes(b"foreign replacement")

        db_session.expire_all()
        with db_session.begin():
            removed = staging_leases.reconcile_capture_staging(db_session)

        assert removed == 0
        assert owned.read_bytes() == b"foreign replacement"


class TestPruneExpired:
    def test_expired_capture_slot_prune_removes_owned_partial(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-expiry", superuser=True)
        _, slots = inbox.create_capture_upload_slots(
            db_session, owner, _payload(b"upload bytes")
        )
        slot = slots[0]
        slot_id = slot.id
        owned = staging_leases.prepare_capture_slot_staging(db_session, slot_id=slot_id)
        with owned.open("ab") as stream:
            stream.write(b"partial")
        lease = db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot_id)
        ).one()
        lease.expires_at = utcnow()
        db_session.commit()

        assert inbox.prune_expired_browser_leases() == 1
        assert not owned.exists()
        assert db_session.get(CaptureUploadSlot, slot_id) is None


class TestUploadCaptureSlot:
    def test_capture_upload_still_publishes_after_owned_temp_setup(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-success", superuser=True)
        data = b"upload bytes"
        _, slots = inbox.create_capture_upload_slots(db_session, owner, _payload(data))
        slot = slots[0]

        uploaded = inbox.upload_capture_slot(
            db_session,
            slot,
            stream=BytesIO(data),
            media_type="application/octet-stream",
        )

        assert uploaded.state == CaptureUploadSlotState.UPLOADED
        assert not staging_leases.capture_slot_staging_path(slot.id).exists()


class TestCaptureStagingLeaseEdges:
    def test_stream_staging_preserves_declared_bytes(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-stream", superuser=True)
        data = b"streamed payload"
        _, slots = inbox.create_capture_upload_slots(db_session, owner, _payload(data))
        slot = slots[0]
        path = staging_leases.prepare_capture_slot_staging(db_session, slot_id=slot.id)

        staged, size, digest = staging_leases.stage_capture_slot_stream(
            db_session,
            slot_id=slot.id,
            stream=BytesIO(data),
            max_bytes=len(data),
        )

        assert staged == path
        assert size == len(data)
        assert digest == hashlib.sha256(data).hexdigest()
        assert path.read_bytes() == data

    def test_stream_staging_rejects_an_oversized_payload(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-too-large", superuser=True)
        data = b"too large"
        _, slots = inbox.create_capture_upload_slots(db_session, owner, _payload(data))
        slot = slots[0]
        staging_leases.prepare_capture_slot_staging(db_session, slot_id=slot.id)

        from printstash_core.files import UploadTooLarge

        with pytest.raises(UploadTooLarge, match="upload_too_large"):
            staging_leases.stage_capture_slot_stream(
                db_session,
                slot_id=slot.id,
                stream=BytesIO(data),
                max_bytes=len(data) - 1,
            )

        assert staging_leases.capture_slot_staging_path(slot.id).exists()

    def test_prepare_rejects_a_lease_with_an_invalid_path(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-invalid-path", superuser=True)
        _, slots = inbox.create_capture_upload_slots(
            db_session, owner, _payload(b"payload")
        )
        lease = db_session.exec(
            select(StagingLease).where(
                StagingLease.capture_upload_slot_id == slots[0].id
            )
        ).one()
        lease.path = str(tmp_path / "unexpected.upload")
        db_session.add(lease)
        db_session.commit()

        with pytest.raises(
            staging_leases.StagingLeaseError,
            match="capture_upload_staging_path_invalid",
        ):
            staging_leases.prepare_capture_slot_staging(db_session, slot_id=slots[0].id)

    def test_open_staging_rejects_a_path_mismatch(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-open-mismatch", superuser=True)
        _, slots = inbox.create_capture_upload_slots(
            db_session, owner, _payload(b"payload")
        )
        slot = slots[0]
        staging_leases.prepare_capture_slot_staging(db_session, slot_id=slot.id)
        lease = db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot.id)
        ).one()
        lease.path = str(tmp_path / "not-the-slot.upload")
        db_session.add(lease)
        db_session.commit()

        with pytest.raises(
            staging_leases.StagingLeaseError,
            match="capture_upload_staging_collision",
        ):
            with staging_leases.open_capture_slot_staging(db_session, slot_id=slot.id):
                pass

    def test_review_creation_rejects_a_missing_inbox_item(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        path = tmp_path / "missing-inbox.stl"
        path.write_bytes(b"payload")

        with pytest.raises(
            staging_leases.StagingLeaseError, match="inbox item does not exist"
        ):
            staging_leases.create_review_lease(
                db_session,
                inbox_item_id=999_999,
                owner_user_id=None,
                path=path,
                size_bytes=path.stat().st_size,
                sha256="a" * 64,
            )

    def test_review_creation_rejects_a_non_regular_path(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path / "staging")
        owner = build_user(db_session, "staging-review-directory", superuser=True)
        row, _slots = inbox.create_capture_upload_slots(
            db_session, owner, _payload(b"payload")
        )
        directory = tmp_path / "not-a-file"
        directory.mkdir()

        with pytest.raises(
            staging_leases.StagingLeaseError,
            match="staged path identity does not match receipt",
        ):
            staging_leases.create_review_lease(
                db_session,
                inbox_item_id=row.id,
                owner_user_id=owner.id,
                path=directory,
                size_bytes=0,
                sha256="b" * 64,
            )

    def test_review_lease_renews_until_the_review_deadline(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        owner = build_user(db_session, "staging-renew-review", superuser=True)
        row, _slots = inbox.create_capture_upload_slots(
            db_session, owner, _payload(b"payload")
        )
        path = tmp_path / "review.stl"
        path.write_bytes(b"payload")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=row.id,
            owner_user_id=owner.id,
            path=path,
            size_bytes=path.stat().st_size,
            sha256="c" * 64,
        )
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)

        renewed = staging_leases.renew_review_lease(
            db_session, inbox_item_id=row.id, now=now
        )

        assert renewed.id == lease.id
        assert renewed.expires_at == now + timedelta(
            days=inbox.settings.staging_review_lease_days
        )

    def test_job_lease_renews_for_every_capture_slot(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        owner = build_user(db_session, "staging-renew-job", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _payload(b"payload")
        )
        job = build_background_job(db_session, owner=owner)
        staging_leases.transfer_capture_slots_to_job(
            db_session, inbox_item_id=row.id, job_id=job.id
        )
        now = datetime(2026, 1, 3, tzinfo=timezone.utc)

        renewed = staging_leases.renew_job_lease(db_session, job_id=job.id, now=now)

        assert renewed.background_job_id == job.id
        assert renewed.expires_at == now + timedelta(
            hours=inbox.settings.staging_import_lease_hours
        )
        assert db_session.exec(
            select(StagingLease).where(StagingLease.background_job_id == job.id)
        ).all()
