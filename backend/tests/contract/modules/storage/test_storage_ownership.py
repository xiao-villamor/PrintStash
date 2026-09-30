"""A finished direct upload is published without sending its bytes again.

A browser's multipart upload is already in the store when PrintStash verifies
and parses its downloaded copy. Publishing it through ``publish_file`` must then
copy it into place inside the store: re-uploading would move the whole file
over the network a third time and hold two copies in the bucket. Against a real
store (SeaweedFS), because only the store decides whether a server-side copy is
create-only and pinned to the verified object.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlmodel import Session

from app.modules.storage.storage_backend.contracts import StagedRemoteObject
from app.modules.storage.storage_backend.s3 import S3StorageBackend
from app.modules.storage.storage_ownership import publish_file
from tests.fakes.s3_delivery import browser_s3

pytestmark = pytest.mark.s3

PAYLOAD = b"solid direct-upload\nendsolid direct-upload\n"


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[S3StorageBackend]:
    with browser_s3(tmp_path, origin=None) as (s3, _tls):
        s3.ensure_setup()
        yield s3


@contextmanager
def _bytes_sent(backend: S3StorageBackend) -> Iterator[list[str]]:
    """The key of every request that carries object bytes to the store."""
    keys: list[str] = []

    def record(params: dict[str, object], **_kwargs: object) -> None:
        keys.append(str(params["Key"]))

    events = backend._client.meta.events
    for operation in ("PutObject", "UploadPart"):
        events.register(f"before-parameter-build.s3.{operation}", record)
    try:
        yield keys
    finally:
        for operation in ("PutObject", "UploadPart"):
            events.unregister(f"before-parameter-build.s3.{operation}", record)


def _finished_upload(backend: S3StorageBackend, payload: bytes) -> StagedRemoteObject:
    receipt = backend.create_bytes(
        payload, f"{backend._prefix()}staging/artifact-uploads/{uuid.uuid4().hex}"
    )
    return StagedRemoteObject(
        key=receipt.key,
        size=receipt.size,
        namespace=receipt.namespace,
        provider_ref=backend.storage_target.target_ref,
        etag=receipt.etag,
        version_id=receipt.version_id,
    )


def _verified_copy(tmp_path: Path, payload: bytes) -> Path:
    staged = tmp_path / "downloaded.stl"
    staged.write_bytes(payload)
    return staged


class TestPublishFile:
    def test_publishes_a_finished_upload_at_its_key(
        self, db_session: Session, backend: S3StorageBackend, tmp_path: Path
    ) -> None:
        upload = _finished_upload(backend, PAYLOAD)
        key = backend.blob_key("direct", 1, "part.stl")

        publish_file(
            db_session,
            backend,
            key,
            _verified_copy(tmp_path, PAYLOAD),
            object_kind="artifact",
            move=True,
            remote_source=upload,
        )

        assert backend.read_bytes(key) == PAYLOAD

    def test_sends_none_of_the_bytes_again(
        self, db_session: Session, backend: S3StorageBackend, tmp_path: Path
    ) -> None:
        upload = _finished_upload(backend, PAYLOAD)
        key = backend.blob_key("direct", 1, "part.stl")
        staged = _verified_copy(tmp_path, PAYLOAD)

        with _bytes_sent(backend) as sent:
            publish_file(
                db_session,
                backend,
                key,
                staged,
                object_kind="artifact",
                move=True,
                remote_source=upload,
            )

        assert key not in sent

    def test_releases_the_verified_local_copy(
        self, db_session: Session, backend: S3StorageBackend, tmp_path: Path
    ) -> None:
        upload = _finished_upload(backend, PAYLOAD)
        staged = _verified_copy(tmp_path, PAYLOAD)

        publish_file(
            db_session,
            backend,
            backend.blob_key("direct", 1, "part.stl"),
            staged,
            object_kind="artifact",
            move=True,
            remote_source=upload,
        )

        assert not staged.exists()

    def test_uploads_the_local_copy_when_the_upload_does_not_match_it(
        self, db_session: Session, backend: S3StorageBackend, tmp_path: Path
    ) -> None:
        # A size mismatch means the pairing is wrong; only the verified local
        # bytes may be published.
        upload = _finished_upload(backend, PAYLOAD + b"extra")
        key = backend.blob_key("direct", 1, "part.stl")

        publish_file(
            db_session,
            backend,
            key,
            _verified_copy(tmp_path, PAYLOAD),
            object_kind="artifact",
            move=True,
            remote_source=upload,
        )

        assert backend.read_bytes(key) == PAYLOAD
