"""Canonical metadata belongs to the Artifact, even when response bytes are cached."""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.modules.storage.artifact_content import resolve
from app.modules.storage.artifact_delivery import DeliveryRequest, plan_artifact
from app.modules.storage.artifact_materializer import ArtifactMaterializer, CachePolicy
from app.modules.storage.delivery_contracts import BrowserDownload
from app.modules.storage.materializer_runtime import bind_materializer, get_materializer
from app.modules.storage.storage_backend.runtime import bind_backend, get_backend
from tests.fakes.counting_remote_storage import CountingRemoteStorage


@pytest.fixture
def cached_artifact(make_model, make_file, tmp_path):
    old_backend, old_cache = get_backend(), get_materializer()
    backend = CountingRemoteStorage()
    cache = ArtifactMaterializer(
        tmp_path / "cache", lambda: CachePolicy(enabled=True, headroom_bytes=0)
    )
    bind_backend(backend)
    bind_materializer(cache)
    source = tmp_path / "source.gcode"
    source.write_bytes(b"G28\nG1 X1\n")
    row = make_file(
        make_model("Cached delivery"),
        filename="source.gcode",
        path=str(source),
        size_bytes=source.stat().st_size,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    yield row, backend, cache
    bind_backend(old_backend)
    bind_materializer(old_cache)


class TestCacheDelivery:
    def test_retains_canonical_metadata_on_cache_hit(
        self, client, auth_headers, cached_artifact
    ):
        row, backend, _ = cached_artifact
        first = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)
        second = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)
        assert first.content == second.content
        assert first.headers["etag"] == second.headers["etag"] == f'"{row.sha256}"'
        assert first.headers["last-modified"] == second.headers["last-modified"]
        assert (
            first.headers["content-disposition"]
            == second.headers["content-disposition"]
        )
        assert backend.bytes_read == row.size_bytes

    def test_cold_range_does_not_publish_partial_representation(
        self, client, auth_headers, cached_artifact
    ):
        row, _, cache = cached_artifact
        response = client.get(
            f"/api/v1/files/{row.id}/download",
            headers={**auth_headers, "Range": "bytes=0-2"},
        )
        assert response.status_code == 206
        assert response.content == b"G28"
        assert cache.status()["entries"] == 0

    def test_selected_response_survives_clear_before_open(self, cached_artifact):
        row, _, cache = cached_artifact
        with resolve(row).materialize():
            pass
        plan = plan_artifact(row, DeliveryRequest(filename=row.original_filename))
        cache.clear()
        assert plan.path.read_bytes() == b"G28\nG1 X1\n"
        plan.close()
        assert cache.status()["bytes"] == 0

    def test_redirect_precedes_cache_selection(self, cached_artifact, monkeypatch):
        row, backend, cache = cached_artifact
        with resolve(row).materialize():
            pass
        target = BrowserDownload(
            url="https://provider.example.test/file",
            method="GET",
            required_headers=(),
            expires_at=utcnow() + timedelta(seconds=60),
            key=row.path,
            cors_origin=None,
        )
        monkeypatch.setattr(backend, "browser_download", lambda *args, **kwargs: target)
        plan = plan_artifact(row, DeliveryRequest(filename=row.original_filename))
        assert plan.status == 307
        assert plan.redirect == target.url
        assert cache.status()["leases"] == 0

    def test_converts_cached_obj_using_artifact_format(
        self, client, auth_headers, cached_artifact, db_session
    ):
        import struct
        from pathlib import Path

        from app.db.models import FileType

        row, _, _ = cached_artifact
        payload = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        Path(row.path).write_bytes(payload)
        row.original_filename = "triangle.obj"
        row.file_type = FileType.OBJ
        row.size_bytes = len(payload)
        row.sha256 = hashlib.sha256(payload).hexdigest()
        db_session.add(row)
        db_session.commit()
        with resolve(row).materialize() as path:
            assert path.suffix == ".blob"
        from tests.integration.api.v1._ingest_assertions import drain_work

        accepted = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)
        assert accepted.status_code == 202
        drain_work()

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)
        assert response.status_code == 200
        assert struct.unpack_from("<I", response.content, 80) == (1,)

    @pytest.mark.parametrize(
        "if_range,expected_status,expected_body",
        [("matching", 206, b"G28"), ("stale", 200, b"G28\nG1 X1\n")],
    )
    def test_hot_range_preserves_canonical_if_range(
        self,
        client,
        auth_headers,
        cached_artifact,
        if_range,
        expected_status,
        expected_body,
    ):
        row, backend, cache = cached_artifact
        with resolve(row).materialize():
            pass
        response = client.get(
            f"/api/v1/files/{row.id}/download",
            headers={
                **auth_headers,
                "Range": "bytes=0-2",
                "If-Range": f'"{row.sha256}"' if if_range == "matching" else '"stale"',
            },
        )
        assert response.status_code == expected_status
        assert response.content == expected_body
        assert backend.bytes_read == row.size_bytes
        assert cache.status()["leases"] == 0

    def test_revoked_share_cannot_use_physical_cache(
        self, client, auth_headers, cached_artifact
    ):
        row, backend, cache = cached_artifact
        with resolve(row).materialize():
            pass
        created = client.post(
            f"/api/v1/models/{row.model_id}/shares",
            headers=auth_headers,
            json={"allow_download": True},
        ).json()
        client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)
        response = client.get(
            f"/api/v1/share/{created['token']}/files/{row.id}/download"
        )
        assert response.status_code == 404
        assert cache.status()["leases"] == 0
        assert backend.bytes_read == row.size_bytes

    @pytest.mark.anyio
    async def test_cancelled_asgi_send_releases_selected_cache_lease(
        self, cached_artifact
    ):
        import asyncio

        from app.api.artifact_responses import render_delivery

        row, _, cache = cached_artifact
        with resolve(row).materialize():
            pass
        plan = plan_artifact(row, DeliveryRequest(filename=row.original_filename))
        response = render_delivery(plan)
        cache.clear()

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            assert cache.status()["leases"] == 1
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await response(
                {"type": "http", "method": "GET", "headers": [], "extensions": {}},
                receive,
                send,
            )
        assert cache.status()["leases"] == 0
        assert cache.status()["bytes"] == 0
