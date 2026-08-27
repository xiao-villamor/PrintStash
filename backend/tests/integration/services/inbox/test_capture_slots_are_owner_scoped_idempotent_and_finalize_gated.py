"""Defends capture slots are owner scoped idempotent and finalize gated at the services inbox integration boundary.

A regression could cross an owner boundary or lose a captured import during a retry.
"""

from __future__ import annotations

from ._inbox_api_shared import (
    BackgroundJob,
    BytesIO,
    CaptureUploadSlot,
    CaptureUploadSlotsCreate,
    CaptureUploadSlotState,
    ClientDisconnect,
    HTTPException,
    InboxItem,
    InboxItemState,
    Request,
    Session,
    StagingLease,
    StorageDeleteIntent,
    _overlay,
    _slot_payload,
    _user,
    cast,
    hashlib,
    inbox,
    inbox_api,
    io,
    process_storage_delete_intents,
    pytest,
    select,
    timedelta,
    utcnow,
)


def test_capture_slots_are_owner_scoped_and_idempotent(
    db_session: Session,
) -> None:
    owner = _user(db_session, "slot-owner")
    other = _user(db_session, "slot-other", admin=False)
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = slots[0]
    with pytest.raises(HTTPException, match="not_found"):
        inbox.require_capture_slot(db_session, other, slot.id)
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

    assert row.owner_user_id == owner.id
    assert replay.id == slot.id


def test_finalize_capture_upload_requires_every_slot_then_enters_review(
    db_session: Session,
) -> None:
    owner = _user(db_session, "slot-finalize-owner")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    slot = slots[0]
    with pytest.raises(HTTPException, match="incomplete"):
        inbox.finalize_capture_upload(db_session, owner, row.id)
    uploaded = inbox.upload_capture_slot(
        db_session,
        slot,
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )

    finalized = inbox.finalize_capture_upload(db_session, owner, row.id)

    assert finalized.state == InboxItemState.REVIEW
    with pytest.raises(ValueError, match="not_uploadable"):
        inbox.upload_capture_slot(
            db_session,
            uploaded,
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
