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

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.requests import Request

from app.core import config
from app.core.config import _overlay
from app.db.models import (
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxItemState,
)
from app.services import inbox
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


def _slot_owner(session: Session, slot_id: str):
    """The user a slot belongs to, so the route can be called without the router."""
    from app.db.models import User

    slot = session.get(CaptureUploadSlot, slot_id)
    assert slot is not None
    item = session.get(InboxItem, slot.inbox_item_id)
    assert item is not None
    owner = session.get(User, item.owner_user_id)
    assert owner is not None
    return owner


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
                current_user=_slot_owner(db_session, opened[0]["id"]),
                session=db_session,
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
                    current_user=_slot_owner(db_session, opened[0]["id"]),
                    session=db_session,
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
                    current_user=_slot_owner(db_session, opened[0]["id"]),
                    session=db_session,
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
                    current_user=_slot_owner(db_session, opened[0]["id"]),
                    session=db_session,
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
                current_user=_slot_owner(db_session, opened[0]["id"]),
                session=db_session,
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
