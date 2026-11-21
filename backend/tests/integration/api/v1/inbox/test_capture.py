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

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import CaptureUploadSlot, InboxItem
from app.modules.ingestion import inbox
from tests.factories.content import binary_stl
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

    def test_resolves_the_source_in_the_background(
        self, client: TestClient, user_headers, no_egress, work_engine
    ) -> None:
        created = client.post(
            "/api/v1/inbox",
            headers=user_headers("capture-schedules"),
            json={"url": "https://example.com/model"},
        )
        work_engine.drain()

        assert no_egress == [created.json()["id"]]

    @pytest.mark.parametrize("on_ready", [None, "review", "import"])
    def test_imports_a_resolved_browser_direct_file(
        self, client: TestClient, auth_headers, monkeypatch, work_engine, on_ready
    ) -> None:
        source = "https://files.example.com/bracket.stl"
        headers = auth_headers
        monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

        async def download(url, *, window, **_options):
            assert url == source
            target = window.directory / "bracket.stl"
            target.write_bytes(binary_stl())
            window.seal(target)
            return target, target.name

        monkeypatch.setattr(inbox.importer, "download_to_staging", download)
        created = client.post(
            "/api/v1/inbox",
            headers=headers,
            json={"url": source, "source_kind": "browser", "on_ready": on_ready},
        )
        assert created.status_code == 202, created.text
        path = f"/api/v1/inbox/{created.json()['id']}"
        work_engine.drain()
        ready = client.get(path, headers=headers).json()
        assert ready["manifest"]["kind"] == "direct"
        if on_ready != "import":
            assert ready["state"] == "review"
            accepted = client.post(
                f"{path}/import", headers=headers, json={"selected_ids": ["direct"]}
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["state"] == "importing"
        work_engine.drain()
        completed = client.get(path, headers=headers).json()
        assert completed["state"] == "completed", completed
        assert completed["resulting_model_id"] is not None

    def test_explicit_resend_flags_existing_pending_capture(
        self, client: TestClient, auth_headers, no_egress
    ) -> None:
        payload = {"url": "https://files.example.com/bracket.stl", "on_ready": "import"}
        created = client.post("/api/v1/inbox", headers=auth_headers, json=payload)
        repeated = client.post(
            "/api/v1/inbox",
            headers=auth_headers,
            json={**payload, "import_again": True},
        )

        assert created.status_code == 202
        assert repeated.status_code == 409
        assert repeated.json()["detail"] == "capture_already_pending"
        assert [
            item["id"]
            for item in client.get("/api/v1/inbox", headers=auth_headers).json()
        ] == [created.json()["id"]]

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
