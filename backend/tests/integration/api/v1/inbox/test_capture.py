"""Capturing a URL into somebody's pending-import queue.

The capture endpoint is the widest entry point in the app: a paired browser extension can
call it with no login. So two things are asserted here before anything else. The URL is
**sanitized at the boundary** — credentials in the URL are refused outright, and a signed
query parameter is stripped before the row is written, because that row is shown in the UI
and copied into provenance. And a capture that carries extension provenance but no user
file is refused **before** anything is persisted: a half-created item with slots nobody
will ever upload to is an item that sits in the queue forever.

Resolution happens in the background, and only for a plain URL capture — a browser capture
brings its own metadata and must not be re-fetched.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import CaptureUploadSlot, InboxItem
from app.services import inbox
from tests.integration.api.v1.inbox.conftest import CANONICAL_URL, capture_source


class TestCapture:
    def test_accepts_a_url_into_the_queue(
        self, client: TestClient, user_headers, no_egress
    ) -> None:
        response = client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-accepts"),
            json={"url": "https://example.com/model", "title": "Bracket"},
        )

        assert response.status_code == 202, response.text
        assert response.json()["state"] == "captured"

    def test_schedules_the_source_to_be_resolved(
        self, client: TestClient, user_headers, no_egress
    ) -> None:
        created = client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-schedules"),
            json={"url": "https://example.com/model"},
        )

        assert no_egress == [created.json()["id"]]

    def test_strips_a_secret_from_the_url_it_stores(
        self, client: TestClient, user_headers, no_egress
    ) -> None:
        response = client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-strips"),
            json={"url": "https://example.com/model?token=secret&view=files#fragment"},
        )

        # The stored URL is shown in the UI and copied into provenance.
        assert response.json()["source_url"] == "https://example.com/model?view=files"

    def test_refuses_a_url_carrying_credentials(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-credentials"),
            json={"url": "https://user:password@example.com/model"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "url_invalid"

    def test_reports_a_source_the_importer_refuses(
        self, client: TestClient, user_headers, monkeypatch
    ) -> None:
        def blocked(_url: str) -> None:
            raise inbox.importer.ImportError_("private_address_blocked")

        monkeypatch.setattr(inbox.importer, "validate_public_url", blocked)

        response = client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-blocked"),
            json={"url": "https://example.com/model"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "private_address_blocked"

    def test_refuses_extension_provenance_with_no_user_file(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-no-file"),
            json={"url": CANONICAL_URL, "capture_source": capture_source()},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "user_file_required"

    def test_persists_nothing_when_it_refuses_a_capture_with_no_user_file(
        self, client: TestClient, db_session: Session, user_headers
    ) -> None:
        client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-no-file-empty"),
            json={"url": CANONICAL_URL, "capture_source": capture_source()},
        )

        # A half-created item with slots nobody uploads to never leaves the queue.
        assert db_session.exec(select(InboxItem)).all() == []
        assert db_session.exec(select(CaptureUploadSlot)).all() == []

    def test_refuses_an_explicit_browser_capture_with_no_user_file(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-browser-no-file"),
            json={
                "url": CANONICAL_URL,
                "capture_source": capture_source(),
                "source_kind": "browser",
            },
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "user_file_required"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/inbox", json={"url": "https://example.com/model"}
        )

        assert response.status_code == 401, response.text
