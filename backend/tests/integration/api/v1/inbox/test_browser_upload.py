"""Accepting model bytes the browser already had, together with where they came from.

This is the one capture route that takes a file body, and the provenance that rides along
with it is untrusted input from an extension. It is validated **before** a byte is staged:
a provider name that is not canonical, a canonical URL still carrying a signed query, or a
stray `signed_url` field all refuse the upload with nothing written. Staging first and
checking after would leave orphaned bytes on every rejected capture.

A well-formed capture is staged as a v2 manifest so the review UI shows the extension's
own field-by-field provenance rather than a guess made from the filename.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import InboxItem
from tests.integration.api.v1.inbox.conftest import CANONICAL_URL, capture_source

FILE = {"file": ("widget.3mf", b"browser-owned", "application/octet-stream")}

UNTRUSTED_SOURCES = [
    pytest.param(capture_source(provider="MakerWorld"), id="uncanonical-provider"),
    pytest.param(
        capture_source(canonical_url=f"{CANONICAL_URL}?token=signed"),
        id="signed-canonical-url",
    ),
    pytest.param(
        {**capture_source(), "signed_url": "https://cdn.example/file?sig=secret"},
        id="extra-signed-url",
    ),
]


@pytest.fixture
def staging(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    return tmp_path


class TestCaptureBrowserUpload:
    def test_accepts_the_file_the_browser_had(
        self, client: TestClient, user_headers, staging
    ) -> None:
        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers=user_headers("browser-accepts"),
            data={"source_url": CANONICAL_URL},
            files=FILE,
        )

        assert response.status_code == 201, response.text

    def test_stages_extension_provenance_as_a_v2_manifest(
        self, client: TestClient, user_headers, staging
    ) -> None:
        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers=user_headers("browser-manifest"),
            data={
                "source_url": CANONICAL_URL,
                "capture_source": json.dumps(capture_source()),
            },
            files=FILE,
        )

        manifest = response.json()["manifest"]
        assert manifest["schema_version"] == 2
        assert manifest["source"] == capture_source()

    def test_records_the_uploaded_file_in_the_manifest(
        self, client: TestClient, user_headers, staging
    ) -> None:
        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers=user_headers("browser-manifest-files"),
            data={
                "source_url": CANONICAL_URL,
                "capture_source": json.dumps(capture_source()),
            },
            files=FILE,
        )

        assert response.json()["manifest"]["files"] == [
            {"id": "widget.3mf", "name": "widget.3mf", "file_type": "3mf", "size": 13}
        ]

    @pytest.mark.parametrize("source", UNTRUSTED_SOURCES)
    def test_refuses_provenance_it_does_not_trust(
        self, client: TestClient, user_headers, staging, source: dict
    ) -> None:
        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers=user_headers(f"browser-untrusted-{len(source)}"),
            data={
                "source_url": CANONICAL_URL,
                "capture_source": json.dumps(source),
            },
            files={
                "file": ("widget.3mf", b"must-not-stage", "application/octet-stream")
            },
        )

        assert response.status_code == 400, response.text

    @pytest.mark.parametrize("source", UNTRUSTED_SOURCES)
    def test_stages_nothing_when_it_refuses_the_provenance(
        self,
        client: TestClient,
        db_session: Session,
        user_headers,
        staging,
        source: dict,
    ) -> None:
        client.post(
            "/api/v1/inbox/browser-upload",
            headers=user_headers(f"browser-unstaged-{len(source)}"),
            data={
                "source_url": CANONICAL_URL,
                "capture_source": json.dumps(source),
            },
            files={
                "file": ("widget.3mf", b"must-not-stage", "application/octet-stream")
            },
        )

        # Staging first and checking after leaves orphaned bytes on every reject.
        assert db_session.exec(select(InboxItem)).all() == []
        assert not (staging / "_incoming").exists()

    def test_refuses_a_file_with_no_name(
        self, client: TestClient, user_headers, staging
    ) -> None:
        boundary = "----printstash-test-boundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="source_url"\r\n\r\n'
            f"{CANONICAL_URL}\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename=""\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
            "browser-owned\r\n"
            f"--{boundary}--\r\n"
        ).encode()

        # httpx turns an empty filename into a plain field, so the multipart body
        # is written by hand to reach the route's own guard.
        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers={
                **user_headers("browser-unnamed"),
                "content-type": f"multipart/form-data; boundary={boundary}",
            },
            content=body,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "filename_required"

    def test_refuses_a_source_url_that_is_not_public(
        self, client: TestClient, user_headers, staging
    ) -> None:
        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers=user_headers("browser-private"),
            data={"source_url": "https://user:password@example.com/model"},
            files=FILE,
        )

        assert response.status_code == 400, response.text

    def test_reports_a_file_past_the_upload_cap(
        self, client: TestClient, user_headers, staging, monkeypatch
    ) -> None:
        from app.services import inbox

        def too_large(*_args: object, **_kwargs: object):
            raise inbox.storage.UploadTooLarge("upload_too_large")

        monkeypatch.setattr(inbox, "create_browser_upload", too_large)

        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers=user_headers("browser-too-large"),
            data={"source_url": CANONICAL_URL},
            files=FILE,
        )

        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "upload_too_large"

    def test_reports_staging_that_has_no_room_left(
        self, client: TestClient, user_headers, staging, monkeypatch
    ) -> None:
        from app.services import inbox

        def full(*_args: object, **_kwargs: object):
            raise inbox.staging_leases.StagingCapacityExceeded("staging_full")

        monkeypatch.setattr(inbox, "create_browser_upload", full)

        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers=user_headers("browser-staging-full"),
            data={"source_url": CANONICAL_URL},
            files=FILE,
        )

        assert response.status_code == 507, response.text

    def test_accepts_a_paired_browser_credential(
        self, client: TestClient, db_session: Session, user_headers, staging
    ) -> None:
        headers = user_headers("browser-paired")
        code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
        credential = client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": "Firefox"}
        ).json()["credential"]

        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers={"Authorization": f"Bearer {credential}"},
            data={"source_url": CANONICAL_URL},
            files=FILE,
        )

        assert response.status_code == 201, response.text

    def test_rejects_a_revoked_browser_credential(
        self, client: TestClient, db_session: Session, user_headers, staging
    ) -> None:
        from app.core.time import utcnow
        from app.db.models import BrowserDevice

        headers = user_headers("browser-revoked")
        code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
        claimed = client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": "Firefox"}
        ).json()
        device = db_session.get(BrowserDevice, claimed["device"]["id"])
        assert device is not None
        device.revoked_at = utcnow()
        db_session.commit()

        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers={"Authorization": f"Bearer {claimed['credential']}"},
            data={"source_url": CANONICAL_URL},
            files=FILE,
        )

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "invalid_browser_credential"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, staging
    ) -> None:
        response = client.post(
            "/api/v1/inbox/browser-upload",
            data={"source_url": CANONICAL_URL},
            files=FILE,
        )

        assert response.status_code == 401, response.text
