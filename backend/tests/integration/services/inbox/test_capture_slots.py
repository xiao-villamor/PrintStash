"""Capture-upload slots: the receipts that keep browser-uploaded bytes accounted for.

A browser capture uploads its files one at a time into per-file *slots*, and every slot's
bytes are owned by a storage receipt and a staging lease. That ownership is what this file
defends, because the failure it prevents is silent: bytes that are written but owned by
nothing are bytes nothing will ever delete, and bytes deleted without their receipt are
somebody's model gone.

So the rules asserted here are all about who owns what, and when. A slot is owner-scoped
and its upload is idempotent by content hash. A dismiss enqueues a delete *intent* before
committing, and keeps the bytes if it cannot. A failed cover write rolls back to a lease
that still points at the same object. A restart reconciles a published object that never
got its receipt. None of these are reachable through the router — they are the service's
own transaction boundaries — so they live here rather than in the API mirror.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import timedelta
from io import BytesIO
from typing import cast
from unittest.mock import MagicMock

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
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    ModelProvenanceSource,
    ModelSourceCover,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.db.session import get_session_factory
from app.schemas.inbox import CaptureUploadSlotsCreate, InboxImportRequest
from app.services import inbox, staging_leases
from app.services.auth import create_access_token
from app.services.source_covers import SourceCoverWrite
from app.services.storage_backend import CreationReceipt, StorageBackend
from app.services.storage_deletion import process_storage_delete_intents
from app.services.storage_ownership import provider_ref_for_backend
from tests.factories import build_model, build_user


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
    user = build_user(
        session, username=username, password="Password123", active=True, superuser=admin
    )
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


def _upload(session, slot, data: bytes = b"slot-owned"):
    """Upload *data* into *slot*. Defaults to the same bytes every time.

    Same bytes by default because the interesting cases are the two replays — one
    identical, one not — and each needs a baseline upload that is byte-for-byte
    what the replay will be compared against.
    """
    return inbox.upload_capture_slot(
        session,
        slot,
        stream=BytesIO(data),
        media_type="application/octet-stream",
    )


class TestUploadCaptureSlot:
    def test_persists_the_publication_provider_ref_before_an_active_switch(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = build_user(db_session, "slot-provider-switch", superuser=True)
        _row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        slot = slots[0]
        backend = inbox.get_backend()
        switched = MagicMock(spec=StorageBackend)
        switched.backend_name = "s3"
        switched.provider_id = "s3"
        switched.transport = "s3"
        switched_ref = provider_ref_for_backend(switched, namespace="test")
        monkeypatch.setattr(
            staging_leases,
            "provider_ref_for_backend",
            lambda *_args, **_kwargs: switched_ref,
        )

        uploaded = _upload(db_session, slot)

        expected_ref = provider_ref_for_backend(
            backend, namespace=backend.namespace_for(uploaded.storage_key or "")
        )
        persisted_slot = json.loads(uploaded.receipt_json or "{}")
        lease = db_session.exec(
            select(StagingLease).where(
                (StagingLease.capture_upload_slot_id == uploaded.id)
                | (StagingLease.capture_upload_slot_origin_id == uploaded.id)
            )
        ).one()
        persisted_lease = json.loads(lease.receipt_json or "{}")
        assert persisted_slot["provider_ref"] == expected_ref
        assert persisted_lease["provider_ref"] == expected_ref

    def test_rejects_a_remote_legacy_receipt_without_a_provider_probe(
        self, db_session: Session
    ) -> None:
        owner = build_user(db_session, "slot-remote-legacy", superuser=True)
        _row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        slot = slots[0]
        lease = db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot.id)
        ).one()
        legacy = {
            "key": slot.storage_key,
            "size": slot.size_bytes,
            "token": "legacy",
            "backend": "s3",
            "namespace": "bucket/prefix",
        }
        slot.receipt_json = json.dumps(legacy)
        lease.receipt_json = json.dumps(legacy)
        db_session.add(slot)
        db_session.add(lease)
        db_session.commit()
        backend = MagicMock(spec=StorageBackend)
        backend.backend_name = "s3"
        backend.provider_id = "s3"
        backend.transport = "s3"
        backend.namespace_for.return_value = "bucket/prefix"

        assert not staging_leases.reconcile_capture_slot(db_session, backend, slot)
        backend.creation_matches.assert_not_called()
        backend.adopt_existing.assert_not_called()
        backend.object_info.assert_not_called()

    def test_rejects_a_foreign_remote_receipt_without_a_provider_probe(
        self, db_session: Session
    ) -> None:
        owner = build_user(db_session, "slot-foreign-provider", superuser=True)
        _row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        slot = slots[0]
        lease = db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot.id)
        ).one()
        receipt = {
            "key": slot.storage_key,
            "size": slot.size_bytes,
            "token": "foreign",
            "backend": "s3",
            "namespace": "bucket/prefix",
            "provider_ref": "foreign-provider",
        }
        slot.receipt_json = json.dumps(receipt)
        lease.receipt_json = json.dumps(receipt)
        db_session.add(slot)
        db_session.add(lease)
        db_session.commit()
        backend = MagicMock(spec=StorageBackend)
        backend.backend_name = "s3"
        backend.provider_id = "s3"
        backend.transport = "s3"
        backend.namespace_for.return_value = "bucket/prefix"

        assert not staging_leases.reconcile_capture_slot(db_session, backend, slot)
        backend.creation_matches.assert_not_called()
        backend.adopt_existing.assert_not_called()
        backend.object_info.assert_not_called()

    def test_a_slot_is_invisible_to_anyone_but_its_owner(
        self, db_session: Session
    ) -> None:
        owner = build_user(db_session, "slot-owner", superuser=True)
        other = build_user(db_session, "slot-other")
        _row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )

        # `not_found` rather than `forbidden`: the other user must not learn that
        # the slot exists, and they are a superuser here to prove the scope is by
        # ownership rather than by privilege.
        with pytest.raises(HTTPException, match="not_found"):
            inbox.require_capture_slot(db_session, other, slots[0].id)

    def test_finalize_refuses_while_a_slot_is_still_empty(
        self, db_session: Session
    ) -> None:
        owner = build_user(db_session, "slot-owner", superuser=True)
        row, _slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )

        with pytest.raises(HTTPException, match="incomplete"):
            inbox.finalize_capture_upload(db_session, owner, row.id)

    def test_re_uploading_the_same_bytes_reuses_the_slot(
        self, db_session: Session
    ) -> None:
        # A browser retrying after a dropped connection. A second row here would
        # leave the first one's bytes owned by nothing.
        owner = build_user(db_session, "slot-owner", superuser=True)
        _row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        first = _upload(db_session, slots[0])

        replay = _upload(db_session, first)

        assert replay.id == first.id

    def test_re_uploading_different_bytes_is_refused(self, db_session: Session) -> None:
        owner = build_user(db_session, "slot-owner", superuser=True)
        _row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        uploaded = _upload(db_session, slots[0])

        with pytest.raises(ValueError, match="sha256"):
            _upload(db_session, uploaded, data=b"different!")

    def test_finalize_moves_the_item_to_review_once_every_slot_is_filled(
        self, db_session: Session
    ) -> None:
        owner = build_user(db_session, "slot-owner", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        _upload(db_session, slots[0])

        finalized = inbox.finalize_capture_upload(db_session, owner, row.id)

        assert finalized.state == InboxItemState.REVIEW

    def test_a_finalized_slot_accepts_no_further_upload(
        self, db_session: Session
    ) -> None:
        owner = build_user(db_session, "slot-owner", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        uploaded = _upload(db_session, slots[0])
        inbox.finalize_capture_upload(db_session, owner, row.id)

        with pytest.raises(ValueError, match="not_uploadable"):
            _upload(db_session, uploaded)

    @pytest.mark.anyio
    async def test_capture_slot_upload_cleans_temp_file_after_stream_disconnect(
        self, db_session: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
        incoming_dir = inbox.settings.incoming_dir
        incoming_dir.mkdir(parents=True)
        owner = build_user(db_session, "slot-disconnect", superuser=True)
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

    def test_capture_source_mismatch_rejects_before_slot_write(
        self, db_session: Session
    ) -> None:
        owner = build_user(db_session, "slot-source", superuser=True)
        raw = _slot_payload().model_dump(mode="json")
        # Two different pages on the same provider: the request says it is uploading
        # for model 1234 while the captured source declares model 9999, so the bytes
        # would be attributed to a model the user never captured.
        #
        # `source_item_id` has to be absent for this guard to be the one that fires.
        # When an item id is present the canonical URL is bound to it, and a
        # mismatched page is rejected a step earlier by the canonicalizer — a
        # stronger check on a narrower input. This is the residual case: no item id,
        # so both URLs canonicalize cleanly and only comparing them catches it.
        raw["capture_source"]["source_item_id"] = None
        raw["capture_source"]["canonical_url"] = (
            "https://makerworld.com/en/models/9999-other"
        )
        with pytest.raises(
            inbox.importer.ImportError_, match="capture_source_url_mismatch"
        ):
            inbox.create_capture_upload_slots(
                db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
            )
        assert db_session.exec(select(InboxItem)).all() == []

    def test_capture_source_from_another_provider_rejects_before_slot_write(
        self,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "slot-source-provider", superuser=True)
        raw = _slot_payload().model_dump(mode="json")
        raw["capture_source"]["canonical_url"] = "https://printables.com/model/1"

        # A URL that does not belong to the declared provider at all is refused when
        # the source is parsed: provider and URL are bound, so there is no canonical
        # form of a Printables URL under `makerworld`.
        with pytest.raises(
            inbox.importer.ImportError_, match="capture_source_url_mismatch"
        ):
            inbox.create_capture_upload_slots(
                db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
            )
        assert db_session.exec(select(InboxItem)).all() == []

    def test_capture_source_page_of_another_item_rejects_before_slot_write(
        self,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "slot-source-item", superuser=True)
        raw = _slot_payload().model_dump(mode="json")
        raw["capture_source"]["canonical_url"] = (
            "https://makerworld.com/en/models/9999-other"
        )

        # Item id 1234 with model 9999's page: the canonical URL is bound to the
        # item, so this never reaches the URL comparison.
        with pytest.raises(
            inbox.importer.ImportError_, match="capture_source_url_mismatch"
        ):
            inbox.create_capture_upload_slots(
                db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
            )
        assert db_session.exec(select(InboxItem)).all() == []

    def test_a_replayed_capture_slot_reassigns_both_of_its_leases(
        self,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "slot-restart", superuser=True)
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
        self,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "slot-crash-restart", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
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

    def test_capture_cover_attaches_before_raw_slot_receipt_is_released(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from PIL import Image

        owner = build_user(db_session, "slot-cover", superuser=True)
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
        model = build_model(
            db_session,
            name="Cover import",
            slug=f"cover-import-{uuid.uuid4().hex}",
            hash=uuid.uuid4().hex * 2,
        )
        source = ModelProvenanceSource(
            model_id=model.id,
            provider="makerworld",
            # All three of provider, item id and canonical URL have to match the
            # captured manifest: the cover is attached to *exactly one* provenance
            # source, and an attach that matched zero or two would silently skip
            # publication instead of raising.
            source_item_id="1234",
            canonical_url="https://makerworld.com/en/models/1234-widget",
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

    @pytest.mark.parametrize("failure", ["invalid_image", "postprocess"])
    def test_capture_cover_service_temp_is_cleaned_when_processing_fails(
        self,
        db_session: Session,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        failure: str,
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
        owner = build_user(db_session, f"cover-service-{failure}", superuser=True)
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


class TestCleanupCaptureSlots:
    def test_capture_slot_cleanup_later_failure_preserves_all_slot_ownership(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
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
        owner = build_user(db_session, "slot-cleanup-atomic", superuser=True)
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

        assert {
            slot.id for slot in db_session.exec(select(CaptureUploadSlot)).all()
        } >= {slot.id for slot in slots}
        assert len(db_session.exec(select(StagingLease)).all()) == 2
        assert db_session.exec(select(StorageDeleteIntent)).all() == []

    def test_the_delete_intent_processor_retries_rather_than_losing_bytes(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.db.models import StorageDeleteIntent
        from app.services import storage_deletion

        owner = build_user(db_session, "slot-retry-owner", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
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

    def test_capture_slot_cleanup_enqueues_before_post_commit_delete(
        self,
        db_session: Session,
    ) -> None:
        """Slot bytes remain until the committed outbox processor consumes receipt."""
        from app.db.models import StorageDeleteIntent
        from app.services.storage_deletion import process_storage_delete_intents

        owner = build_user(db_session, "slot-outbox-owner", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
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
        assert (
            db_session.exec(select(StorageDeleteIntent)).one().key == slot.storage_key
        )
        db_session.commit()
        assert backend.exists(slot.storage_key)

        assert process_storage_delete_intents().completed == 1
        assert not backend.exists(slot.storage_key)

    @pytest.mark.parametrize("created", [True, False])
    def test_finished_capture_rolls_back_cover_write_when_commit_fails(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, created: bool
    ) -> None:
        owner = build_user(
            db_session, f"cover-commit-failure-{created}", superuser=True
        )
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

    def test_a_rolled_back_slot_cleanup_leaves_everything_in_place(
        self,
        db_session: Session,
    ) -> None:
        from app.db.models import StorageDeleteIntent

        owner = build_user(db_session, "slot-rollback-owner", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
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


class _BackgroundTaskRecorder:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def add_task(self, function: object, *args: object, **kwargs: object) -> None:
        self.tasks.append((function, args, kwargs))


class TestImportItem:
    @pytest.mark.parametrize("requested", [["missing"], ["ok", "missing"]])
    def test_import_route_rejects_invalid_v2_selection_before_scheduling(
        self, db_session: Session, requested: list[str]
    ) -> None:
        owner = build_user(
            db_session, f"import-selection-route-{len(requested)}", superuser=True
        )
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


class TestRetry:
    def test_retry_returns_transferred_capture_slot_leases_to_review(
        self,
        db_session: Session,
    ) -> None:
        """A failed durable capture can be retried after its import job owned slots."""
        owner = build_user(db_session, "slot-retry-after-transfer", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
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
            select(StagingLease).where(
                StagingLease.capture_upload_slot_id == slots[0].id
            )
        ).one()
        assert lease.background_job_id is None
        assert lease.capture_upload_slot_origin_id is None

    def test_retry_partial_schedules_failed_selection_only(
        self, client: TestClient, db_session: Session, monkeypatch
    ) -> None:
        owner = build_user(db_session, "retry-partial-api", superuser=True)
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

        async def fake_run_import(
            item_id: int, selected_ids: list[str], _factory
        ) -> None:
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
        self,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "retry-selection-route", superuser=True)
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

    def test_retry_item_schedules_resolve_when_returned_to_captured(
        self, client: TestClient, db_session: Session, monkeypatch
    ) -> None:
        headers = _headers(db_session, "retry-success", admin=True)
        owner = build_user(db_session, "retry-success-owner", superuser=True)
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


class TestDismiss:
    def test_capture_slot_lease_uses_slot_owner_for_dismiss(
        self, db_session: Session
    ) -> None:
        """Dismiss finds the lease through the same capture-slot identity that created it."""
        owner = build_user(db_session, "slot-dismiss-owner", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )

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
        self,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "slot-dismiss-uploaded", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
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
                select(StagingLease).where(
                    StagingLease.capture_upload_slot_id == slot.id
                )
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
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = build_user(db_session, "slot-dismiss-retry", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        slot = inbox.upload_capture_slot(
            db_session,
            slots[0],
            stream=BytesIO(b"slot-owned"),
            media_type="application/octet-stream",
        )
        monkeypatch.setattr(
            inbox,
            "enqueue_creation_receipt",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("intent unavailable")
            ),
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

    def test_capture_slot_dismiss_preserves_unadoptable_collision(
        self,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "slot-collision-preserved", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
        slot = slots[0]
        assert slot.storage_key is not None
        backend = inbox.get_backend()
        backend.create_bytes(b"foreign-bytes", slot.storage_key)

        with pytest.raises(HTTPException, match="staging_cleanup_failed"):
            inbox.dismiss(db_session, row)
        assert backend.read_bytes(slot.storage_key) == b"foreign-bytes"
        assert db_session.get(CaptureUploadSlot, slot.id) is not None


class TestPruneExpiredBrowserLeases:
    def test_expired_uploaded_capture_slot_fails_inbox_after_durable_cleanup(
        self,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "slot-expiry-owner", superuser=True)
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, _slot_payload()
        )
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
