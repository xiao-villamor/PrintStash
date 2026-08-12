"""S3StorageBackend exercised against a real object store (SeaweedFS).

Skipped unless PRINTSTASH_TEST_S3_ENDPOINT is set, so ``uv run pytest tests``
stays dependency-free for contributors. Start a throwaway SeaweedFS with:

    docker compose -f docker-compose.test.yml up -d
    PRINTSTASH_TEST_S3_ENDPOINT=http://localhost:9100 \
        uv run pytest tests/test_storage_s3.py -v
    docker compose -f docker-compose.test.yml down -v

``tests/test_storage_seam.py`` covers ``exists()``'s auth-error handling with
a hand-built client stub; this file covers the read/write round trip that
only a real (or real-compatible) S3 endpoint can validate.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from app.core.config import _overlay
from app.services.storage_backend import S3StorageBackend

_ENDPOINT = os.environ.get("PRINTSTASH_TEST_S3_ENDPOINT")

pytestmark = pytest.mark.skipif(
    not _ENDPOINT, reason="set PRINTSTASH_TEST_S3_ENDPOINT to run S3 integration tests"
)


@pytest.fixture
def s3_backend() -> Iterator[S3StorageBackend]:
    bucket = f"printstash-test-{uuid.uuid4().hex[:12]}"
    _overlay.update(
        {
            "s3_bucket": bucket,
            "s3_endpoint_url": _ENDPOINT,
            "s3_region": "us-east-1",
            "s3_access_key": os.environ.get("PRINTSTASH_TEST_S3_ACCESS_KEY", "printstash"),
            "s3_secret_key": os.environ.get(
                "PRINTSTASH_TEST_S3_SECRET_KEY", "printstash-secret"
            ),
        }
    )
    backend = S3StorageBackend()  # creates the bucket (_ensure_bucket)
    try:
        yield backend
    finally:
        for key in backend.list_keys():
            backend.delete(key)
        for field in (
            "s3_bucket",
            "s3_endpoint_url",
            "s3_region",
            "s3_access_key",
            "s3_secret_key",
        ):
            _overlay.pop(field, None)


def test_round_trips_bytes(s3_backend: S3StorageBackend):
    key = "models/round-trip.txt"
    assert not s3_backend.exists(key)

    s3_backend.write_bytes(b"hello s3", key)

    assert s3_backend.exists(key)
    assert s3_backend.stat_size(key) == len(b"hello s3")
    assert s3_backend.read_bytes(key) == b"hello s3"
    info = s3_backend.object_info(key)
    assert info is not None
    assert info.size == len(b"hello s3")
    assert info.etag


def test_upload_file_then_download_to_path(s3_backend: S3StorageBackend, tmp_path: Path):
    src = tmp_path / "source.bin"
    src.write_bytes(b"payload bytes")
    key = "models/uploaded.bin"

    s3_backend.upload_file(src, key)
    assert s3_backend.exists(key)

    dest = tmp_path / "downloaded.bin"
    s3_backend.download_to_path(key, dest)
    assert dest.read_bytes() == b"payload bytes"


def test_move_in_uploads_and_removes_staged_file(
    s3_backend: S3StorageBackend, tmp_path: Path
):
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"staged content")
    key = "models/moved.bin"

    s3_backend.move_in(staged, key)

    assert s3_backend.exists(key)
    assert s3_backend.read_bytes(key) == b"staged content"
    assert not staged.exists()


def test_delete_removes_object(s3_backend: S3StorageBackend):
    key = "models/to-delete.txt"
    s3_backend.write_bytes(b"gone soon", key)
    assert s3_backend.exists(key)

    s3_backend.delete(key)

    assert not s3_backend.exists(key)


def test_exists_false_on_missing_key(s3_backend: S3StorageBackend):
    assert not s3_backend.exists("models/never-written.txt")


def test_write_stream_above_multipart_threshold_round_trips(
    s3_backend: S3StorageBackend, tmp_path: Path
):
    # Force the multipart path (default threshold is 50MB) with a small payload.
    _overlay["s3_multipart_threshold_mb"] = 1
    try:
        payload = os.urandom(2 * 1024 * 1024)
        src = tmp_path / "big.bin"
        src.write_bytes(payload)
        key = "models/multipart.bin"

        with src.open("rb") as f:
            size = s3_backend.write_stream(f, key)

        assert size == len(payload)
        assert s3_backend.stat_size(key) == len(payload)
        assert s3_backend.read_bytes(key) == payload
    finally:
        _overlay.pop("s3_multipart_threshold_mb", None)


def test_upload_file_above_multipart_threshold_round_trips(
    s3_backend: S3StorageBackend, tmp_path: Path
):
    _overlay["s3_multipart_threshold_mb"] = 1
    try:
        payload = os.urandom(2 * 1024 * 1024)
        src = tmp_path / "big-upload.bin"
        src.write_bytes(payload)
        key = "models/multipart-upload.bin"

        s3_backend.upload_file(src, key)

        assert s3_backend.read_bytes(key) == payload
    finally:
        _overlay.pop("s3_multipart_threshold_mb", None)


def test_move_copies_and_deletes_source(s3_backend: S3StorageBackend):
    s3_backend.write_bytes(b"move me", "models/move-src.txt")

    s3_backend.move("models/move-src.txt", "models/move-dest.txt")

    assert not s3_backend.exists("models/move-src.txt")
    assert s3_backend.read_bytes("models/move-dest.txt") == b"move me"


def test_stream_chunks_reassembles_full_content(s3_backend: S3StorageBackend):
    payload = b"x" * 5000
    s3_backend.write_bytes(payload, "models/chunked.bin")

    chunks = list(s3_backend.stream_chunks("models/chunked.bin", chunk_size=1024))

    assert len(chunks) == 5  # 4 full 1024-byte chunks + one 904-byte remainder
    assert b"".join(chunks) == payload


def test_list_keys_and_walk_keys_and_usage(s3_backend: S3StorageBackend):
    s3_backend.write_bytes(b"a", "models/list-1.txt")
    s3_backend.write_bytes(b"bb", "models/list-2.txt")

    listed = s3_backend.list_keys(prefix="models/")
    walked = list(s3_backend.walk_keys(prefix="models/"))
    assert set(listed) == set(walked) == {"models/list-1.txt", "models/list-2.txt"}

    usage = s3_backend.usage(prefix="models/")
    assert usage["backend"] == "s3"
    assert usage["object_count"] == 2
    assert usage["total_size_bytes"] == 3


def test_presigned_download_url_is_fetchable(s3_backend: S3StorageBackend):
    import httpx

    s3_backend.write_bytes(b"presigned content", "models/presigned.txt")

    url = s3_backend.presigned_download_url("models/presigned.txt", "download.txt")

    assert url is not None
    resp = httpx.get(url)
    assert resp.status_code == 200
    assert resp.content == b"presigned content"
    assert 'filename="download.txt"' in resp.headers.get("content-disposition", "")


def test_health_probe_reports_ok_for_reachable_bucket(s3_backend: S3StorageBackend):
    probe = s3_backend.health_probe()
    assert probe == {
        "backend": "s3",
        "ok": True,
        "bucket": s3_backend._bucket,
        "endpoint": _ENDPOINT,
    }


def test_health_probe_reports_error_for_missing_bucket(s3_backend: S3StorageBackend):
    real_bucket = s3_backend._bucket
    s3_backend._bucket = f"does-not-exist-{uuid.uuid4().hex[:12]}"
    try:
        probe = s3_backend.health_probe()
        assert probe["ok"] is False
        assert probe["backend"] == "s3"
        assert "error" in probe
    finally:
        # The fixture's teardown lists/deletes against s3_backend._bucket —
        # leaving it pointed at a bucket that was never created would break
        # that cleanup, not this test.
        s3_backend._bucket = real_bucket


def test_ensure_setup_applies_lifecycle_policy_when_configured(
    s3_backend: S3StorageBackend,
):
    _overlay["s3_lifecycle_expiration_days"] = 30
    try:
        s3_backend.ensure_setup()  # must not raise against a real S3-compatible bucket

        lifecycle = s3_backend._client.get_bucket_lifecycle_configuration(
            Bucket=s3_backend._bucket
        )
        rules = lifecycle["Rules"]
        assert any(rule.get("Expiration", {}).get("Days") == 30 for rule in rules)
    finally:
        _overlay.pop("s3_lifecycle_expiration_days", None)


def test_exists_raises_on_non_404_client_error(s3_backend: S3StorageBackend):
    """A credential/permission failure must surface, not be swallowed as 'missing'.

    Swallowing it would tell the orphan-blob GC every blob is gone and delete
    the bucket — see the docstring on ``S3StorageBackend.exists``.
    """
    import botocore.exceptions

    original_head_object = s3_backend._client.head_object

    def _forbidden(**kwargs: object) -> object:
        raise botocore.exceptions.ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
        )

    s3_backend._client.head_object = _forbidden
    try:
        with pytest.raises(botocore.exceptions.ClientError):
            s3_backend.exists("models/anything.txt")
    finally:
        s3_backend._client.head_object = original_head_object


def test_delete_swallows_errors_for_already_missing_key(s3_backend: S3StorageBackend):
    # delete() is called during cleanup/GC; a missing key must not raise.
    s3_backend.delete("models/never-existed.txt")
