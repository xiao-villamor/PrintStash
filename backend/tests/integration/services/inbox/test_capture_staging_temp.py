"""Defends ``test_capture_staging_recovery_removes_owned_partial_and_preserves_foreign`` behavior for the ``inbox`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import CaptureUploadSlot, CaptureUploadSlotState, StagingLease, User
from app.schemas.inbox import CaptureUploadSlotsCreate
from app.services import inbox, staging_leases
from app.services.auth import hash_password


def _user(session: Session, username: str) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_superuser=True,
    )
    session.add(user)
    session.flush()
    return user


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


def test_capture_staging_recovery_removes_owned_partial_and_preserves_foreign(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    owner = _user(db_session, "staging-recovery")
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
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    owner = _user(db_session, "staging-replaced")
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


def test_expired_capture_slot_prune_removes_owned_partial(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    owner = _user(db_session, "staging-expiry")
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


def test_capture_upload_still_publishes_after_owned_temp_setup(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    owner = _user(db_session, "staging-success")
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
