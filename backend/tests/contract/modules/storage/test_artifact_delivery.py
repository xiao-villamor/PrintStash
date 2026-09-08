"""Real S3 authorizes browser downloads and serves exact original byte ranges."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from app.modules.storage.artifact_delivery import DeliveryRequest, plan_artifact
from app.modules.storage.storage_backend.runtime import bind_backend
from tests.factories import build_file, build_model
from tests.fakes.s3_delivery import browser_s3

pytestmark = pytest.mark.s3
PAYLOAD = b"solid delivery\nendsolid delivery\n"


class TestBrowserDelivery:
    def test_offloads_the_original(self, tmp_path):
        with browser_s3(tmp_path) as (backend, tls):
            key = backend.blob_key("delivery", 1, "part.stl")
            backend.write_bytes(PAYLOAD, key)

            target = backend.browser_download(
                key, "piece.stl", "application/sla", origin="https://app.test"
            )
            assert target is not None
            response = httpx.get(
                target.url,
                headers={"Origin": "https://app.test"},
                verify=tls.client_context(),
            )

            assert response.content == PAYLOAD

    def test_preserves_the_download_filename(self, tmp_path):
        with browser_s3(tmp_path) as (backend, tls):
            key = backend.blob_key("delivery", 1, "part.stl")
            backend.write_bytes(PAYLOAD, key)

            target = backend.browser_download(
                key, "pièce.stl", "application/sla", origin="https://app.test"
            )
            assert target is not None
            response = httpx.get(target.url, verify=tls.client_context())

            assert (
                "filename*=UTF-8''pi%C3%A8ce.stl"
                in response.headers["content-disposition"]
            )

    def test_falls_back_without_cors(self, tmp_path):
        with browser_s3(tmp_path, origin=None) as (backend, _tls):
            key = backend.blob_key("delivery", 1, "part.stl")
            backend.write_bytes(PAYLOAD, key)

            target = backend.browser_download(
                key, "piece.stl", "application/sla", origin="https://app.test"
            )

            assert target is None

    def test_serves_selected_range(self, tmp_path, db_session):
        with browser_s3(tmp_path) as (backend, _tls):
            key = backend.blob_key("delivery", 1, "part.stl")
            backend.write_bytes(PAYLOAD, key)
            artifact = build_file(
                db_session, build_model(db_session), path=key, size_bytes=len(PAYLOAD)
            )
            bind_backend(backend)

            result = plan_artifact(
                artifact,
                DeliveryRequest(filename="part.stl", range_header="bytes=2-7"),
            )

            assert (result.status, b"".join(result.chunks)) == (206, PAYLOAD[2:8])

    def test_compares_if_range_to_the_original_digest(self, tmp_path, db_session):
        with browser_s3(tmp_path) as (backend, _tls):
            key = backend.blob_key("delivery", 1, "part.stl")
            backend.write_bytes(PAYLOAD, key)
            digest = hashlib.sha256(PAYLOAD).hexdigest()
            artifact = build_file(
                db_session,
                build_model(db_session),
                path=key,
                size_bytes=len(PAYLOAD),
                sha256=digest,
            )
            bind_backend(backend)

            result = plan_artifact(
                artifact,
                DeliveryRequest(
                    filename="part.stl",
                    range_header="bytes=2-7",
                    if_range=f'"{digest}"',
                ),
            )

            assert (result.status, b"".join(result.chunks)) == (206, PAYLOAD[2:8])

    def test_serves_full_original_for_stale_if_range(self, tmp_path, db_session):
        with browser_s3(tmp_path) as (backend, _tls):
            key = backend.blob_key("delivery", 1, "part.stl")
            backend.write_bytes(PAYLOAD, key)
            artifact = build_file(
                db_session, build_model(db_session), path=key, size_bytes=len(PAYLOAD)
            )
            bind_backend(backend)

            result = plan_artifact(
                artifact,
                DeliveryRequest(
                    filename="part.stl",
                    range_header="bytes=2-7",
                    if_range='"stale"',
                ),
            )

            assert (result.status, b"".join(result.chunks)) == (200, PAYLOAD)


def test_preserves_inline_provider_disposition(tmp_path):
    with browser_s3(tmp_path) as (backend, tls):
        key = backend.thumbnail_key(313)
        backend.write_bytes(PAYLOAD, key)

        target = backend.browser_download(
            key, "thumb.webp", "image/webp", origin="https://app.test", inline=True
        )
        assert target is not None
        response = httpx.get(target.url, verify=tls.client_context())

        assert response.headers["content-disposition"].startswith("inline;")
        assert response.headers["content-type"] == "image/webp"
