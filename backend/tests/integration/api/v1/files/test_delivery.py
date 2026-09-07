"""Authorized delivery revalidates exact representations without public caching."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import build_file, build_model

PAYLOAD = b"solid delivery\nendsolid delivery\n"
MODIFIED = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def stored_artifact(db_session):
    backend = get_backend()
    key = backend.blob_key("delivery", 1, "part.stl")
    backend.write_bytes(PAYLOAD, key)
    return build_file(
        db_session,
        build_model(db_session),
        path=key,
        filename="part.stl",
        sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        size_bytes=len(PAYLOAD),
        uploaded_at=MODIFIED,
    )


class TestAuthorizedDelivery:
    def test_revalidates_authenticated_original_privately(
        self, client, auth_headers, stored_artifact
    ):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download", headers=auth_headers
        )

        assert (
            response.headers["cache-control"] == "private, max-age=0, must-revalidate"
        )

    def test_uses_the_original_digest_as_validator(
        self, client, auth_headers, stored_artifact
    ):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download", headers=auth_headers
        )

        assert response.headers["etag"] == f'"{stored_artifact.sha256}"'

    def test_returns_no_body_for_matching_validator(
        self, client, auth_headers, stored_artifact
    ):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download",
            headers={**auth_headers, "If-None-Match": f'W/"{stored_artifact.sha256}"'},
        )

        assert (response.status_code, response.content) == (304, b"")

    def test_prioritizes_etag_over_modification_date(
        self, client, auth_headers, stored_artifact
    ):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download",
            headers={
                **auth_headers,
                "If-None-Match": '"different"',
                "If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT",
            },
        )

        assert response.content == PAYLOAD

    def test_evaluates_modification_date(self, client, auth_headers, stored_artifact):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download",
            headers={
                **auth_headers,
                "If-Modified-Since": "Thu, 01 Jan 2026 00:00:00 GMT",
            },
        )

        assert response.status_code == 304

    def test_authorizes_before_conditional_response(self, client, stored_artifact):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download",
            headers={"If-None-Match": "*"},
        )

        assert response.status_code == 401

    def test_serves_exact_local_range(self, client, auth_headers, stored_artifact):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download",
            headers={**auth_headers, "Range": "bytes=2-7"},
        )

        assert (response.status_code, response.content) == (206, PAYLOAD[2:8])

    def test_ignores_range_when_if_range_does_not_match(
        self, client, auth_headers, stored_artifact
    ):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download",
            headers={**auth_headers, "Range": "bytes=2-7", "If-Range": '"old"'},
        )

        assert (response.status_code, response.content) == (200, PAYLOAD)

    @pytest.mark.parametrize(
        "suffix", ["download-url", "download-direct"], ids=["url", "direct"]
    )
    def test_removes_obsolete_delivery_route(
        self, client, auth_headers, stored_artifact, suffix
    ):
        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/{suffix}",
            headers=auth_headers,
            follow_redirects=False,
        )

        assert response.status_code == 404
