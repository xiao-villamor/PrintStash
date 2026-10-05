"""Uploading a browser capture's files one slot at a time.

Slots exist because a browser extension holds the bytes, not the server: the app hands
back one upload URL per declared file and the extension PUTs into each. The router's job
in that exchange is to refuse anything that would let unaccounted bytes reach disk. It
checks the declared `Content-Length` **before** reading a byte, then counts what actually
arrives and stops at the cap either way — a lying header must not be able to fill the
staging directory.

Slot bytes are lease-owned, so the placeholder is committed before the stream is consumed:
a process killed mid-upload leaves an identity-bound partial that startup reconciliation
can find, rather than an anonymous temp file nothing owns. Finalizing is gated on every
slot having arrived.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager
from threading import get_ident

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select
from starlette.requests import Request

from app.api.command_actor import CommandActor
from app.core import config
from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    BrowserDevice,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxItemState,
    StagingLease,
    User,
)
from app.db.session import get_session_factory, override_session_factory
from app.modules.ingestion import inbox
from tests.fakes.thread_sessions import ThreadBoundSessionFactory
from tests.integration.api.v1.inbox.conftest import CANONICAL_URL, capture_source

BODY = b"slot-owned"
OCTET = {"content-type": "application/octet-stream"}


def _create_payload(data: bytes = BODY) -> dict:
    return {
        "source_url": CANONICAL_URL,
        "capture_source": capture_source(),
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


def _receives(*chunks: bytes):
    """An ASGI `receive` that hands back exactly these body chunks, then stops."""
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks or (b"",))
    ]
    pending = iter(messages)

    async def receive() -> dict[str, object]:
        return next(pending)

    return receive


def _put_request(slot_id: str, receive, *, content_length: int | None) -> Request:
    headers = [(b"content-type", b"application/octet-stream")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "PUT",
            "scheme": "http",
            "path": f"/api/v1/inbox/capture-upload-slots/{slot_id}",
            "raw_path": b"/api/v1/inbox/capture-upload-slots",
            "query_string": b"",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("testclient", 123),
        },
        receive,
    )


def _slot_actor(session: Session, slot_id: str):
    """The user a slot belongs to, so the route can be called without the router."""
    from app.db.models import User

    slot = session.get(CaptureUploadSlot, slot_id)
    assert slot is not None
    item = session.get(InboxItem, slot.inbox_item_id)
    assert item is not None
    owner = session.get(User, item.owner_user_id)
    assert owner is not None
    return CommandActor.from_user(owner)


@pytest.fixture
def staging(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point staging at a throwaway directory so uploads land where we can see them."""
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    inbox.settings.incoming_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def slots(client: TestClient, staging):
    """Ask for upload slots and hand back the created item together with them."""

    def run(headers: dict[str, str], data: bytes = BODY) -> tuple[int, list[dict]]:
        response = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=headers,
            json=_create_payload(data),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return body["item"]["id"], body["slots"]

    return run


class TestCreateCaptureUploadSlots:
    def test_oversized_batch_rolls_back_earlier_slot_reservations(
        self,
        client: TestClient,
        db_session: Session,
        user_headers,
        staging,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_max_gb", 1)
        payload = _create_payload()
        payload["files"] = [
            {
                **payload["files"][0],
                "id": f"large-{index}",
                "filename": f"large-{index}.3mf",
                "size_bytes": 600 * 1024 * 1024,
            }
            for index in range(2)
        ]
        before = len(db_session.exec(select(StagingLease)).all())

        response = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=user_headers("batch-rollback"),
            json=payload,
        )

        assert response.status_code == 507, response.text
        db_session.expire_all()
        assert len(db_session.exec(select(StagingLease)).all()) == before

    def test_a_five_file_capture_uses_one_active_review_allocation(
        self, client: TestClient, user_headers, staging
    ) -> None:
        headers = user_headers("five-file-capture")
        payload = _create_payload()
        payload["files"] = [
            {
                **payload["files"][0],
                "id": f"widget-{index}",
                "filename": f"widget-{index}.3mf",
            }
            for index in range(5)
        ]

        response = client.post(
            "/api/v1/inbox/capture-upload-slots", headers=headers, json=payload
        )

        assert response.status_code == 201, response.text
        assert len(response.json()["slots"]) == 5

        for _ in range(3):
            admitted = client.post(
                "/api/v1/inbox/capture-upload-slots",
                headers=headers,
                json=_create_payload(),
            )
            assert admitted.status_code == 201, admitted.text
        rejected = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=headers,
            json=_create_payload(),
        )
        assert rejected.status_code == 507, rejected.text

    def test_cancel_releases_an_unfinished_capture(
        self, client: TestClient, db_session: Session, user_headers, staging
    ) -> None:
        headers = user_headers("cancel-slots")
        created = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=headers,
            json=_create_payload(),
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["item"]["id"]
        denied = client.delete(
            f"/api/v1/inbox/{item_id}/capture-upload",
            headers=user_headers("other-cancel-user"),
        )
        assert denied.status_code == 404

        cancelled = client.delete(
            f"/api/v1/inbox/{item_id}/capture-upload", headers=headers
        )

        assert cancelled.status_code == 204, cancelled.text
        db_session.expire_all()
        assert db_session.exec(select(StagingLease)).all() == []
        assert db_session.exec(select(CaptureUploadSlot)).all() == []

    def test_hands_back_one_slot_per_declared_file(
        self, client: TestClient, user_headers, staging
    ) -> None:
        response = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=user_headers("slots-create"),
            json=_create_payload(),
        )

        assert response.status_code == 201, response.text
        assert len(response.json()["slots"]) == 1

    def test_puts_the_item_in_the_queue_awaiting_its_bytes(
        self, client: TestClient, user_headers, staging
    ) -> None:
        response = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=user_headers("slots-state"),
            json=_create_payload(),
        )

        assert response.json()["item"]["state"] != InboxItemState.REVIEW.value

    def test_refuses_provenance_that_does_not_match_the_source_url(
        self, client: TestClient, user_headers, staging
    ) -> None:
        payload = _create_payload()
        payload["capture_source"] = capture_source(
            canonical_url="https://makerworld.com/en/models/9999-other"
        )

        response = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=user_headers("slots-mismatch"),
            json=payload,
        )

        assert response.status_code == 400, response.text

    def test_reports_staging_that_has_no_room_left(
        self, client: TestClient, user_headers, staging, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def full(*_args: object, **_kwargs: object):
            raise inbox.staging_leases.StagingCapacityExceeded("staging_full")

        monkeypatch.setattr(inbox, "create_capture_upload_slots", full)

        response = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=user_headers("slots-full"),
            json=_create_payload(),
        )

        # 507 rather than 500: the request was fine, the disk was not.
        assert response.status_code == 507, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, staging
    ) -> None:
        response = client.post(
            "/api/v1/inbox/capture-upload-slots", json=_create_payload()
        )

        assert response.status_code == 401, response.text


class TestPutCaptureUploadSlot:
    def test_accepts_the_bytes_the_slot_was_opened_for(
        self, client: TestClient, user_headers, slots
    ) -> None:
        headers = user_headers("slot-put")
        _, opened = slots(headers)

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**headers, **OCTET},
            content=BODY,
        )

        assert response.status_code == 200, response.text

    def test_marks_the_slot_uploaded(
        self, client: TestClient, db_session: Session, user_headers, slots
    ) -> None:
        headers = user_headers("slot-put-state")
        _, opened = slots(headers)

        client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**headers, **OCTET},
            content=BODY,
        )

        db_session.expire_all()
        slot = db_session.get(CaptureUploadSlot, opened[0]["id"])
        assert slot is not None
        assert slot.state == CaptureUploadSlotState.UPLOADED

    def test_accepts_the_same_bytes_twice(
        self, client: TestClient, user_headers, slots
    ) -> None:
        headers = user_headers("slot-replay")
        _, opened = slots(headers)
        url = f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}"
        client.put(url, headers={**headers, **OCTET}, content=BODY)

        replay = client.put(url, headers={**headers, **OCTET}, content=BODY)

        # A retried upload is normal; it must not be a conflict.
        assert replay.status_code == 200, replay.text

    def test_refuses_bytes_that_do_not_match_the_declared_hash(
        self, client: TestClient, user_headers, slots
    ) -> None:
        headers = user_headers("slot-wrong-bytes")
        _, opened = slots(headers)

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**headers, **OCTET},
            content=b"different!",
        )

        assert response.status_code == 400, response.text
        assert "sha256" in response.json()["detail"]

    def test_refuses_a_declared_length_past_the_upload_cap(
        self, client: TestClient, user_headers, slots, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = user_headers("slot-declared-too-large")
        _, opened = slots(headers)
        monkeypatch.setitem(_overlay, "max_upload_mb", 0.000004)

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**headers, **OCTET},
            content=BODY,
        )

        # The route's own per-file guard answers, through the full HTTP stack: the
        # request ceiling now sits above the per-file cap, so a body that is only
        # over the *file* limit reaches route code instead of being swallowed by
        # the middleware as a generic `request_too_large`.
        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "upload_too_large"

    def test_refuses_a_body_past_the_whole_request_ceiling(
        self, client: TestClient, user_headers, slots, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The outer ceiling is still there, and still answers differently.

        It bounds what the process will buffer at all — a lying `content-length`,
        or a stream with no end — so it refuses before route code runs and says
        `request_too_large`. Collapsing the two details would leave a client
        unable to tell "your file is too big" from "your request is malformed".
        """
        headers = user_headers("slot-request-too-large")
        _, opened = slots(headers)
        # Zero per-file cap puts the request ceiling at the overhead allowance
        # alone, which a padded body then exceeds.
        monkeypatch.setitem(_overlay, "max_upload_mb", 0.000001)
        monkeypatch.setattr(config, "MULTIPART_OVERHEAD_BYTES", 4)

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**headers, **OCTET},
            content=b"x" * 64,
        )

        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "request_too_large"

    def test_stores_the_streamed_body_at_the_route_itself(
        self, db_session: Session, slots, user_headers
    ) -> None:
        from app.api.v1 import inbox as inbox_api

        headers = user_headers("slot-route-stream")
        _, opened = slots(headers)

        uploaded = asyncio.run(
            inbox_api.put_capture_upload_slot(
                opened[0]["id"],
                _put_request(
                    opened[0]["id"], _receives(b"slot-", b"owned"), content_length=None
                ),
                actor=_slot_actor(db_session, opened[0]["id"]),
            )
        )

        # The body arrives in chunks and is written as it is read, never buffered.
        assert uploaded.state == CaptureUploadSlotState.UPLOADED
        assert uploaded.size_bytes == len(BODY)

    def test_stops_a_body_that_grows_past_the_cap_at_the_route_itself(
        self, db_session: Session, slots, user_headers, monkeypatch
    ) -> None:
        from fastapi import HTTPException

        from app.api.v1 import inbox as inbox_api

        headers = user_headers("slot-route-stream-cap")
        _, opened = slots(headers)
        monkeypatch.setitem(_overlay, "max_upload_mb", 0.000004)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                inbox_api.put_capture_upload_slot(
                    opened[0]["id"],
                    _put_request(
                        opened[0]["id"],
                        _receives(b"slot-", b"owned"),
                        content_length=None,
                    ),
                    actor=_slot_actor(db_session, opened[0]["id"]),
                )
            )

        # No declared length to check, so the count of what actually arrived is
        # the only thing standing between a lying client and a full disk.
        assert exc_info.value.status_code == 413
        assert exc_info.value.detail == "upload_too_large"

    def test_refuses_a_body_that_grows_past_the_cap_mid_stream(
        self, client: TestClient, user_headers, slots, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = user_headers("slot-streamed-too-large")
        _, opened = slots(headers)
        monkeypatch.setitem(_overlay, "max_upload_mb", 0.000004)

        def chunks():
            yield BODY

        # No content-length, so the declared-size gate cannot fire: a lying or
        # absent header must not be able to fill the staging directory.
        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**headers, **OCTET},
            content=chunks(),
        )

        assert response.status_code == 413, response.text

    def test_refuses_a_content_length_that_is_not_a_number(
        self, client: TestClient, user_headers, slots
    ) -> None:
        headers = user_headers("slot-bad-length")
        _, opened = slots(headers)

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**headers, **OCTET, "content-length": "not-a-number"},
            content=BODY,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_content_length"

    def test_reports_bytes_the_storage_backend_calls_too_large(
        self, db_session: Session, slots, user_headers, monkeypatch
    ) -> None:
        from fastapi import HTTPException

        from app.api.v1 import inbox as inbox_api

        headers = user_headers("slot-storage-too-large")
        _, opened = slots(headers)

        def too_large(*_args: object, **_kwargs: object):
            raise inbox.storage.UploadTooLarge("upload_too_large")

        monkeypatch.setattr(inbox, "upload_capture_slot", too_large)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                inbox_api.put_capture_upload_slot(
                    opened[0]["id"],
                    _put_request(opened[0]["id"], _receives(BODY), content_length=None),
                    actor=_slot_actor(db_session, opened[0]["id"]),
                )
            )

        assert exc_info.value.status_code == 413
        assert exc_info.value.detail == "upload_too_large"

    def test_reports_a_staging_lease_that_cannot_be_taken(
        self, db_session: Session, slots, user_headers, monkeypatch
    ) -> None:
        from fastapi import HTTPException

        from app.api.v1 import inbox as inbox_api

        headers = user_headers("slot-lease-conflict")
        _, opened = slots(headers)

        def taken(*_args: object, **_kwargs: object):
            raise inbox.staging_leases.StagingLeaseError("staging_lease_conflict")

        monkeypatch.setattr(inbox.staging_leases, "prepare_capture_slot_staging", taken)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                inbox_api.put_capture_upload_slot(
                    opened[0]["id"],
                    _put_request(opened[0]["id"], _receives(BODY), content_length=None),
                    actor=_slot_actor(db_session, opened[0]["id"]),
                )
            )

        # 409, not 500: another request holds the lease and a retry may work.
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "staging_lease_conflict"

    def test_still_publishes_when_the_staging_cleanup_fails(
        self, db_session: Session, slots, user_headers, monkeypatch
    ) -> None:
        from app.api.v1 import inbox as inbox_api

        headers = user_headers("slot-cleanup-fails")
        _, opened = slots(headers)

        real = inbox.staging_leases.remove_capture_slot_staging
        calls = {"n": 0}

        def exploding_after_publish(*args: object, **kwargs: object):
            calls["n"] += 1
            # The publish itself clears the staging row; the route's own tidy-up
            # afterwards is the call this test breaks.
            if calls["n"] == 1:
                return real(*args, **kwargs)
            raise RuntimeError("staging ledger unavailable")

        monkeypatch.setattr(
            inbox.staging_leases,
            "remove_capture_slot_staging",
            exploding_after_publish,
        )

        uploaded = asyncio.run(
            inbox_api.put_capture_upload_slot(
                opened[0]["id"],
                _put_request(opened[0]["id"], _receives(BODY), content_length=None),
                actor=_slot_actor(db_session, opened[0]["id"]),
            )
        )

        # The bytes are already published and owned; a failed tidy-up is not a
        # reason to tell the extension its upload failed.
        assert uploaded.state == CaptureUploadSlotState.UPLOADED

    def test_refuses_a_slot_that_belongs_to_another_account(
        self, client: TestClient, user_headers, slots
    ) -> None:
        _, opened = slots(user_headers("slot-owner"))

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**user_headers("slot-stranger"), **OCTET},
            content=BODY,
        )

        assert response.status_code == 404, response.text

    def test_refuses_a_slot_that_does_not_exist(
        self, client: TestClient, user_headers, staging
    ) -> None:
        response = client.put(
            "/api/v1/inbox/capture-upload-slots/not-a-slot",
            headers={**user_headers("slot-missing"), **OCTET},
            content=BODY,
        )

        assert response.status_code == 404, response.text

    def test_refuses_a_slot_that_was_already_finalized(
        self, client: TestClient, user_headers, slots
    ) -> None:
        headers = user_headers("slot-finalized")
        item_id, opened = slots(headers)
        url = f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}"
        client.put(url, headers={**headers, **OCTET}, content=BODY)
        client.post(f"/api/v1/inbox/{item_id}/capture-upload-finalize", headers=headers)

        response = client.put(url, headers={**headers, **OCTET}, content=BODY)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "capture_upload_slot_not_uploadable"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, user_headers, slots
    ) -> None:
        _, opened = slots(user_headers("slot-anon"))

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}", content=BODY
        )

        assert response.status_code == 401, response.text


class TestFinalizeCaptureUpload:
    def test_moves_the_item_to_review_once_every_slot_arrived(
        self, client: TestClient, user_headers, slots
    ) -> None:
        headers = user_headers("finalize-ready")
        item_id, opened = slots(headers)
        client.put(
            f"/api/v1/inbox/capture-upload-slots/{opened[0]['id']}",
            headers={**headers, **OCTET},
            content=BODY,
        )

        response = client.post(
            f"/api/v1/inbox/{item_id}/capture-upload-finalize", headers=headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "review"

    def test_refuses_while_a_slot_is_still_missing(
        self, client: TestClient, user_headers, slots
    ) -> None:
        headers = user_headers("finalize-incomplete")
        item_id, _ = slots(headers)

        response = client.post(
            f"/api/v1/inbox/{item_id}/capture-upload-finalize", headers=headers
        )

        assert response.status_code == 409, response.text
        assert "incomplete" in response.json()["detail"]

    def test_refuses_an_item_that_belongs_to_another_account(
        self, client: TestClient, user_headers, slots
    ) -> None:
        item_id, _ = slots(user_headers("finalize-owner"))

        response = client.post(
            f"/api/v1/inbox/{item_id}/capture-upload-finalize",
            headers=user_headers("finalize-stranger"),
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, user_headers, slots
    ) -> None:
        item_id, _ = slots(user_headers("finalize-anon"))

        response = client.post(f"/api/v1/inbox/{item_id}/capture-upload-finalize")

        assert response.status_code == 401, response.text


class TestCaptureUploadExecution:
    @pytest.mark.asyncio
    async def test_keeps_loop_responsive_during_capture_slot_lookup(
        self, db_session, slots, user_headers, loop_handshake
    ):
        from app.api.v1 import inbox as inbox_api

        _, opened = slots(user_headers("slot-responsive-sql"))
        slot_id = opened[0]["id"]
        owner = _slot_actor(db_session, slot_id)
        wait_for_loop, observations = loop_handshake
        engine = db_session.get_bind()

        def delayed_query(connection, cursor, statement, parameters, context, many):
            if "capture_upload_slots" in statement.lower():
                wait_for_loop()

        event.listen(engine, "before_cursor_execute", delayed_query)
        try:
            uploaded = await inbox_api.put_capture_upload_slot(
                slot_id,
                _put_request(slot_id, _receives(BODY), content_length=None),
                actor=owner,
            )
        finally:
            event.remove(engine, "before_cursor_execute", delayed_query)

        assert uploaded.state == CaptureUploadSlotState.UPLOADED
        assert observations
        assert all(observations)

    @pytest.mark.asyncio
    async def test_keeps_loop_responsive_during_capture_spool_writes(
        self, db_session, slots, user_headers, loop_handshake, monkeypatch
    ):
        from app.api.v1 import inbox as inbox_api
        from app.modules.storage.storage_backend.runtime import get_backend

        _, opened = slots(user_headers("slot-responsive-spool"))
        slot_id = opened[0]["id"]
        wait_for_loop, observations = loop_handshake
        original_open = inbox.staging_leases.open_capture_slot_staging

        @contextmanager
        def delayed_open(*args, **kwargs):
            with original_open(*args, **kwargs) as target:

                class Writer:
                    def write(self, chunk):
                        if chunk:
                            wait_for_loop()
                        return target.write(chunk)

                yield Writer()

        monkeypatch.setattr(
            inbox.staging_leases, "open_capture_slot_staging", delayed_open
        )
        uploaded = await inbox_api.put_capture_upload_slot(
            slot_id,
            _put_request(slot_id, _receives(b"slot-", b"owned"), content_length=None),
            actor=_slot_actor(db_session, slot_id),
        )
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert get_backend().read_bytes(slot.storage_key) == BODY
        assert uploaded.state == CaptureUploadSlotState.UPLOADED
        assert observations == [True, True]

    @pytest.mark.asyncio
    async def test_applies_backpressure_between_capture_chunks(
        self, db_session, slots, user_headers, monkeypatch
    ):
        from app.api.v1 import inbox as inbox_api

        _, opened = slots(user_headers("slot-bounded-chunks"))
        slot_id = opened[0]["id"]
        written = bytearray()
        original_open = inbox.staging_leases.open_capture_slot_staging

        @contextmanager
        def observed_open(*args, **kwargs):
            with original_open(*args, **kwargs) as target:

                class Writer:
                    def write(self, chunk):
                        result = target.write(chunk)
                        written.extend(chunk)
                        return result

                yield Writer()

        messages = iter([b"slot-", b"owned"])

        async def receive():
            chunk = next(messages)
            if chunk == b"owned":
                assert written == b"slot-"
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": chunk != b"owned",
            }

        monkeypatch.setattr(
            inbox.staging_leases, "open_capture_slot_staging", observed_open
        )
        uploaded = await inbox_api.put_capture_upload_slot(
            slot_id,
            _put_request(slot_id, receive, content_length=None),
            actor=_slot_actor(db_session, slot_id),
        )
        assert uploaded.state == CaptureUploadSlotState.UPLOADED
        assert written == BODY

    @pytest.mark.asyncio
    async def test_cleans_capture_spool_after_disconnect(
        self, db_session, slots, user_headers
    ):
        from starlette.requests import ClientDisconnect

        from app.api.v1 import inbox as inbox_api
        from app.modules.storage.storage_backend.runtime import get_backend

        _, opened = slots(user_headers("slot-disconnected"))
        slot_id = opened[0]["id"]
        messages = iter(
            [
                {"type": "http.request", "body": b"slot-", "more_body": True},
                {"type": "http.disconnect"},
            ]
        )

        async def receive():
            return next(messages)

        with pytest.raises(ClientDisconnect):
            await inbox_api.put_capture_upload_slot(
                slot_id,
                _put_request(slot_id, receive, content_length=None),
                actor=_slot_actor(db_session, slot_id),
            )
        db_session.expire_all()
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot.state == CaptureUploadSlotState.PENDING
        assert not get_backend().exists(slot.storage_key)
        assert not inbox.staging_leases.capture_slot_staging_path(slot_id).exists()
        assert db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot_id)
        ).one()

    @pytest.mark.asyncio
    async def test_releases_worker_capacity_while_waiting_for_body(
        self, db_session, slots, user_headers
    ):
        from anyio import to_thread
        from fastapi.concurrency import run_in_threadpool

        from app.api.v1 import inbox as inbox_api

        _, opened = slots(user_headers("slot-idle-capacity"))
        slot_id = opened[0]["id"]
        owner = _slot_actor(db_session, slot_id)
        limiter = to_thread.current_default_thread_limiter()
        before = limiter.total_tokens

        async def receive():
            # With one available worker, unrelated synchronous work must finish
            # while this request waits for its next network chunk.
            marker = await asyncio.wait_for(
                run_in_threadpool(lambda: "progressed"), timeout=1
            )
            assert marker == "progressed"
            return {"type": "http.request", "body": BODY, "more_body": False}

        limiter.total_tokens = 1
        try:
            uploaded = await inbox_api.put_capture_upload_slot(
                slot_id,
                _put_request(slot_id, receive, content_length=None),
                actor=owner,
            )
        finally:
            limiter.total_tokens = before
        assert uploaded.state == CaptureUploadSlotState.UPLOADED

    @pytest.mark.asyncio
    async def test_releases_capture_resources_on_cancellation(
        self, db_session, slots, user_headers, loop_handshake, monkeypatch
    ):
        import anyio
        from fastapi.concurrency import run_in_threadpool

        from app.api.v1 import inbox as inbox_api
        from app.modules.storage.storage_backend.runtime import get_backend

        _, opened = slots(user_headers("slot-cancelled-body"))
        slot_id = opened[0]["id"]
        owner = _slot_actor(db_session, slot_id)
        wait_for_loop, observations = loop_handshake
        original_open = inbox.staging_leases.open_capture_slot_staging
        contexts = []
        targets = []

        @contextmanager
        def observed_open(*args, **kwargs):
            with original_open(*args, **kwargs) as target:
                targets.append(target)
                try:
                    yield target
                finally:
                    wait_for_loop()

        def retain_context(*args, **kwargs):
            context = observed_open(*args, **kwargs)
            contexts.append(context)
            return context

        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"slot-", "more_body": True}
            scope.cancel()
            await anyio.sleep(0)
            raise AssertionError("cancelled receive must not continue")

        monkeypatch.setattr(
            inbox.staging_leases, "open_capture_slot_staging", retain_context
        )
        with anyio.CancelScope() as scope:
            await inbox_api.put_capture_upload_slot(
                slot_id,
                _put_request(slot_id, receive, content_length=None),
                actor=owner,
            )
        closed_before_cleanup = targets[0].closed
        exit_progress = list(observations)
        # Keep the context alive until observing closure; garbage collection
        # must not make an unclosed descriptor look like explicit cleanup.
        if not closed_before_cleanup:
            await run_in_threadpool(contexts[0].__exit__, None, None, None)
        assert scope.cancelled_caught
        assert closed_before_cleanup
        assert exit_progress == [True]
        db_session.expire_all()
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot.state == CaptureUploadSlotState.PENDING
        assert not get_backend().exists(slot.storage_key)
        assert not inbox.staging_leases.capture_slot_staging_path(slot_id).exists()
        assert db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot_id)
        ).one()


@pytest.fixture
def capture_command_sessions(db_session):
    previous = get_session_factory()
    factory = ThreadBoundSessionFactory(db_session.get_bind())
    override_session_factory(factory)
    try:
        yield factory
    finally:
        override_session_factory(previous)


async def _asgi_capture_upload(app, headers, slot_id, body):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.put(
            f"/api/v1/inbox/capture-upload-slots/{slot_id}",
            headers={**headers, **OCTET},
            content=body,
        )


class TestCaptureCommandOwnership:
    @pytest.mark.asyncio
    async def test_keeps_capture_lifecycle_sessions_owned(
        self, app, slots, make_user, headers_for, capture_command_sessions
    ):
        owner = make_user("capture-owned-lifecycle", superuser=True)
        headers = headers_for(owner)
        item_id, opened = slots(headers)
        response = await _asgi_capture_upload(app, headers, opened[0]["id"], BODY)
        assert response.status_code == 200, response.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            finalized = await client.post(
                f"/api/v1/inbox/{item_id}/capture-upload-finalize", headers=headers
            )
            assert finalized.status_code == 200, finalized.text
            assert finalized.json()["state"] == "review"
            imported = await client.post(
                f"/api/v1/inbox/{item_id}/import",
                headers=headers,
                json={"selected_ids": []},
            )
        assert imported.status_code == 200, imported.text
        assert imported.json()["state"] == "importing"
        assert capture_command_sessions.active_count == 0
        assert capture_command_sessions.opened_count >= 6

    @pytest.mark.asyncio
    async def test_body_wait_holds_no_session(
        self, app, slots, user_headers, capture_command_sessions
    ):
        from fastapi.concurrency import run_in_threadpool

        headers = user_headers("capture-no-idle-session")
        _, opened = slots(headers)
        slot_id = opened[0]["id"]
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous_tokens = limiter.total_tokens
        loop_thread = get_ident()

        async def body():
            assert capture_command_sessions.active_count == 0
            assert (
                await asyncio.wait_for(
                    run_in_threadpool(lambda: "worker-progressed"), timeout=1
                )
                == "worker-progressed"
            )
            yield BODY

        limiter.total_tokens = 1
        try:
            response = await _asgi_capture_upload(app, headers, slot_id, body())
        finally:
            limiter.total_tokens = previous_tokens
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "uploaded"
        assert capture_command_sessions.active_count == 0
        assert capture_command_sessions.opened_count >= 4
        assert loop_thread not in capture_command_sessions.thread_ids

    @pytest.mark.asyncio
    async def test_keeps_loop_responsive_during_sql_commands(
        self,
        app,
        db_session,
        slots,
        user_headers,
        loop_handshake,
        capture_command_sessions,
    ):
        headers = user_headers("capture-worker-sql")
        _, opened = slots(headers)
        wait_for_loop, observations = loop_handshake
        engine = db_session.get_bind()

        def delayed_query(connection, cursor, statement, parameters, context, many):
            if "capture_upload_slots" in statement.lower():
                wait_for_loop()

        event.listen(engine, "before_cursor_execute", delayed_query)
        try:
            response = await _asgi_capture_upload(app, headers, opened[0]["id"], BODY)
        finally:
            event.remove(engine, "before_cursor_execute", delayed_query)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "uploaded"
        assert observations
        assert all(observations)
        assert capture_command_sessions.active_count == 0

    @pytest.mark.asyncio
    async def test_keeps_loop_responsive_during_spool_writes(
        self,
        app,
        db_session,
        slots,
        user_headers,
        loop_handshake,
        capture_command_sessions,
        monkeypatch,
    ):
        from app.modules.storage.storage_backend.runtime import get_backend

        headers = user_headers("capture-worker-spool")
        _, opened = slots(headers)
        slot_id = opened[0]["id"]
        wait_for_loop, observations = loop_handshake
        original_open = inbox.staging_leases.open_capture_slot_staging

        @contextmanager
        def delayed_open(*args, **kwargs):
            with original_open(*args, **kwargs) as target:

                class Writer:
                    def write(self, chunk):
                        if chunk:
                            wait_for_loop()
                        return target.write(chunk)

                yield Writer()

        async def body():
            yield b"slot-"
            yield b"owned"

        monkeypatch.setattr(
            inbox.staging_leases, "open_capture_slot_staging", delayed_open
        )
        response = await _asgi_capture_upload(app, headers, slot_id, body())
        db_session.expire_all()
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot is not None
        assert get_backend().read_bytes(slot.storage_key) == BODY
        assert response.status_code == 200, response.text
        assert observations == [True, True]
        assert capture_command_sessions.active_count == 0

    @pytest.mark.asyncio
    async def test_revalidates_user_before_publication(
        self, app, db_session, slots, user_headers, capture_command_sessions
    ):
        from app.modules.storage.storage_backend.runtime import get_backend

        headers = user_headers("capture-auth-changed")
        _, opened = slots(headers)
        slot_id = opened[0]["id"]
        owner_id = _slot_actor(db_session, slot_id).user_id

        async def body():
            assert capture_command_sessions.active_count == 0
            owner = db_session.get(User, owner_id)
            assert owner is not None
            owner.auth_version += 1
            db_session.add(owner)
            db_session.commit()
            yield BODY

        response = await _asgi_capture_upload(app, headers, slot_id, body())
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "not_authenticated"
        db_session.expire_all()
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot is not None and slot.state is CaptureUploadSlotState.PENDING
        assert not get_backend().exists(slot.storage_key)
        assert not inbox.staging_leases.capture_slot_staging_path(slot_id).exists()
        assert db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot_id)
        ).one()
        assert capture_command_sessions.active_count == 0

    @pytest.mark.asyncio
    async def test_revalidates_browser_device_before_publication(
        self, app, client, db_session, slots, user_headers, capture_command_sessions
    ):
        from app.modules.storage.storage_backend.runtime import get_backend

        headers = user_headers("capture-device-revoked")
        paired = client.post("/api/v1/browser-pairings", headers=headers)
        assert paired.status_code == 201, paired.text
        claimed = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": paired.json()["code"], "name": "Capture ownership browser"},
        )
        assert claimed.status_code == 200, claimed.text
        device_headers = {"Authorization": f"Bearer {claimed.json()['credential']}"}
        _, opened = slots(device_headers)
        slot_id = opened[0]["id"]
        device_id = db_session.exec(select(BrowserDevice)).one().id

        async def body():
            assert capture_command_sessions.active_count == 0
            device = db_session.get(BrowserDevice, device_id)
            assert device is not None
            device.revoked_at = utcnow()
            db_session.add(device)
            db_session.commit()
            yield BODY

        response = await _asgi_capture_upload(app, device_headers, slot_id, body())
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "invalid_browser_credential"
        db_session.expire_all()
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot is not None and slot.state is CaptureUploadSlotState.PENDING
        assert not get_backend().exists(slot.storage_key)
        assert not inbox.staging_leases.capture_slot_staging_path(slot_id).exists()
        assert capture_command_sessions.active_count == 0

    @pytest.mark.asyncio
    async def test_cleans_owned_spool_after_asgi_cancellation(
        self,
        app,
        db_session,
        slots,
        user_headers,
        capture_command_sessions,
        monkeypatch,
    ):
        from app.modules.storage.storage_backend.runtime import get_backend

        headers = user_headers("capture-asgi-cancel")
        _, opened = slots(headers)
        slot_id = opened[0]["id"]
        original_open = inbox.staging_leases.open_capture_slot_staging
        targets = []

        @contextmanager
        def observed_open(*args, **kwargs):
            with original_open(*args, **kwargs) as target:
                targets.append(target)
                yield target

        async def body():
            yield b"slot-"
            assert capture_command_sessions.active_count == 0
            scope.cancel()
            await anyio.sleep(0)
            raise AssertionError("cancelled network body must not continue")

        monkeypatch.setattr(
            inbox.staging_leases, "open_capture_slot_staging", observed_open
        )
        with anyio.CancelScope() as scope:
            await _asgi_capture_upload(app, headers, slot_id, body())
        assert scope.cancelled_caught
        assert len(targets) == 1 and targets[0].closed
        assert capture_command_sessions.active_count == 0
        db_session.expire_all()
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot is not None and slot.state is CaptureUploadSlotState.PENDING
        assert not get_backend().exists(slot.storage_key)
        assert not inbox.staging_leases.capture_slot_staging_path(slot_id).exists()
        assert db_session.exec(
            select(StagingLease).where(StagingLease.capture_upload_slot_id == slot_id)
        ).one()

    @pytest.mark.asyncio
    async def test_closes_spool_after_native_acquisition_cancellation(
        self,
        app,
        db_session,
        slots,
        user_headers,
        capture_command_sessions,
        monkeypatch,
    ):
        from contextlib import ExitStack, suppress
        from threading import Event

        from app.api.v1 import inbox as inbox_api
        from app.modules.storage.storage_backend.runtime import get_backend

        headers = user_headers("capture-native-open-cancel")
        _, slots_opened = slots(headers)
        slot_id = slots_opened[0]["id"]
        opened = Event()
        release = Event()
        retained = []
        cleanup = ExitStack()
        original = inbox_api._open_capture_spool

        def observed_open(slot):
            context, target = original(slot)
            retained.append((context, target))
            cleanup.callback(target.close)
            opened.set()
            assert release.wait(timeout=5), "test must release the owned worker"
            return context, target

        monkeypatch.setattr(inbox_api, "_open_capture_spool", observed_open)
        request = asyncio.create_task(_asgi_capture_upload(app, headers, slot_id, BODY))
        try:
            assert await anyio.to_thread.run_sync(opened.wait, 5)
            assert capture_command_sessions.active_count == 0
            request.cancel()
            await asyncio.sleep(0)
            assert not request.done(), "cancellation must join the owned worker"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, timeout=5)

            assert len(retained) == 1
            assert retained[0][1].closed, (
                "acquired descriptor must close before cancellation returns"
            )
            assert capture_command_sessions.active_count == 0
            db_session.expire_all()
            slot = db_session.get(CaptureUploadSlot, slot_id)
            assert slot is not None and slot.state is CaptureUploadSlotState.PENDING
            assert not get_backend().exists(slot.storage_key)
            assert not inbox.staging_leases.capture_slot_staging_path(slot_id).exists()
            assert db_session.exec(
                select(StagingLease).where(
                    StagingLease.capture_upload_slot_id == slot_id
                )
            ).one()
        finally:
            release.set()
            request.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(request, timeout=5)
            cleanup.close()
