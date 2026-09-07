"""An authenticated canonical download hands the original body to the provider."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from app.modules.storage.storage_backend.runtime import bind_backend
from tests.factories import build_file, build_model
from tests.fakes.s3_delivery import browser_s3

pytestmark = pytest.mark.s3
PAYLOAD = b"solid offload\nendsolid offload\n"


class TestArtifactDelivery:
    @pytest.mark.asyncio
    async def test_offloads_authorized_download(
        self, api, superuser_headers, e2e_db, tmp_path
    ):
        with browser_s3(tmp_path, origin="http://app") as (backend, tls):
            key = backend.blob_key("delivery", 1, "part.stl")
            backend.write_bytes(PAYLOAD, key)
            artifact = build_file(
                e2e_db,
                build_model(e2e_db),
                path=key,
                filename="part.stl",
                sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                size_bytes=len(PAYLOAD),
            )
            bind_backend(backend)

            response = await api.get(
                f"/api/v1/files/{artifact.id}/download",
                headers={
                    **superuser_headers,
                    "Sec-Fetch-Mode": "cors",
                    "Origin": "http://app",
                },
            )
            assert (response.status_code, response.content) == (307, b"")
            async with httpx.AsyncClient(verify=tls.client_context()) as browser:
                download = await browser.get(
                    response.headers["location"], headers={"Origin": "http://app"}
                )

            assert download.content == PAYLOAD
