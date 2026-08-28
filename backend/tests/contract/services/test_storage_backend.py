"""S3StorageBackend exercised against a real object store (SeaweedFS).

The unit tests cover our reaction to an S3 client that misbehaves on cue. What
they cannot cover is the protocol: conditional writes, version ids, ETag
semantics and the exact shape of a `ClientError` are properties of the store, and
a stub that returns what we expect proves only that we expect it.

The endpoint is a SeaweedFS container started for the run — the only path, so
this file runs everywhere or the session stops saying why. See
``tests/containers.py``.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterator

import boto3
import pytest

from app.core.config import _overlay
from app.services.storage_backend import (
    S3StorageBackend,
    StorageCollisionError,
    StorageConfigurationError,
)
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint

# The `s3` marker is what tells conftest.py this file needs a real endpoint, so
# the prerequisite is stated once for every resource-gated file rather than
# re-implemented per file.
pytestmark = pytest.mark.s3


@pytest.fixture
def s3_backend() -> Iterator[S3StorageBackend]:
    bucket = f"printstash-test-{uuid.uuid4().hex[:12]}"
    test_client = boto3.client(
        "s3",
        endpoint_url=s3_endpoint(),
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    # Bucket administration belongs to test infrastructure. Production setup
    # only verifies the operator-provisioned namespace.
    test_client.create_bucket(Bucket=bucket)
    _overlay.update(
        {
            "s3_bucket": bucket,
            "s3_endpoint_url": s3_endpoint(),
            "s3_region": "us-east-1",
            "s3_access_key": S3_ACCESS_KEY,
            "s3_secret_key": S3_SECRET_KEY,
        }
    )
    backend = S3StorageBackend()
    try:
        yield backend
    finally:
        for key in backend.list_keys():
            backend._client.delete_object(Bucket=bucket, Key=key)
        for field in (
            "s3_bucket",
            "s3_endpoint_url",
            "s3_region",
            "s3_access_key",
            "s3_secret_key",
        ):
            _overlay.pop(field, None)


@pytest.fixture
def versioned_s3_backend(s3_backend: S3StorageBackend) -> S3StorageBackend:
    """The same backend against a bucket with object versioning enabled.

    This is the configuration PrintStash's delete path actually supports, and the
    distinction is load-bearing rather than incidental. `rollback_create` refuses
    to delete an object it cannot name by immutable version id, so on a bucket
    without versioning *every* rollback fails closed — which means
    `verify_destructive_access` raises and no purge can run at all. Both halves are
    real deployments, so both are tested: this fixture for the supported one, and
    `TestUnversionedBucket` for what an operator hits without it.
    """
    s3_backend._client.put_bucket_versioning(
        Bucket=s3_backend._bucket,
        VersioningConfiguration={"Status": "Enabled"},
    )
    return s3_backend


class TestCaptureUploadSlotKey:
    def test_capture_upload_slot_key_uses_s3_prefix(self, s3_backend: S3StorageBackend):
        assert s3_backend.capture_upload_slot_key("slot-1").endswith(
            "capture-slots/slot-1"
        )


class TestExists:
    def test_exists_false_on_missing_key(self, s3_backend: S3StorageBackend):
        assert not s3_backend.exists("models/never-written.txt")

    def test_exists_raises_on_non_404_client_error(self, s3_backend: S3StorageBackend):
        """A credential/permission failure must surface, not be swallowed as 'missing'."""
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


class TestWriteStream:
    def test_write_stream_above_multipart_threshold_round_trips(
        self, s3_backend: S3StorageBackend, tmp_path: Path
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

    def test_round_trips_bytes(self, s3_backend: S3StorageBackend):
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


class TestMove:
    def test_an_unchecked_move_is_refused_with_the_source_intact(
        self, s3_backend: S3StorageBackend
    ):
        s3_backend.write_bytes(b"move me", "models/move-src.txt")

        with pytest.raises(RuntimeError, match="unchecked_storage_move_disabled"):
            s3_backend.move("models/move-src.txt", "models/move-dest.txt")

        assert s3_backend.read_bytes("models/move-src.txt") == b"move me"
        assert not s3_backend.exists("models/move-dest.txt")


class TestStreamChunks:
    def test_stream_chunks_reassembles_full_content(self, s3_backend: S3StorageBackend):
        payload = b"x" * 5000
        s3_backend.write_bytes(payload, "models/chunked.bin")

        chunks = list(s3_backend.stream_chunks("models/chunked.bin", chunk_size=1024))

        assert len(chunks) == 5  # 4 full 1024-byte chunks + one 904-byte remainder
        assert b"".join(chunks) == payload


class TestDownloadToPath:
    def test_upload_file_then_download_to_path(
        self, s3_backend: S3StorageBackend, tmp_path: Path
    ):
        src = tmp_path / "source.bin"
        src.write_bytes(b"payload bytes")
        key = "models/uploaded.bin"

        s3_backend.upload_file(src, key)
        assert s3_backend.exists(key)

        dest = tmp_path / "downloaded.bin"
        s3_backend.download_to_path(key, dest)
        assert dest.read_bytes() == b"payload bytes"


class TestUploadFile:
    def test_upload_file_above_multipart_threshold_round_trips(
        self, s3_backend: S3StorageBackend, tmp_path: Path
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


class TestEnsureSetup:
    def test_fails_actionably_for_a_missing_bucket(
        self, s3_backend: S3StorageBackend
    ) -> None:
        real_bucket = s3_backend._bucket
        s3_backend._bucket = f"does-not-exist-{uuid.uuid4().hex[:12]}"
        try:
            with pytest.raises(StorageConfigurationError, match="create it with"):
                s3_backend.ensure_setup()
        finally:
            s3_backend._bucket = real_bucket

    def test_never_creates_a_bucket(
        self, s3_backend: S3StorageBackend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _unexpected_create(**_kwargs: object) -> None:
            pytest.fail("production storage setup attempted to create the S3 bucket")

        monkeypatch.setattr(s3_backend._client, "create_bucket", _unexpected_create)

        s3_backend.ensure_setup()

    def test_ensure_setup_never_mutates_bucket_lifecycle_automatically(
        self,
        s3_backend: S3StorageBackend,
    ):
        _overlay["s3_lifecycle_expiration_days"] = 30
        try:
            s3_backend.ensure_setup()  # must not raise against a real S3-compatible bucket

            import botocore.exceptions

            with pytest.raises(botocore.exceptions.ClientError):
                s3_backend._client.get_bucket_lifecycle_configuration(
                    Bucket=s3_backend._bucket
                )
        finally:
            _overlay.pop("s3_lifecycle_expiration_days", None)


class TestDelete:
    def test_delete_removes_object(self, s3_backend: S3StorageBackend):
        key = "models/to-delete.txt"
        receipt = s3_backend.create_bytes(b"gone soon", key)
        assert s3_backend.exists(key)

        if receipt.version_id:
            assert s3_backend.rollback_create(receipt) is True
            assert not s3_backend.exists(key)
        else:
            # Compatible stores without immutable VersionId cannot authorize an
            # exact delete. Preserve the bytes and leave the outbox intent blocked.
            assert s3_backend.rollback_create(receipt) is False
            assert s3_backend.exists(key)

    def test_unchecked_delete_is_disabled(self, s3_backend: S3StorageBackend):
        with pytest.raises(RuntimeError, match="unchecked_storage_delete_disabled"):
            s3_backend.delete("models/never-existed.txt")


def _seed_two_objects(backend: S3StorageBackend) -> None:
    """Two objects of known, different sizes under one prefix.

    Different sizes so a `total_size_bytes` that summed the wrong thing — a count,
    or one object twice — could not accidentally match.
    """
    backend.write_bytes(b"a", "models/list-1.txt")
    backend.write_bytes(b"bb", "models/list-2.txt")


class TestListKeys:
    def test_list_keys_agrees_with_walk_keys(self, s3_backend: S3StorageBackend):
        _seed_two_objects(s3_backend)

        listed = s3_backend.list_keys(prefix="models/")
        walked = list(s3_backend.walk_keys(prefix="models/"))

        assert set(listed) == set(walked) == {"models/list-1.txt", "models/list-2.txt"}

    def test_usage_reports_the_object_count_with_its_total_size(
        self, s3_backend: S3StorageBackend
    ):
        _seed_two_objects(s3_backend)

        usage = s3_backend.usage(prefix="models/")

        assert usage["backend"] == "s3"
        assert usage["object_count"] == 2
        assert usage["total_size_bytes"] == 3


class TestPresignedDownloadUrl:
    def test_presigned_download_url_is_fetchable(self, s3_backend: S3StorageBackend):
        import httpx

        s3_backend.write_bytes(b"presigned content", "models/presigned.txt")

        url = s3_backend.presigned_download_url("models/presigned.txt", "download.txt")

        assert url is not None
        resp = httpx.get(url)
        assert resp.status_code == 200
        assert resp.content == b"presigned content"
        assert 'filename="download.txt"' in resp.headers.get("content-disposition", "")


class TestHealthProbe:
    def test_health_probe_reports_ok_for_reachable_bucket(
        self, s3_backend: S3StorageBackend
    ):
        probe = s3_backend.health_probe()
        assert probe["backend"] == "s3"
        assert probe["ok"] is True
        assert probe["bucket"] == s3_backend._bucket
        assert probe["endpoint"] == s3_endpoint()
        assert probe["capabilities"] == s3_backend.capabilities.as_dict()
        assert probe["diagnostics"] == s3_backend.probe_diagnostics

    def test_health_probe_reports_error_for_missing_bucket(
        self, s3_backend: S3StorageBackend
    ):
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


class TestMoveIn:
    def test_move_in_consumes_the_staged_file(
        self, s3_backend: S3StorageBackend, tmp_path: Path
    ):
        staged = tmp_path / "staged.bin"
        staged.write_bytes(b"staged content")
        key = "models/moved.bin"

        s3_backend.move_in(staged, key)

        assert s3_backend.exists(key)
        assert s3_backend.read_bytes(key) == b"staged content"
        assert not staged.exists()


class TestKeyDerivation:
    """Where every kind of object lands under the configured prefix.

    These look trivial and are the most consequential methods on the class: the
    key *is* the object's identity, and every audit, GC sweep and ownership check
    finds an object by deriving the same key again. A prefix that drops off one of
    them writes objects the audit cannot see, which reads as missing data rather
    than as a naming bug — and it stays invisible until somebody enables S3.
    """

    def test_reports_no_local_path_for_any_key(
        self, s3_backend: S3StorageBackend
    ) -> None:
        # Callers branch on this to choose between a filesystem operation and an
        # API call. A path returned here sends a local `open()` at an object store.
        assert s3_backend.direct_path("anything") is None

    def test_derives_an_artifact_key_that_carries_its_revision(
        self, s3_backend: S3StorageBackend
    ) -> None:
        key = s3_backend.blob_key("bracket", 2, "part.stl")

        # The version in the path is what lets two revisions of one model coexist.
        assert key == f"{s3_backend._prefix()}files/bracket/v2/part.stl"

    @pytest.mark.parametrize(
        ("derive", "expected"),
        [
            (lambda b: b.thumbnail_key(7), "thumbs/7.webp"),
            (lambda b: b.legacy_thumbnail_key(7), "thumbs/7.png"),
            (lambda b: b.source_cover_key(9), "source-covers/9.webp"),
            (lambda b: b.capture_upload_slot_key("abc"), "capture-slots/abc"),
            (lambda b: b.stl_cache_key("a" * 64), f"stl-cache/{'a' * 64}.stl"),
            (
                lambda b: b.collection_image_key(3, "hero.webp"),
                "collection-images/3/hero.webp",
            ),
            (lambda b: b.document_file_key(4, "manual.pdf"), "documents/4/manual.pdf"),
            (
                lambda b: b.document_image_key(4, "fig.webp"),
                "document-images/4/fig.webp",
            ),
        ],
    )
    def test_puts_every_object_kind_in_its_own_namespace(
        self,
        s3_backend: S3StorageBackend,
        derive: Callable[[S3StorageBackend], str],
        expected: str,
    ) -> None:
        # Separate namespaces are what make a prefix-scoped sweep possible: GC
        # walks `thumbs/` without touching `files/`, so two kinds sharing a prefix
        # means one sweep deletes the other's objects.
        assert derive(s3_backend) == f"{s3_backend._prefix()}{expected}"

    def test_keeps_the_legacy_thumbnail_key_distinct_from_the_current_one(
        self, s3_backend: S3StorageBackend
    ) -> None:
        # Same id, two extensions. They must not collide, or the repair job that
        # regenerates a `.webp` overwrites the `.png` it is migrating from.
        assert s3_backend.thumbnail_key(7) != s3_backend.legacy_thumbnail_key(7)


class TestConstruction:
    def test_refuses_to_build_without_a_bucket(self) -> None:
        _overlay.update(
            {
                "s3_endpoint_url": s3_endpoint(),
                "s3_region": "us-east-1",
                "s3_access_key": S3_ACCESS_KEY,
                "s3_secret_key": S3_SECRET_KEY,
            }
        )
        _overlay.pop("s3_bucket", None)
        try:
            # Failing at construction is the point: a bucket-less client builds
            # fine and then addresses every object at `s3://None/...`, which
            # surfaces as a permission error far from the missing setting.
            with pytest.raises(RuntimeError, match="VAULT_S3_BUCKET is required"):
                S3StorageBackend()
        finally:
            for field in (
                "s3_endpoint_url",
                "s3_region",
                "s3_access_key",
                "s3_secret_key",
            ):
                _overlay.pop(field, None)


class TestRollbackCreate:
    def test_removes_the_exact_version_the_receipt_names(
        self, versioned_s3_backend: S3StorageBackend
    ) -> None:
        receipt = versioned_s3_backend.create_bytes(b"mine", "rollback-happy.bin")

        assert versioned_s3_backend.rollback_create(receipt) is True
        assert versioned_s3_backend.object_info(receipt.key) is None

    def test_treats_an_object_that_is_already_gone_as_rolled_back(
        self, versioned_s3_backend: S3StorageBackend
    ) -> None:
        receipt = versioned_s3_backend.create_bytes(b"gone", "rollback-absent.bin")
        versioned_s3_backend.rollback_create(receipt)

        # Idempotent on purpose: a rollback that ran, crashed before recording,
        # and ran again must not report failure and leave the caller retrying
        # forever against an object nobody has.
        assert versioned_s3_backend.rollback_create(receipt) is True

    def test_removes_only_its_own_version_when_another_writer_added_one(
        self, versioned_s3_backend: S3StorageBackend
    ) -> None:
        receipt = versioned_s3_backend.create_bytes(b"mine", "rollback-replaced.bin")
        versioned_s3_backend._client.put_object(
            Bucket=versioned_s3_backend._bucket,
            Key=receipt.key,
            Body=b"theirs",
            Metadata={"printstash-create-token": "somebody-else"},
        )

        assert versioned_s3_backend.rollback_create(receipt) is True

        # This is what versioning buys. The rollback names its own version, so the
        # other writer's version survives untouched — where a delete by key would
        # have destroyed a write this caller never made. The current version is
        # still theirs.
        assert versioned_s3_backend.read_bytes(receipt.key) == b"theirs"


class TestUnversionedBucket:
    """What an operator gets on a bucket without object versioning.

    Not a hypothetical: it is the default for a new bucket on most stores, and on
    SeaweedFS's `mini` mode. `create_bytes` there returns a receipt with no version
    id, and `rollback_create` will not delete an object it cannot name immutably —
    so it fails closed, and `verify_destructive_access` refuses to let any purge
    start.

    That is the safe direction, and it is also a configuration in which PrintStash
    can never reclaim storage. These two tests exist so the behaviour is a stated
    constraint rather than something discovered from a support thread; the
    operator-facing half (telling them to enable versioning) is not implemented.
    """

    def test_writes_a_receipt_with_no_version_identity(
        self, s3_backend: S3StorageBackend
    ) -> None:
        receipt = s3_backend.create_bytes(b"payload", "unversioned.bin")

        assert receipt.version_id is None

    def test_refuses_to_delete_an_object_it_cannot_name_immutably(
        self, s3_backend: S3StorageBackend
    ) -> None:
        receipt = s3_backend.create_bytes(b"payload", "unversioned-rollback.bin")

        # Fails closed. Deleting by key alone would remove whatever is at that key
        # now, which after a concurrent write is somebody else's object.
        assert s3_backend.rollback_create(receipt) is False
        assert s3_backend.read_bytes(receipt.key) == b"payload"

    def test_blocks_a_purge_from_starting_at_all(
        self, s3_backend: S3StorageBackend
    ) -> None:
        receipt = s3_backend.create_bytes(b"real", "unversioned-target.bin")

        # The probe cannot clean itself up, so the purge refuses to proceed. An
        # installation on a non-versioned bucket therefore never reclaims storage —
        # the trade PrintStash makes for never deleting the wrong bytes.
        with pytest.raises(RuntimeError, match="storage_delete_probe_cleanup"):
            s3_backend.verify_destructive_access([receipt.key])


class TestAdoptExisting:
    def test_recovers_a_publication_that_crashed_before_its_receipt(
        self, s3_backend: S3StorageBackend
    ) -> None:
        data = b"published-but-unrecorded"
        original = s3_backend.create_bytes(data, "adopt-me.bin")

        adopted = s3_backend.adopt_existing(
            original.key,
            expected_size=len(data),
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )

        # The restart path: the bytes reached S3 and the process died before
        # writing the row. Adoption is what lets the object be owned afterwards
        # instead of leaking forever.
        assert adopted.key == original.key
        assert adopted.token == original.token

    def test_reports_a_key_that_was_never_published(
        self, s3_backend: S3StorageBackend
    ) -> None:
        with pytest.raises(FileNotFoundError):
            s3_backend.adopt_existing(
                "adopt-missing.bin", expected_size=1, expected_sha256="0" * 64
            )

    def test_refuses_an_object_whose_size_disagrees(
        self, s3_backend: S3StorageBackend
    ) -> None:
        receipt = s3_backend.create_bytes(b"four", "adopt-size.bin")

        # Adopting on key alone would claim ownership of whatever happens to sit
        # there, which on a shared bucket is somebody else's object.
        with pytest.raises(StorageCollisionError):
            s3_backend.adopt_existing(
                receipt.key, expected_size=999, expected_sha256="0" * 64
            )

    def test_refuses_an_object_whose_bytes_disagree(
        self, s3_backend: S3StorageBackend
    ) -> None:
        data = b"actual-content"
        receipt = s3_backend.create_bytes(data, "adopt-digest.bin")

        # Same length, different content — the case a size check alone accepts.
        with pytest.raises(StorageCollisionError):
            s3_backend.adopt_existing(
                receipt.key,
                expected_size=len(data),
                expected_sha256=hashlib.sha256(b"x" * len(data)).hexdigest(),
            )


class TestReplaceStream:
    def test_replaces_the_bytes_behind_a_current_receipt(
        self, s3_backend: S3StorageBackend
    ) -> None:
        receipt = s3_backend.create_bytes(b"first", "replace-me.bin")

        replaced = s3_backend.replace_stream(BytesIO(b"second"), receipt)

        assert s3_backend.read_bytes(receipt.key) == b"second"
        assert replaced.token != receipt.token

    def test_refuses_to_replace_through_a_receipt_that_is_no_longer_current(
        self, s3_backend: S3StorageBackend
    ) -> None:
        receipt = s3_backend.create_bytes(b"first", "replace-stale.bin")
        s3_backend.replace_stream(BytesIO(b"second"), receipt)

        # The stale receipt still names the right key. Honouring it would let a
        # slow writer overwrite a newer version it never read.
        with pytest.raises(StorageCollisionError):
            s3_backend.replace_stream(BytesIO(b"third"), receipt)
        assert s3_backend.read_bytes(receipt.key) == b"second"


class TestVerifyDestructiveAccess:
    def test_asks_for_nothing_when_there_is_nothing_to_delete(
        self, s3_backend: S3StorageBackend
    ) -> None:
        # Called on every purge, including ones with an empty candidate list. A
        # probe object written for a no-op delete is a leak per sweep.
        assert s3_backend.verify_destructive_access([]) is None

    def test_proves_it_can_delete_before_deleting_anything(
        self, versioned_s3_backend: S3StorageBackend
    ) -> None:
        receipt = versioned_s3_backend.create_bytes(b"real", "probe-target.bin")

        versioned_s3_backend.verify_destructive_access([receipt.key])

        # The probe writes and removes its own object rather than testing against
        # the caller's, and it leaves nothing behind — a stranded probe would be
        # counted by the next audit as an unowned object.
        assert not [
            key for key in versioned_s3_backend.list_keys() if "delete-probes" in key
        ]


class TestDestructiveLifecycleFindings:
    def test_reports_nothing_for_a_bucket_with_no_expiry_rules(
        self, s3_backend: S3StorageBackend
    ) -> None:
        # An expiry rule over the vault prefix silently deletes a user's models on
        # the object store's own schedule, which no amount of PrintStash-side
        # retention can prevent. A fresh bucket has none, and the check must say so
        # rather than raising on a store with no lifecycle API at all.
        assert s3_backend.destructive_lifecycle_findings() == []
