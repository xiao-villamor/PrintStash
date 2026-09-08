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

    def test_denies_revoked_conditional_access(
        self, client, auth_headers, stored_artifact
    ):
        created = client.post(
            f"/api/v1/models/{stored_artifact.model_id}/shares",
            headers=auth_headers,
            json={"allow_download": True},
        ).json()
        client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{stored_artifact.id}/download",
            headers={"If-None-Match": f'"{stored_artifact.sha256}"'},
        )

        assert response.status_code == 404

    def test_keeps_shared_original_noncacheable(
        self, client, auth_headers, stored_artifact
    ):
        created = client.post(
            f"/api/v1/models/{stored_artifact.model_id}/shares",
            headers=auth_headers,
            json={"allow_download": True},
        ).json()

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{stored_artifact.id}/download"
        )

        assert response.headers["cache-control"] == "private, no-store"

    def test_keeps_slicer_original_noncacheable(
        self, client, auth_headers, stored_artifact
    ):
        url = client.get(
            f"/api/v1/files/{stored_artifact.id}/slicer-url", headers=auth_headers
        ).json()["url"]

        response = client.get(url)

        assert response.headers["cache-control"] == "private, no-store"

    def test_revalidates_authenticated_thumbnail_privately(
        self, client, auth_headers, stored_artifact
    ):
        backend = get_backend()
        backend.write_bytes(b"thumbnail", backend.thumbnail_key(stored_artifact.id))

        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/thumbnail", headers=auth_headers
        )

        assert (
            response.headers["cache-control"] == "private, max-age=0, must-revalidate"
        )

    def test_preserves_inline_thumbnail_disposition(
        self, client, auth_headers, stored_artifact
    ):
        backend = get_backend()
        backend.write_bytes(b"thumbnail", backend.thumbnail_key(stored_artifact.id))

        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/thumbnail", headers=auth_headers
        )

        assert response.headers["content-disposition"].startswith("inline;")

    def test_uses_a_separate_converted_validator(
        self, client, auth_headers, db_session
    ):
        payload = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        backend = get_backend()
        key = backend.blob_key("conversion", 1, "part.obj")
        backend.write_bytes(payload, key)
        artifact = build_file(
            db_session,
            build_model(db_session),
            filename="part.obj",
            path=key,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )

        response = client.get(
            f"/api/v1/files/{artifact.id}/stl",
            headers={**auth_headers, "If-None-Match": f'"{artifact.sha256}"'},
        )

        assert response.status_code == 200
        assert response.headers["etag"] != f'"{artifact.sha256}"'


class TestDeliveryObservability:
    def test_records_selected_delivery_strategy(
        self, client, auth_headers, stored_artifact
    ):
        from app.core.metrics import registry

        labels = {"provider": "local", "purpose": "download", "strategy": "local"}
        before = (
            registry.get_sample_value("printstash_delivery_strategy_total", labels) or 0
        )

        response = client.get(
            f"/api/v1/files/{stored_artifact.id}/download", headers=auth_headers
        )

        assert response.content == PAYLOAD
        assert (
            registry.get_sample_value("printstash_delivery_strategy_total", labels)
            == before + 1
        )

    def test_reports_delivery_capability_without_signing(self, client, auth_headers):
        response = client.get("/api/v1/health/details", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["components"]["storage"]["delivery"] == {
            "mode": "local",
            "native_candidate": False,
            "ranges": True,
        }
