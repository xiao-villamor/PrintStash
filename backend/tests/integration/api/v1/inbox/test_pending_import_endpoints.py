"""Pending Import item, batch, upload-slot, and authentication endpoints.

The API is the boundary where untrusted capture metadata and browser bytes enter;
these cases keep validation failures from mutating another user's durable work.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    User,
)
from app.services.auth import create_access_token, hash_password


def _headers(session: Session, username: str) -> tuple[dict[str, str], User]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}, user


def _item(
    session: Session,
    owner: User,
    *,
    state: InboxItemState = InboxItemState.CAPTURED,
    source_kind: InboxSourceKind = InboxSourceKind.URL,
    title: str = "Captured model",
    retryable: bool = False,
) -> InboxItem:
    assert owner.id is not None
    row = InboxItem(
        owner_user_id=owner.id,
        source_kind=source_kind,
        source_url="https://example.com/model.stl",
        source_hostname="example.com",
        display_title=title,
        state=state,
        retryable=retryable,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _capture_source() -> dict[str, object]:
    return {
        "provider": "makerworld",
        "canonical_url": "https://makerworld.com/en/models/1234-widget",
        "source_item_id": "1234",
        "adapter_version": "extension-v1",
        "fields": {"title": {"value": "Widget", "origin": "confirmed"}},
        "tags": [],
    }


def _slot_request(data: bytes = b"slot-owned") -> dict[str, object]:
    return {
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


def _invalid_slot_requests() -> list[pytest.ParameterSet]:
    base_file = _slot_request()["files"]
    assert isinstance(base_file, list)
    declaration = base_file[0]
    assert isinstance(declaration, dict)
    return [
        pytest.param({**_slot_request(), "files": []}, id="empty-files"),
        pytest.param(
            {**_slot_request(), "files": [dict(declaration) for _ in range(101)]},
            id="too-many-files",
        ),
        pytest.param(
            {**_slot_request(), "files": [dict(declaration), dict(declaration)]},
            id="duplicate-id",
        ),
        pytest.param(
            {
                **_slot_request(),
                "files": [{**declaration, "size_bytes": 0}],
            },
            id="nonpositive-size",
        ),
        pytest.param(
            {
                **_slot_request(),
                "files": [{**declaration, "sha256": "not-a-sha256"}],
            },
            id="invalid-sha256",
        ),
    ]


class TestCapture:
    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"url": ""}, id="empty-url"),
            pytest.param(
                {"url": "https://example.com", "unknown": True}, id="extra-field"
            ),
            pytest.param(
                {"url": "https://example.com", "title": "x" * 256},
                id="title-over-limit",
            ),
            pytest.param(
                {"url": "https://example.com", "tags": ["tag"] * 101},
                id="tags-over-limit",
            ),
        ],
    )
    def test_rejects_malformed_or_over_limit_capture_input(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        body: dict[str, object],
    ) -> None:
        response = client.post("/api/v1/inbox", headers=auth_headers, json=body)

        assert response.status_code == 422, response.text
        assert db_session.exec(select(InboxItem)).all() == []


class TestListItems:
    def test_lists_only_the_current_users_items(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "inbox-list-owner")
        _other_headers, other = _headers(db_session, "inbox-list-other")
        own = _item(db_session, owner, title="Own item")
        _item(db_session, other, title="Foreign item")

        response = client.get("/api/v1/inbox", headers=headers)

        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()] == [own.id]

    def test_filters_completed_items_when_requested(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "inbox-list-completed")
        active = _item(db_session, owner, title="Active item")
        _item(
            db_session,
            owner,
            title="Completed item",
            state=InboxItemState.COMPLETED,
        )

        response = client.get("/api/v1/inbox?include_completed=false", headers=headers)

        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()] == [active.id]


class TestGetItem:
    def test_hides_another_users_item(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, _owner = _headers(db_session, "inbox-get-owner")
        _other_headers, other = _headers(db_session, "inbox-get-other")
        foreign = _item(db_session, other)

        response = client.get(f"/api/v1/inbox/{foreign.id}", headers=headers)

        assert response.status_code == 404, response.text


class TestUpdateItem:
    def test_updates_an_owned_review_title(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "inbox-update-owner")
        row = _item(db_session, owner, state=InboxItemState.REVIEW)

        response = client.patch(
            f"/api/v1/inbox/{row.id}",
            headers=headers,
            json={"title": "Reviewed title"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["display_title"] == "Reviewed title"
        db_session.refresh(row)
        assert row.display_title == "Reviewed title"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"title": "x" * 256}, id="title-over-limit"),
            pytest.param({"tags": ["tag"] * 101}, id="tags-over-limit"),
            pytest.param({"selected_ids": ["id"] * 501}, id="selection-over-limit"),
            pytest.param({"unknown": True}, id="extra-field"),
        ],
    )
    def test_rejects_invalid_updates_without_mutating_the_item(
        self,
        client: TestClient,
        db_session: Session,
        body: dict[str, object],
    ) -> None:
        headers, owner = _headers(db_session, f"inbox-update-invalid-{len(body)}")
        row = _item(db_session, owner, state=InboxItemState.REVIEW)
        original_title = row.display_title

        response = client.patch(f"/api/v1/inbox/{row.id}", headers=headers, json=body)

        assert response.status_code == 422, response.text
        db_session.refresh(row)
        assert row.display_title == original_title


class TestResolveItem:
    def test_rejects_an_item_that_is_not_resolvable(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "inbox-resolve-review")
        row = _item(db_session, owner, state=InboxItemState.REVIEW)

        response = client.post(f"/api/v1/inbox/{row.id}/resolve", headers=headers)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "pending_import_not_resolvable"


class TestImportItem:
    def test_rejects_an_item_that_is_not_ready_for_import(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "inbox-import-captured")
        row = _item(db_session, owner)

        response = client.post(
            f"/api/v1/inbox/{row.id}/import",
            headers=headers,
            json={"selected_ids": []},
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "pending_import_not_ready"


class TestRetryItem:
    def test_rejects_a_non_retryable_item(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "inbox-retry-nonretryable")
        row = _item(
            db_session,
            owner,
            state=InboxItemState.FAILED,
            retryable=False,
        )

        response = client.post(f"/api/v1/inbox/{row.id}/retry", headers=headers)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "pending_import_not_retryable"


class TestDismissItem:
    def test_hides_another_users_item_from_dismissal(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, _owner = _headers(db_session, "inbox-dismiss-owner")
        _other_headers, other = _headers(db_session, "inbox-dismiss-other")
        foreign = _item(db_session, other)

        response = client.delete(f"/api/v1/inbox/{foreign.id}", headers=headers)

        assert response.status_code == 404, response.text
        db_session.refresh(foreign)
        assert foreign.state == InboxItemState.CAPTURED


class TestBatchItems:
    def test_batch_dismiss_does_not_mutate_a_foreign_item(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "inbox-batch-owner")
        _foreign_headers, foreign_owner = _headers(
            db_session, "inbox-batch-foreign-owner"
        )
        own = _item(db_session, owner, title="Owned batch item")
        foreign = _item(db_session, foreign_owner, title="Foreign batch item")

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers,
            json={"item_ids": [own.id, foreign.id], "action": "dismiss"},
        )

        assert response.status_code == 404, response.text
        db_session.refresh(foreign)
        assert foreign.state == InboxItemState.CAPTURED

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"item_ids": [], "action": "dismiss"}, id="empty-items"),
            pytest.param(
                {"item_ids": list(range(501)), "action": "dismiss"},
                id="too-many-items",
            ),
            pytest.param(
                {"item_ids": [1], "action": "unsupported"},
                id="unsupported-action",
            ),
        ],
    )
    def test_rejects_invalid_batch_requests_without_mutating_items(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        body: dict[str, object],
    ) -> None:
        items_before = db_session.exec(select(InboxItem)).all()

        response = client.post("/api/v1/inbox/batch", headers=auth_headers, json=body)

        assert response.status_code == 422, response.text
        assert db_session.exec(select(InboxItem)).all() == items_before


class TestCaptureUploadSlots:
    def test_creates_a_distinct_optional_cover_slot(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, _owner = _headers(db_session, "slot-cover-owner")
        body = _slot_request()
        cover = b"cover"
        body["cover"] = {
            "id": "cover.png",
            "filename": "cover.png",
            "media_type": "image/png",
            "size_bytes": len(cover),
            "sha256": hashlib.sha256(cover).hexdigest(),
        }

        response = client.post(
            "/api/v1/inbox/capture-upload-slots", headers=headers, json=body
        )

        assert response.status_code == 201, response.text
        assert [(slot["role"], slot["state"]) for slot in response.json()["slots"]] == [
            ("file", "pending"),
            ("cover", "pending"),
        ]

    def test_creates_owned_pending_slots_for_declared_files(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "slot-create-owner")

        response = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=headers,
            json=_slot_request(),
        )

        assert response.status_code == 201, response.text
        assert response.json()["item"]["owner_user_id"] == owner.id
        assert response.json()["slots"][0]["state"] == "pending"
        slot = db_session.exec(select(CaptureUploadSlot)).one()
        assert slot.state == CaptureUploadSlotState.PENDING

    @pytest.mark.parametrize(
        "body",
        _invalid_slot_requests(),
    )
    def test_rejects_invalid_upload_declarations_without_creating_slots(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        body: dict[str, object],
    ) -> None:
        response = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=auth_headers,
            json=body,
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(InboxItem)).all() == []
        assert db_session.exec(select(CaptureUploadSlot)).all() == []

    def test_rejects_mismatched_slot_bytes_without_publishing_them(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, _owner = _headers(db_session, "slot-mismatch-owner")
        created = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=headers,
            json=_slot_request(),
        )
        slot_id = created.json()["slots"][0]["id"]

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{slot_id}",
            headers={**headers, "content-type": "application/octet-stream"},
            content=b"wrong-bytes",
        )

        assert response.status_code == 400, response.text
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot is not None
        assert slot.state == CaptureUploadSlotState.PENDING

    def test_uploads_exact_bytes_to_an_owned_pending_slot(
        self, client: TestClient, db_session: Session
    ) -> None:
        data = b"slot-owned"
        headers, _owner = _headers(db_session, "slot-upload-owner")
        created = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=headers,
            json=_slot_request(data),
        )
        slot_id = created.json()["slots"][0]["id"]

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{slot_id}",
            headers={**headers, "content-type": "application/octet-stream"},
            content=data,
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "uploaded"
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot is not None and slot.state == CaptureUploadSlotState.UPLOADED

    def test_hides_another_users_upload_slot(
        self, client: TestClient, db_session: Session
    ) -> None:
        data = b"slot-owned"
        owner_headers, _owner = _headers(db_session, "slot-private-owner")
        foreign_headers, _foreign = _headers(db_session, "slot-private-foreign")
        created = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=owner_headers,
            json=_slot_request(data),
        )
        slot_id = created.json()["slots"][0]["id"]

        response = client.put(
            f"/api/v1/inbox/capture-upload-slots/{slot_id}",
            headers={**foreign_headers, "content-type": "application/octet-stream"},
            content=data,
        )

        assert response.status_code == 404, response.text
        slot = db_session.get(CaptureUploadSlot, slot_id)
        assert slot is not None and slot.state == CaptureUploadSlotState.PENDING

    def test_finalizes_after_all_required_slots_are_uploaded(
        self, client: TestClient, db_session: Session
    ) -> None:
        data = b"slot-owned"
        headers, _owner = _headers(db_session, "slot-finalize-owner")
        created = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=headers,
            json=_slot_request(data),
        )
        item_id = created.json()["item"]["id"]
        slot_id = created.json()["slots"][0]["id"]
        uploaded = client.put(
            f"/api/v1/inbox/capture-upload-slots/{slot_id}",
            headers={**headers, "content-type": "application/octet-stream"},
            content=data,
        )
        assert uploaded.status_code == 200, uploaded.text

        response = client.post(
            f"/api/v1/inbox/{item_id}/capture-upload-finalize", headers=headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "review"

    def test_rejects_finalization_while_a_required_slot_is_pending(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, _owner = _headers(db_session, "slot-finalize-pending")
        created = client.post(
            "/api/v1/inbox/capture-upload-slots",
            headers=headers,
            json=_slot_request(),
        )
        item_id = created.json()["item"]["id"]

        response = client.post(
            f"/api/v1/inbox/{item_id}/capture-upload-finalize",
            headers=headers,
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "capture_upload_slots_incomplete"


class TestRouterAuthentication:
    @pytest.mark.parametrize(
        ("method", "path", "request_kwargs"),
        [
            pytest.param(
                "POST",
                "/api/v1/inbox",
                {"json": {"url": "https://example.com"}},
                id="capture",
            ),
            pytest.param("GET", "/api/v1/inbox", {}, id="list"),
            pytest.param(
                "POST",
                "/api/v1/inbox/batch",
                {"json": {"item_ids": [1], "action": "dismiss"}},
                id="batch",
            ),
            pytest.param(
                "POST", "/api/v1/inbox/browser-upload", {}, id="browser-upload"
            ),
            pytest.param(
                "POST",
                "/api/v1/inbox/capture-upload-slots",
                {"json": _slot_request()},
                id="create-slots",
            ),
            pytest.param(
                "PUT", "/api/v1/inbox/capture-upload-slots/slot", {}, id="put-slot"
            ),
            pytest.param("GET", "/api/v1/inbox/1", {}, id="get"),
            pytest.param("PATCH", "/api/v1/inbox/1", {"json": {}}, id="update"),
            pytest.param("POST", "/api/v1/inbox/1/resolve", {}, id="resolve"),
            pytest.param("POST", "/api/v1/inbox/1/import", {"json": {}}, id="import"),
            pytest.param("POST", "/api/v1/inbox/1/retry", {}, id="retry"),
            pytest.param(
                "POST", "/api/v1/inbox/1/capture-upload-finalize", {}, id="finalize"
            ),
            pytest.param("DELETE", "/api/v1/inbox/1", {}, id="dismiss"),
        ],
    )
    def test_requires_authentication_for_every_inbox_route(
        self,
        client: TestClient,
        method: str,
        path: str,
        request_kwargs: dict[str, object],
    ) -> None:
        response = client.request(method, path, **request_kwargs)

        assert response.status_code == 401, response.text
