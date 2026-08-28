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
from io import BytesIO
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import CaptureUploadSlot, CaptureUploadSlotState, StagingLease
from app.schemas.inbox import CaptureUploadSlotsCreate
from app.services import inbox, staging_leases
from tests.factories import build_user


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
