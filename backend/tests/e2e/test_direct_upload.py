"""A browser's direct upload reaches the library without sending its bytes again.

The browser puts the parts straight into the bucket; PrintStash verifies a
downloaded copy, then publishes the Artifact by copying the finished
upload inside the store. Driven through the real API against a real store
(SeaweedFS), so the copy is only proven when the store itself performed it.

One wire field is stood in for. SeaweedFS verifies each part's SHA-256 when it
is uploaded and acknowledges it in the response, but leaves it out of
ListParts, which PrintStash reads back before it enables or completes a native
upload. The stand-in reports in ListParts exactly the checksum the store
acknowledged for that part; every other request is the store's own answer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
from sqlmodel import select

from app.db.models import File
from app.modules.storage.storage_backend.runtime import bind_backend
from app.modules.storage.storage_backend.s3 import S3StorageBackend
from tests.e2e._jobs import settle
from tests.factories import content
from tests.fakes.s3_delivery import browser_s3

pytestmark = pytest.mark.s3


@contextmanager
def _listed_part_checksums(backend: S3StorageBackend) -> Iterator[dict[int, str]]:
    """ListParts reports the checksum the store acknowledged per part number."""
    acknowledged: dict[int, str] = {}
    pending: list[int] = []

    def remember_part(params: dict[str, object], **_kwargs: object) -> None:
        pending.append(int(str(params["PartNumber"])))

    def acknowledge(parsed: dict[str, object], **_kwargs: object) -> None:
        number = pending.pop()
        if "ChecksumSHA256" in parsed:
            acknowledged[number] = str(parsed["ChecksumSHA256"])

    def report(parsed: dict[str, list[dict[str, object]]], **_kwargs: object) -> None:
        for part in parsed.get("Parts", []):
            number = int(str(part["PartNumber"]))
            if number in acknowledged:
                part.setdefault("ChecksumSHA256", acknowledged[number])

    hooks = (
        ("before-parameter-build.s3.UploadPart", remember_part),
        ("after-call.s3.UploadPart", acknowledge),
        ("after-call.s3.ListParts", report),
    )
    events = backend._client.meta.events
    for event, handler in hooks:
        events.register(event, handler)
    try:
        yield acknowledged
    finally:
        for event, handler in hooks:
            events.unregister(event, handler)


@contextmanager
def _writes_by_operation(backend: S3StorageBackend) -> Iterator[dict[str, list[str]]]:
    """The keys each object-writing request from the app targeted."""
    writes: dict[str, list[str]] = {
        "PutObject": [],
        "UploadPart": [],
        "UploadPartCopy": [],
    }

    def recorder(operation: str):
        def record(params: dict[str, object], **_kwargs: object) -> None:
            writes[operation].append(str(params["Key"]))

        return record

    handlers = {operation: recorder(operation) for operation in writes}
    events = backend._client.meta.events
    for operation, handler in handlers.items():
        events.register(f"before-parameter-build.s3.{operation}", handler)
    try:
        yield writes
    finally:
        for operation, handler in handlers.items():
            events.unregister(f"before-parameter-build.s3.{operation}", handler)


class TestDirectUpload:
    @pytest.mark.asyncio
    async def test_publishes_the_upload_by_copying_it_inside_the_store(
        self, api, superuser_headers, e2e_db, tmp_path
    ):
        with (
            browser_s3(tmp_path, origin=None) as (backend, tls),
            _listed_part_checksums(backend) as acknowledged,
        ):
            backend.ensure_setup()
            bind_backend(backend)
            capability = backend.native_multipart_capability
            assert capability is not None
            # Bigger than one part, so the upload goes straight to the bucket.
            mesh = content.binary_stl()
            payload = mesh + b"\0" * (capability.part_size + 1024 - len(mesh))

            with _writes_by_operation(backend) as writes:
                created = await api.post(
                    "/api/v1/artifact-uploads",
                    json={
                        "purpose": "model",
                        "target_role": "new_model",
                        "filename": "direct.stl",
                        "media_type": "model/stl",
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    },
                    headers=superuser_headers,
                )
                assert created.json()["mode"] == "native_parts", created.text
                upload_id = created.json()["id"]
                async with httpx.AsyncClient(verify=tls.client_context()) as browser:
                    for offset in range(0, len(payload), capability.part_size):
                        part = payload[offset : offset + capability.part_size]
                        number = offset // capability.part_size + 1
                        checksum = hashlib.sha256(part).hexdigest()
                        signed = await api.post(
                            f"/api/v1/artifact-uploads/{upload_id}/parts/{number}/sign",
                            json={"checksum_sha256": checksum},
                            headers=superuser_headers,
                        )
                        instruction = signed.json()
                        sent = await browser.request(
                            instruction["method"],
                            instruction["url"],
                            headers=instruction["headers"],
                            content=part,
                        )
                        assert sent.status_code == 200, sent.text
                        acknowledged[number] = sent.headers["x-amz-checksum-sha256"]
                        await api.post(
                            f"/api/v1/artifact-uploads/{upload_id}/parts/{number}",
                            json={
                                "size_bytes": len(part),
                                "checksum_sha256": checksum,
                                "etag": sent.headers["etag"],
                            },
                            headers=superuser_headers,
                        )
                finalized = await api.post(
                    f"/api/v1/artifact-uploads/{upload_id}/finalize",
                    headers=superuser_headers,
                )
                assert finalized.status_code == 200, finalized.text
                settle()

            status = await api.get(
                f"/api/v1/artifact-uploads/{upload_id}", headers=superuser_headers
            )
            assert status.json()["state"] == "completed", status.text
            artifact = e2e_db.exec(
                select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
            ).one()
            assert artifact.path in writes["UploadPartCopy"]
            assert artifact.path not in writes["PutObject"] + writes["UploadPart"]
            assert backend.read_bytes(artifact.path) == payload
