from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import timedelta
from io import BytesIO
from typing import cast

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select
from starlette.requests import ClientDisconnect, Request

from app.api.v1 import inbox as inbox_api
from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    BrowserDevice,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    Collection,
    File,
    FileType,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.db.session import get_session_factory
from app.schemas.inbox import CaptureUploadSlotsCreate, InboxImportRequest
from app.services import inbox
from app.services.auth import create_access_token, hash_password
from app.services.source_covers import SourceCoverWrite
from app.services.storage_backend import CreationReceipt
from app.services.storage_deletion import process_storage_delete_intents


@pytest.fixture(autouse=True)
def _isolate_capture_slot_lifecycle_rows(db_session: Session) -> None:
    """The shared SQLite reset predates capture slots; avoid reused inbox IDs."""
    db_session.exec(delete(StorageDeleteIntent))
    db_session.exec(delete(StagingLease))
    db_session.exec(delete(CaptureUploadSlot))
    db_session.commit()


def _capture_source(
    *,
    provider: str = "makerworld",
    canonical_url: str = "https://makerworld.com/en/models/1234-widget",
) -> dict:
    return {
        "provider": provider,
        "canonical_url": canonical_url,
        "source_item_id": "1234",
        "source_revision": None,
        "adapter_version": "extension-v1",
        "fields": {"title": {"value": "Widget", "origin": "confirmed"}},
        "tags": [],
    }


def _headers(session: Session, username: str, *, admin: bool = False) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=admin,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.username, scope='admin' if admin else 'write')}"
    }


def _slot_payload(data: bytes = b"slot-owned") -> CaptureUploadSlotsCreate:
    return CaptureUploadSlotsCreate.model_validate(
        {
            "source_url": "https://makerworld.com/en/models/1234-widget",
            "capture_source": _capture_source(),
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


def test_capture_slots_are_owner_scoped_idempotent_and_finalize_gated(
    db_session: Session,
) -> None:
    owner = _user(db_session, "slot-owner")
    other = _user(db_session, "slot-other", admin=False)
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = slots[0]
    with pytest.raises(HTTPException, match="not_found"):
        inbox.require_capture_slot(db_session, other, slot.id)
    with pytest.raises(HTTPException, match="incomplete"):
        inbox.finalize_capture_upload(db_session, owner, row.id)
    first = inbox.upload_capture_slot(
        db_session,
        slot,
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    replay = inbox.upload_capture_slot(
        db_session,
        first,
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    assert replay.id == first.id
    with pytest.raises(ValueError, match="sha256"):
        inbox.upload_capture_slot(
            db_session,
            replay,
            stream=BytesIO(b"different!"),
            media_type="application/octet-stream",
        )
    finalized = inbox.finalize_capture_upload(db_session, owner, row.id)
    assert finalized.state == InboxItemState.REVIEW
    with pytest.raises(ValueError, match="not_uploadable"):
        inbox.upload_capture_slot(
            db_session,
            replay,
            stream=BytesIO(b"slot-owned"),
            media_type="application/octet-stream",
        )


@pytest.mark.anyio
async def test_capture_slot_upload_cleans_temp_file_after_stream_disconnect(
    db_session: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    incoming_dir = inbox.settings.incoming_dir
    incoming_dir.mkdir(parents=True)
    owner = _user(db_session, "slot-disconnect")
    payload = _slot_payload(data=b"disconnect-me")
    _, slots = inbox.create_capture_upload_slots(db_session, owner, payload)
    slot = slots[0]
    state = iter(
        [
            {
                "type": "http.request",
                "body": b"disconnect",
                "more_body": True,
            },
            {"type": "http.disconnect"},
        ]
    )

    async def receive() -> dict[str, object]:
        return cast(dict[str, object], next(state))

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "PUT",
            "scheme": "http",
            "path": f"/api/v1/inbox/capture-upload-slots/{slot.id}",
            "raw_path": b"/api/v1/inbox/capture-upload-slots",
            "query_string": b"",
            "headers": [
                (b"content-length", str(len(b"disconnect-me")).encode()),
                (b"content-type", b"application/octet-stream"),
            ],
            "server": ("testserver", 80),
            "client": ("testclient", 123),
        },
        receive,
    )

    with pytest.raises(ClientDisconnect):
        await inbox_api.put_capture_upload_slot(
            slot.id, request, current_user=owner, session=db_session
        )

    assert list(incoming_dir.iterdir()) == []


@pytest.mark.parametrize("failure", ["invalid_image", "postprocess"])
def test_capture_cover_service_temp_is_cleaned_when_processing_fails(
    db_session: Session, tmp_path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Service staging is distinct from the API request temp and is always cleaned."""
    from PIL import Image

    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    incoming_dir = inbox.settings.incoming_dir
    incoming_dir.mkdir(parents=True)
    if failure == "invalid_image":
        cover_bytes = b"not-an-image"
    else:
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "navy").save(image, format="PNG")
        cover_bytes = image.getvalue()

        def fail_postprocess(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("injected cover postprocess failure")

        monkeypatch.setattr(inbox, "process_source_cover_upload", fail_postprocess)

    raw = _slot_payload().model_dump(mode="json")
    raw["cover"] = {
        "id": "cover",
        "filename": "cover.png",
        "media_type": "image/png",
        "size_bytes": len(cover_bytes),
        "sha256": hashlib.sha256(cover_bytes).hexdigest(),
    }
    owner = _user(db_session, f"cover-service-{failure}")
    _, slots = inbox.create_capture_upload_slots(
        db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
    )
    cover_slot = next(slot for slot in slots if slot.role == "cover")

    expected_error = ValueError if failure == "invalid_image" else RuntimeError
    with pytest.raises(expected_error):
        inbox.upload_capture_slot(
            db_session,
            cover_slot,
            stream=BytesIO(cover_bytes),
            media_type="image/png",
        )

    assert list(incoming_dir.iterdir()) == []
    db_session.expire_all()
    fresh_slot = db_session.get(CaptureUploadSlot, cover_slot.id)
    assert fresh_slot is not None
    assert fresh_slot.state == CaptureUploadSlotState.PENDING
    assert (
        db_session.exec(
            select(StagingLease).where(
                StagingLease.capture_upload_slot_id == cover_slot.id
            )
        ).one()
        is not None
    )


def test_capture_slot_lease_uses_slot_owner_for_dismiss(db_session: Session) -> None:
    """Dismiss finds the lease through the same capture-slot identity that created it."""
    owner = _user(db_session, "slot-dismiss-owner")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())

    inbox.dismiss(db_session, row)

    dismissed = db_session.get(InboxItem, row.id)
    assert dismissed is not None
    assert dismissed.state == InboxItemState.DISMISSED
    assert (
        db_session.exec(
            select(StagingLease).where(
                StagingLease.capture_upload_slot_id == slots[0].id
            )
        ).all()
        == []
    )


def test_dismiss_durably_releases_uploaded_capture_slot_bytes(
    db_session: Session,
) -> None:
    owner = _user(db_session, "slot-dismiss-uploaded")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    assert slot.storage_key is not None

    inbox.dismiss(db_session, row)

    assert db_session.get(CaptureUploadSlot, slot.id) is None
    assert (
        db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot.id)
        ).all()
        == []
    )
    intent = db_session.exec(select(StorageDeleteIntent)).one()
    assert (intent.resource_kind, intent.resource_id, intent.key) == (
        "capture_upload_slot",
        slot.id,
        slot.storage_key,
    )


def test_dismiss_keeps_capture_slot_when_delete_intent_cannot_be_enqueued(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session, "slot-dismiss-retry")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    monkeypatch.setattr(
        inbox,
        "enqueue_creation_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("intent unavailable")),
    )

    with pytest.raises(HTTPException, match="staging_cleanup_failed"):
        inbox.dismiss(db_session, row)
    db_session.commit()
    db_session.expire_all()

    retained = db_session.get(InboxItem, row.id)
    assert retained is not None
    assert retained.state == InboxItemState.CAPTURED
    assert db_session.get(CaptureUploadSlot, slot.id) is not None
    assert db_session.exec(
        select(StagingLease).where(StagingLease.capture_upload_slot_id == slot.id)
    ).one()


def test_capture_slot_cleanup_enqueues_before_post_commit_delete(
    db_session: Session,
) -> None:
    """Slot bytes remain until the committed outbox processor consumes receipt."""
    from app.db.models import StorageDeleteIntent
    from app.services.storage_deletion import process_storage_delete_intents

    owner = _user(db_session, "slot-outbox-owner")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    assert slot.storage_key is not None
    backend = inbox.get_backend()
    assert backend.exists(slot.storage_key)

    assert inbox._cleanup_capture_slots(db_session, row)
    assert backend.exists(slot.storage_key)
    assert db_session.exec(select(StorageDeleteIntent)).one().key == slot.storage_key
    db_session.commit()
    assert backend.exists(slot.storage_key)

    assert process_storage_delete_intents().completed == 1
    assert not backend.exists(slot.storage_key)


def test_expired_uploaded_capture_slot_fails_inbox_after_durable_cleanup(
    db_session: Session,
) -> None:
    owner = _user(db_session, "slot-expiry-owner")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    lease = db_session.exec(
        select(StagingLease).where(StagingLease.capture_upload_slot_id == slot.id)
    ).one()
    lease.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    assert inbox.prune_expired_browser_leases() == 1
    db_session.expire_all()
    expired_item = db_session.get(InboxItem, row.id)
    assert expired_item is not None
    assert expired_item.state == InboxItemState.FAILED
    assert expired_item.error_code == "staging_expired"


def test_capture_slot_cleanup_rollback_preserves_receipt_lease_and_bytes(
    db_session: Session,
) -> None:
    from app.db.models import StorageDeleteIntent

    owner = _user(db_session, "slot-rollback-owner")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    assert slot.storage_key is not None
    slot_id = slot.id
    slot_key = slot.storage_key
    assert inbox._cleanup_capture_slots(db_session, row)
    db_session.rollback()
    db_session.expire_all()

    assert db_session.get(CaptureUploadSlot, slot_id) is not None
    assert db_session.exec(
        select(StagingLease).where(StagingLease.capture_upload_slot_id == slot_id)
    ).one()
    assert inbox.get_backend().exists(slot_key)
    assert db_session.exec(select(StorageDeleteIntent)).all() == []


def test_delete_intent_processor_retries_backend_failure_and_blocks_mismatch(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.models import StorageDeleteIntent
    from app.services import storage_deletion

    owner = _user(db_session, "slot-retry-owner")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    assert slot.storage_key is not None
    assert inbox._cleanup_capture_slots(db_session, row)
    db_session.commit()
    backend = inbox.get_backend()
    monkeypatch.setattr(
        backend,
        "rollback_create",
        lambda _receipt: (_ for _ in ()).throw(OSError("offline")),
    )
    result = storage_deletion.process_storage_delete_intents()
    assert result.pending == 1
    intent = db_session.exec(select(StorageDeleteIntent)).one()
    assert intent.status == "retry"
    assert backend.exists(slot.storage_key)

    # A receiver must fail closed when deletion cannot prove this is our object.
    monkeypatch.setattr(backend, "rollback_create", lambda _receipt: False)
    intent.next_attempt_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    result = storage_deletion.process_storage_delete_intents()
    assert result.blocked == 1
    db_session.expire_all()
    blocked_intent = db_session.get(StorageDeleteIntent, intent.id)
    assert blocked_intent is not None
    assert blocked_intent.status == "blocked"
    assert backend.exists(slot.storage_key)


def test_capture_source_mismatch_rejects_before_slot_write(db_session: Session) -> None:
    owner = _user(db_session, "slot-source")
    raw = _slot_payload().model_dump(mode="json")
    raw["capture_source"]["canonical_url"] = (
        "https://makerworld.com/en/models/1234-other"
    )
    with pytest.raises(
        inbox.importer.ImportError_, match="capture_source_url_mismatch"
    ):
        inbox.create_capture_upload_slots(
            db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
        )
    assert db_session.exec(select(InboxItem)).all() == []


def test_capture_slot_replay_survives_fresh_session_and_reassigns_two_leases(
    db_session: Session,
) -> None:
    owner = _user(db_session, "slot-restart")
    payload = _slot_payload()
    raw = payload.model_dump(mode="json")
    raw["files"].append(
        {
            "id": "second.stl",
            "filename": "second.stl",
            "media_type": "application/octet-stream",
            "size_bytes": 3,
            "sha256": hashlib.sha256(b"two").hexdigest(),
        }
    )
    row, slots = inbox.create_capture_upload_slots(
        db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
    )
    inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    inbox.upload_capture_slot(
        db_session,
        slots[1],
        stream=BytesIO(b"two"),
        media_type="application/octet-stream",
    )
    db_session.expire_all()  # simulates a new request/session identity map
    replay_slot = db_session.get(type(slots[0]), slots[0].id)
    assert replay_slot is not None
    assert (
        inbox.upload_capture_slot(
            db_session,
            replay_slot,
            stream=BytesIO(b"slot-owned"),
            media_type="application/octet-stream",
        ).id
        == slots[0].id
    )
    first = BackgroundJob(id="slot-job-one", owner_user_id=owner.id)
    second = BackgroundJob(id="slot-job-two", owner_user_id=owner.id)
    db_session.add(first)
    db_session.add(second)
    db_session.flush()
    inbox.staging_leases.transfer_capture_slots_to_job(
        db_session, inbox_item_id=row.id, job_id=first.id
    )
    inbox.staging_leases.transfer_capture_slots_to_job(
        db_session, inbox_item_id=row.id, job_id=second.id
    )
    leases = db_session.exec(
        select(StagingLease).where(StagingLease.background_job_id == second.id)
    ).all()
    assert len(leases) == 2
    assert {lease.capture_upload_slot_origin_id for lease in leases} == {
        slot.id for slot in slots
    }


def test_capture_slot_restart_reconciles_published_object_without_receipt(
    db_session: Session,
) -> None:
    owner = _user(db_session, "slot-crash-restart")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = slots[0]
    backend = inbox.get_backend()
    assert slot.storage_key is not None

    # Simulate create-only success followed by process death before slot and
    # lease receipt persistence.
    backend.create_bytes(b"slot-owned", slot.storage_key)
    db_session.expire_all()
    recovered_slot = db_session.get(CaptureUploadSlot, slot.id)
    assert recovered_slot is not None
    assert recovered_slot.receipt_json is None
    assert inbox.staging_leases.reconcile_capture_slot(
        db_session, backend, recovered_slot
    )
    db_session.commit()

    assert recovered_slot.state == CaptureUploadSlotState.UPLOADED
    assert recovered_slot.receipt_json is not None
    inbox.dismiss(db_session, row)
    assert backend.exists(slot.storage_key)
    process_storage_delete_intents()
    assert not backend.exists(slot.storage_key)


def test_capture_slot_dismiss_preserves_unadoptable_collision(
    db_session: Session,
) -> None:
    owner = _user(db_session, "slot-collision-preserved")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = slots[0]
    assert slot.storage_key is not None
    backend = inbox.get_backend()
    backend.create_bytes(b"foreign-bytes", slot.storage_key)

    with pytest.raises(HTTPException, match="staging_cleanup_failed"):
        inbox.dismiss(db_session, row)
    assert backend.read_bytes(slot.storage_key) == b"foreign-bytes"
    assert db_session.get(CaptureUploadSlot, slot.id) is not None


def test_retry_returns_transferred_capture_slot_leases_to_review(
    db_session: Session,
) -> None:
    """A failed durable capture can be retried after its import job owned slots."""
    owner = _user(db_session, "slot-retry-after-transfer")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    row.state = InboxItemState.FAILED
    row.retryable = True
    job = BackgroundJob(id="slot-retry-after-transfer-job", owner_user_id=owner.id)
    db_session.add(job)
    db_session.flush()
    row.background_job_id = job.id
    inbox.staging_leases.transfer_capture_slots_to_job(
        db_session, inbox_item_id=row.id, job_id=job.id
    )
    db_session.commit()

    retried = inbox.retry(db_session, row)

    assert retried.state == InboxItemState.REVIEW
    lease = db_session.exec(
        select(StagingLease).where(StagingLease.capture_upload_slot_id == slots[0].id)
    ).one()
    assert lease.background_job_id is None
    assert lease.capture_upload_slot_origin_id is None


def test_capture_slot_cleanup_later_failure_preserves_all_slot_ownership(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup is all-or-nothing when a later durable intent cannot be made."""
    raw = _slot_payload().model_dump(mode="json")
    raw["files"].append(
        {
            "id": "second.stl",
            "filename": "second.stl",
            "media_type": "application/octet-stream",
            "size_bytes": 3,
            "sha256": hashlib.sha256(b"two").hexdigest(),
        }
    )
    owner = _user(db_session, "slot-cleanup-atomic")
    row, slots = inbox.create_capture_upload_slots(
        db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
    )
    inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    inbox.upload_capture_slot(
        db_session,
        slots[1],
        stream=BytesIO(b"two"),
        media_type="application/octet-stream",
    )
    original_enqueue = inbox.enqueue_creation_receipt
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("intent store unavailable")
        return original_enqueue(*args, **kwargs)

    monkeypatch.setattr(inbox, "enqueue_creation_receipt", fail_second)

    assert not inbox._cleanup_capture_slots(db_session, row)
    db_session.commit()
    db_session.expire_all()

    assert {slot.id for slot in db_session.exec(select(CaptureUploadSlot)).all()} >= {
        slot.id for slot in slots
    }
    assert len(db_session.exec(select(StagingLease)).all()) == 2
    assert db_session.exec(select(StorageDeleteIntent)).all() == []


def test_capture_cover_attaches_before_raw_slot_receipt_is_released(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    owner = _user(db_session, "slot-cover")
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "navy").save(image, format="PNG")
    cover_bytes = image.getvalue()
    raw = _slot_payload().model_dump(mode="json")
    raw["cover"] = {
        "id": "cover",
        "filename": "cover.png",
        "media_type": "image/png",
        "size_bytes": len(cover_bytes),
        "sha256": hashlib.sha256(cover_bytes).hexdigest(),
    }
    row, slots = inbox.create_capture_upload_slots(
        db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
    )
    file_slot, cover_slot = slots
    inbox.upload_capture_slot(
        db_session,
        file_slot,
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    inbox.upload_capture_slot(
        db_session, cover_slot, stream=BytesIO(cover_bytes), media_type="image/png"
    )
    model = Model(
        name="Cover import",
        slug=f"cover-import-{uuid.uuid4().hex}",
        hash=uuid.uuid4().hex * 2,
    )
    db_session.add(model)
    db_session.flush()
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="makerworld",
        canonical_url="https://makerworld.com/en/models/1234-widget",
        source_item_id="1234",
        identity_key=uuid.uuid4().hex * 2,
    )
    db_session.add(source)
    row.resulting_model_id = model.id
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(
        inbox.source_covers,
        "put",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("cover publish failed")
        ),
    )
    with pytest.raises(RuntimeError, match="cover publish failed"):
        inbox._attach_capture_cover(db_session, row)
    assert db_session.get(type(cover_slot), cover_slot.id) is not None
    attached: list[int] = []
    monkeypatch.setattr(
        inbox.source_covers,
        "put",
        lambda _s, _b, **kwargs: attached.append(kwargs["provenance_source_id"]),
    )
    assert inbox._attach_capture_cover(db_session, row)
    assert attached == [source.id]
    assert inbox._cleanup_capture_slots(db_session, row)
    db_session.commit()
    assert db_session.get(type(cover_slot), cover_slot.id) is None


@pytest.mark.parametrize("created", [True, False])
def test_finished_capture_rolls_back_cover_write_when_commit_fails(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, created: bool
) -> None:
    owner = _user(db_session, f"cover-commit-failure-{created}")
    row = InboxItem(
        owner_user_id=owner.id,
        source_kind="browser",
        source_url="https://makerworld.com/en/models/1234-widget",
        source_hostname="makerworld.com",
        state=InboxItemState.IMPORTING,
    )
    db_session.add(row)
    db_session.commit()
    receipt = CreationReceipt(
        key=f"covers/{created}.webp",
        size=1,
        token="receipt",
        backend="fake",
        namespace="test",
    )
    write = SourceCoverWrite(
        cover=ModelSourceCover(provenance_source_id=1, storage_key=receipt.key),
        created=created,
        creation_receipt=receipt if created else None,
        replacement_receipt=None if created else receipt,
        replaced_bytes=None if created else b"old",
    )

    class _Factory:
        def scoped_session(self) -> object:
            class _Scope:
                def __enter__(self) -> Session:
                    return db_session

                def __exit__(self, *args: object) -> None:
                    return None

            return _Scope()

    job = type("Job", (), {"state": "completed", "model_id": 1, "result": None})()
    monkeypatch.setattr(inbox.registry, "get", lambda _job_id: job)
    monkeypatch.setattr(inbox, "_record_v2_results", lambda *_args: (True, 1, 0))
    monkeypatch.setattr(inbox, "_attach_capture_cover", lambda *_args: write)
    monkeypatch.setattr(inbox, "_cleanup_capture_slots", lambda *_args: True)
    rollback = pytest.MonkeyPatch()
    rollback.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )
    seam_calls: list[SourceCoverWrite] = []
    monkeypatch.setattr(
        inbox.source_covers,
        "rollback_after_commit_failure",
        lambda _session, _backend, result: seam_calls.append(result),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        inbox._finish_import(row.id, "cover-commit-failure-job", _Factory())

    assert seam_calls == [write]
    rollback.undo()


def test_capture_is_durable_and_owner_scoped(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    owner = _headers(db_session, "capture-owner", admin=True)
    other = _headers(db_session, "capture-other", admin=True)
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def no_resolve(_item_id: int) -> None:
        return None

    monkeypatch.setattr(inbox, "resolve", no_resolve)
    created = client.post(
        "/api/v1/inbox",
        headers=owner,
        json={
            "url": "https://example.com/model?token=secret&view=files#fragment",
            "title": "Bracket",
        },
    )
    assert created.status_code == 202
    body = created.json()
    assert body["state"] == "captured"
    assert body["source_url"] == "https://example.com/model?view=files"
    assert client.get("/api/v1/inbox", headers=owner).json()[0]["id"] == body["id"]
    # Superusers may inspect all queues; ordinary users remain owner-scoped.
    ordinary = _headers(db_session, "capture-ordinary")
    assert client.get("/api/v1/inbox", headers=ordinary).json() == []
    assert (
        client.get(f"/api/v1/inbox/{body['id']}", headers=ordinary).status_code == 404
    )
    assert client.get(f"/api/v1/inbox/{body['id']}", headers=other).status_code == 200


@pytest.mark.parametrize(
    "source_kind",
    [pytest.param("browser", id="explicit-browser"), pytest.param(None, id="default")],
)
def test_rich_metadata_capture_requires_user_file_before_persistence(
    client: TestClient, db_session: Session, source_kind: str | None
) -> None:
    headers = _headers(db_session, "rich-browser-metadata", admin=True)
    payload = {
        "url": "https://makerworld.com/en/models/1234-widget",
        "capture_source": _capture_source(),
    }
    if source_kind is not None:
        payload["source_kind"] = source_kind

    response = client.post(
        "/api/v1/inbox",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "user_file_required"
    assert db_session.exec(select(InboxItem)).all() == []
    assert db_session.exec(select(CaptureUploadSlot)).all() == []


def test_capture_rejects_url_credentials_at_boundary(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "capture-credentials", admin=True)
    response = client.post(
        "/api/v1/inbox",
        headers=headers,
        json={"url": "https://user:password@example.com/model"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "url_invalid"


def test_browser_upload_rich_source_is_staged_as_v2_manifest(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    headers = _headers(db_session, "browser-rich", admin=True)

    response = client.post(
        "/api/v1/inbox/browser-upload",
        headers=headers,
        data={
            "source_url": "https://makerworld.com/en/models/1234-widget",
            "capture_source": json.dumps(_capture_source()),
        },
        files={"file": ("widget.3mf", b"browser-owned", "application/octet-stream")},
    )

    assert response.status_code == 201, response.text
    manifest = response.json()["manifest"]
    assert manifest["schema_version"] == 2
    assert manifest["source"] == _capture_source()
    assert manifest["files"] == [
        {"id": "widget.3mf", "name": "widget.3mf", "file_type": "3mf", "size": 13}
    ]


@pytest.mark.parametrize(
    "source",
    [
        _capture_source(provider="MakerWorld"),
        _capture_source(
            canonical_url="https://makerworld.com/en/models/1234-widget?token=signed"
        ),
        {**_capture_source(), "signed_url": "https://cdn.example/file?sig=secret"},
    ],
)
def test_browser_upload_rejects_untrusted_source_before_staging(
    client: TestClient, db_session: Session, tmp_path, monkeypatch, source: dict
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    headers = _headers(db_session, f"browser-reject-{len(source)}", admin=True)

    response = client.post(
        "/api/v1/inbox/browser-upload",
        headers=headers,
        data={
            "source_url": "https://makerworld.com/en/models/1234-widget",
            "capture_source": json.dumps(source),
        },
        files={"file": ("widget.3mf", b"must-not-stage", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert db_session.exec(select(InboxItem)).all() == []
    assert not (tmp_path / "_incoming").exists()


def test_capture_routes_accept_only_active_paired_browser_credentials(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    owner = User(
        username="paired-browser-owner",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)
    credential = "opaque-browser-import-credential"
    device = BrowserDevice(
        user_id=owner.id,
        name="Firefox",
        credential_hash=hashlib.sha256(credential.encode()).hexdigest(),
    )
    db_session.add(device)
    db_session.commit()
    headers = {"Authorization": f"Bearer {credential}"}

    accepted = client.post(
        "/api/v1/inbox/browser-upload",
        headers=headers,
        data={"source_url": "https://makerworld.com/en/models/1234-widget"},
        files={"file": ("widget.3mf", b"browser-owned", "application/octet-stream")},
    )
    assert accepted.status_code == 201, accepted.text

    device.revoked_at = inbox.utcnow()
    db_session.add(device)
    db_session.commit()
    rejected = client.post(
        "/api/v1/inbox",
        headers=headers,
        json={"url": "https://example.com/model"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "invalid_browser_credential"


def test_review_item_cannot_be_resolved_again(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "capture-review", admin=True)
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def no_resolve(_item_id: int) -> None:
        return None

    monkeypatch.setattr(inbox, "resolve", no_resolve)
    created = client.post(
        "/api/v1/inbox", headers=headers, json={"url": "https://example.com/model"}
    )
    item_id = created.json()["id"]
    row = db_session.get(inbox.InboxItem, item_id)
    assert row is not None
    row.state = inbox.InboxItemState.REVIEW
    db_session.add(row)
    db_session.commit()

    response = client.post(f"/api/v1/inbox/{item_id}/resolve", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "pending_import_not_resolvable"


def _make_item(db_session: Session, owner: User, **overrides) -> InboxItem:
    defaults = dict(
        owner_user_id=owner.id,
        source_url="https://example.com/model",
        source_hostname="example.com",
        state=InboxItemState.CAPTURED,
    )
    defaults.update(overrides)
    row = InboxItem(**defaults)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _user(db_session: Session, username: str, *, admin: bool = True) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=admin,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_capture_maps_import_error_to_400(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "capture-import-error", admin=True)

    def _raise(_url: str) -> None:
        raise inbox.importer.ImportError_("private_address_blocked")

    monkeypatch.setattr(inbox.importer, "validate_public_url", _raise)

    response = client.post(
        "/api/v1/inbox", headers=headers, json={"url": "https://example.com/model"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "private_address_blocked"


def test_get_and_update_item(client: TestClient, db_session: Session) -> None:
    headers = _headers(db_session, "update-owner")
    owner = _user(db_session, "update-owner-user", admin=False)
    row = _make_item(db_session, owner)

    fetched = client.get(f"/api/v1/inbox/{row.id}", headers=headers)
    assert fetched.status_code == 404  # owner mismatch: caller is a different user

    own_headers = {
        "Authorization": f"Bearer {create_access_token(owner.id, owner.username, scope='write')}"
    }
    fetched_own = client.get(f"/api/v1/inbox/{row.id}", headers=own_headers)
    assert fetched_own.status_code == 200

    updated = client.patch(
        f"/api/v1/inbox/{row.id}",
        headers=own_headers,
        json={"title": "Renamed bracket"},
    )
    assert updated.status_code == 200
    assert updated.json()["display_title"] == "Renamed bracket"


def test_get_item_includes_durable_per_file_results(
    client: TestClient, db_session: Session
) -> None:
    owner = _user(db_session, "result-owner", admin=False)
    row = _make_item(db_session, owner)
    result = InboxItemResult(
        inbox_item_id=row.id,
        source_selection_id="remote-stl",
        result_key="self",
        original_filename="bracket.stl",
        state=InboxItemResultState.IMPORTED,
        model_id=42,
        file_id=99,
        retryable=False,
    )
    db_session.add(result)
    db_session.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(owner.id, owner.username, scope='write')}"
    }

    response = client.get(f"/api/v1/inbox/{row.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "imported"
    assert response.json()["results"][0]["result_key"] == "self"


def test_resolve_item_success_schedules_background_resolve(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "resolve-success", admin=True)
    owner = _user(db_session, "resolve-success-owner")
    row = _make_item(db_session, owner, state=InboxItemState.FAILED)
    calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)

    response = client.post(f"/api/v1/inbox/{row.id}/resolve", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert calls == [row.id]


def test_import_item_requires_review_state(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "import-not-ready", admin=True)
    owner = _user(db_session, "import-not-ready-owner")
    row = _make_item(db_session, owner, state=InboxItemState.CAPTURED)

    response = client.post(
        f"/api/v1/inbox/{row.id}/import", headers=headers, json={"selected_ids": []}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "pending_import_not_ready"


def test_import_item_success_schedules_run_import(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "import-success", admin=True)
    owner = _user(db_session, "import-success-owner")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json='{"kind": "direct", "title": "x"}',
    )
    calls: list[tuple[int, list[str]]] = []

    async def fake_run_import(
        item_id: int, selected_ids: list[str], _session_factory
    ) -> None:
        calls.append((item_id, selected_ids))

    monkeypatch.setattr(inbox, "run_import", fake_run_import)

    response = client.post(
        f"/api/v1/inbox/{row.id}/import",
        headers=headers,
        json={"selected_ids": ["a"]},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "review"
    assert calls == [(row.id, ["a"])]


class _BackgroundTaskRecorder:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def add_task(self, function: object, *args: object, **kwargs: object) -> None:
        self.tasks.append((function, args, kwargs))


@pytest.mark.parametrize("requested", [["missing"], ["ok", "missing"]])
def test_import_route_rejects_invalid_v2_selection_before_scheduling(
    db_session: Session, requested: list[str]
) -> None:
    owner = _user(db_session, f"import-selection-route-{len(requested)}")
    row = _make_item(
        db_session,
        owner,
        source_url="https://makerworld.com/en/models/1234-widget",
        source_hostname="makerworld.com",
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": _capture_source(),
                "files": [
                    {"id": "ok", "name": "ok.stl", "file_type": "stl", "size": 1}
                ],
                "selected_ids": ["ok"],
            }
        ),
    )
    assert row.id is not None
    background = _BackgroundTaskRecorder()
    jobs_before = db_session.exec(select(BackgroundJob)).all()

    with pytest.raises(HTTPException) as exc_info:
        inbox_api.import_item(
            row.id,
            InboxImportRequest(selected_ids=requested),
            cast(BackgroundTasks, background),
            current_user=owner,
            session=db_session,
            session_factory=get_session_factory(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "file_selection_invalid"
    assert background.tasks == []
    assert db_session.exec(select(BackgroundJob)).all() == jobs_before


def test_retry_item_schedules_resolve_when_returned_to_captured(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "retry-success", admin=True)
    owner = _user(db_session, "retry-success-owner")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.FAILED,
        retryable=True,
        manifest_json="",
    )
    calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)

    response = client.post(f"/api/v1/inbox/{row.id}/retry", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "captured"
    assert calls == [row.id]


def test_retry_partial_schedules_failed_selection_only(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    owner = _user(db_session, "retry-partial-api")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.COMPLETED,
        completion="partial",
        retryable=True,
        manifest_json='{"kind":"model_files","selected_ids":["bad"]}',
    )
    result = InboxItemResult(
        inbox_item_id=row.id,
        source_selection_id="bad",
        result_key="self",
        original_filename="bad.stl",
        state=InboxItemResultState.FAILED,
        error_code="captured_artifact_trashed",
        retryable=True,
    )
    db_session.add(result)
    db_session.commit()
    calls: list[tuple[int, list[str]]] = []

    async def fake_run_import(item_id: int, selected_ids: list[str], _factory) -> None:
        calls.append((item_id, selected_ids))

    monkeypatch.setattr(inbox, "run_import", fake_run_import)
    headers = {
        "Authorization": f"Bearer {create_access_token(owner.id, owner.username, scope='write')}"
    }

    response = client.post(f"/api/v1/inbox/{row.id}/retry", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "review"
    assert calls == [(row.id, ["bad"])]


def test_retry_route_rejects_invalid_v2_selection_before_scheduling(
    db_session: Session,
) -> None:
    owner = _user(db_session, "retry-selection-route")
    row = _make_item(
        db_session,
        owner,
        source_url="https://makerworld.com/en/models/1234-widget",
        source_hostname="makerworld.com",
        state=InboxItemState.FAILED,
        retryable=True,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": _capture_source(),
                "files": [
                    {"id": "ok", "name": "ok.stl", "file_type": "stl", "size": 1}
                ],
                "selected_ids": ["missing"],
            }
        ),
    )
    assert row.id is not None
    background = _BackgroundTaskRecorder()
    jobs_before = db_session.exec(select(BackgroundJob)).all()

    with pytest.raises(HTTPException) as exc_info:
        inbox_api.retry_item(
            row.id,
            cast(BackgroundTasks, background),
            current_user=owner,
            session=db_session,
            session_factory=get_session_factory(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "file_selection_invalid"
    assert background.tasks == []
    assert db_session.exec(select(BackgroundJob)).all() == jobs_before


def test_dismiss_item_returns_204(client: TestClient, db_session: Session) -> None:
    headers = _headers(db_session, "dismiss-owner", admin=True)
    owner = _user(db_session, "dismiss-owner-user")
    row = _make_item(db_session, owner)

    response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers)

    assert response.status_code == 204
    db_session.expire_all()
    refreshed = db_session.get(InboxItem, row.id)
    assert refreshed.state == InboxItemState.DISMISSED


def test_dismiss_completed_capture_after_terminal_cleanup_preserves_model(
    client: TestClient, db_session: Session
) -> None:
    """A completed capture has no review lease left to return before dismissing."""
    owner = _user(db_session, "dismiss-completed-owner")
    model = Model(
        name="Imported widget",
        slug="imported-widget",
        hash="d" * 64,
        source_url="https://makerworld.com/en/models/1234-widget",
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    artifact = File(
        model_id=model.id,
        path="imported/widget.stl",
        original_filename="widget.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=4,
        sha256="e" * 64,
    )
    db_session.add(artifact)
    db_session.commit()
    db_session.refresh(artifact)
    job = BackgroundJob(
        id="completed-dismiss-job",
        owner_user_id=owner.id,
        state="completed",
        status_json='{"state":"completed"}',
        finished_at=utcnow(),
    )
    db_session.add(job)
    db_session.commit()
    row = _make_item(
        db_session,
        owner,
        source_kind="BROWSER",
        state=InboxItemState.COMPLETED,
        background_job_id=job.id,
        resulting_model_id=model.id,
    )
    headers = {
        "Authorization": f"Bearer {create_access_token(owner.id, owner.username, scope='write')}"
    }

    response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers)

    assert response.status_code == 204, response.text
    db_session.expire_all()
    refreshed = db_session.get(InboxItem, row.id)
    assert refreshed is not None
    assert refreshed.state == InboxItemState.DISMISSED
    assert refreshed.background_job_id is None
    assert db_session.get(Model, model.id) is not None
    assert db_session.get(File, artifact.id) is not None
    listed = client.get("/api/v1/inbox", headers=headers)
    assert listed.status_code == 200, listed.text
    assert row.id not in {item["id"] for item in listed.json()}


def test_batch_actions_cover_every_branch(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "batch-owner", admin=True)
    owner = _user(db_session, "batch-owner-user")

    resolve_calls: list[int] = []
    import_calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        resolve_calls.append(item_id)

    async def fake_run_import(item_id: int, _selected_ids, _session_factory) -> None:
        import_calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)
    monkeypatch.setattr(inbox, "run_import", fake_run_import)

    collection = Collection(
        name="Batch target", slug="batch-target", path="batch-target"
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)

    set_collection_item = _make_item(db_session, owner)
    tag_item = _make_item(db_session, owner, requested_tags_json='["existing"]')
    retry_item = _make_item(
        db_session, owner, state=InboxItemState.FAILED, retryable=True, manifest_json=""
    )
    review_item = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json='{"kind": "direct", "title": "x"}',
    )
    not_ready_item = _make_item(db_session, owner, state=InboxItemState.CAPTURED)
    dismiss_item = _make_item(db_session, owner)

    set_collection = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={
            "item_ids": [set_collection_item.id],
            "action": "set_collection",
            "collection_id": collection.id,
        },
    )
    assert set_collection.status_code == 200
    assert set_collection.json()[0]["target_collection_id"] == collection.id

    response = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={
            "item_ids": [tag_item.id, tag_item.id],  # dedup via dict.fromkeys
            "action": "add_tags",
            "tags": ["new"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0]["requested_tags"]) == {"existing", "new"}

    retried = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [retry_item.id], "action": "retry"},
    )
    assert retried.status_code == 200
    assert retried.json()[0]["state"] == "captured"
    assert resolve_calls == [retry_item.id]

    imported = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [review_item.id, not_ready_item.id], "action": "import"},
    )
    assert imported.status_code == 200
    # not_ready_item hits `continue` for action="import" (state != REVIEW) and is
    # dropped from the output entirely; only review_item is returned.
    assert {row["id"] for row in imported.json()} == {review_item.id}
    assert import_calls == [review_item.id]

    dismissed = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [dismiss_item.id], "action": "dismiss"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()[0]["state"] == "dismissed"
