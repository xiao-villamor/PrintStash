"""S3 storage safety reactions are deterministic without opening a socket.

These tests exercise the provider-specific evidence checks with a tiny client
that models S3 responses. The real wire protocol remains covered by the
contract tier.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Iterator

import pytest

from app.services import storage_backend
from app.services.storage_backend import (
    S3StorageBackend,
    StorageCollisionError,
    StorageConfigurationError,
    StorageTier,
)


class _MemoryS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str]] = {}
        self.deleted: list[dict[str, str]] = []

    def head_object(self, **kwargs: str) -> dict[str, object]:
        key = kwargs["Key"]
        payload, etag, version_id = self.objects[key]
        requested_version = kwargs.get("VersionId")
        if requested_version is not None and requested_version != version_id:
            import botocore.exceptions

            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "NoSuchVersion"}}, "HeadObject"
            )
        return {
            "ContentLength": len(payload),
            "ETag": etag,
            "VersionId": version_id,
            "Metadata": {"printstash-create-token": "token"},
        }

    def get_object(self, **kwargs: str) -> dict[str, BytesIO]:
        payload, _etag, _version_id = self.objects[kwargs["Key"]]
        return {"Body": BytesIO(payload)}

    def delete_object(self, **kwargs: str) -> None:
        self.deleted.append(kwargs)


def _bare_s3_backend(client: _MemoryS3Client) -> S3StorageBackend:
    backend = object.__new__(S3StorageBackend)
    backend._client = client  # type: ignore[attr-defined]
    backend._bucket = "vault"  # type: ignore[attr-defined]
    return backend


class TestS3Reclaim:
    def test_reclaims_a_matching_unversioned_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _MemoryS3Client()
        payload = b"remote payload"
        client.objects["vault-data/blob.bin"] = (payload, '"etag"', "")
        backend = _bare_s3_backend(client)
        monkeypatch.setattr(backend, "stream_chunks", lambda _key: iter([payload]))

        assert (
            backend.reclaim_unverified(
                "vault-data/blob.bin",
                expected_size=len(payload),
                expected_etag='"etag"',
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            is True
        )
        assert client.deleted == [{"Bucket": "vault", "Key": "vault-data/blob.bin"}]

    def test_preserves_unversioned_object_with_wrong_digest(self) -> None:
        client = _MemoryS3Client()
        payload = b"remote payload"
        key = "vault-data/blob.bin"
        client.objects[key] = (payload, '"etag"', "")

        assert (
            _bare_s3_backend(client).reclaim_unverified(
                key,
                expected_size=len(payload),
                expected_etag='"etag"',
                expected_sha256="0" * 64,
            )
            is False
        )
        assert client.deleted == []

    def test_reclaims_the_named_version(self) -> None:
        client = _MemoryS3Client()
        key = "vault-data/versioned.bin"
        client.objects[key] = (b"versioned", '"etag"', "v1")

        assert (
            _bare_s3_backend(client).reclaim_unverified(
                key,
                expected_size=9,
                expected_etag='"etag"',
                expected_version_id="v1",
            )
            is True
        )
        assert client.deleted == [{"Bucket": "vault", "Key": key, "VersionId": "v1"}]

    def test_treats_a_missing_named_version_as_reclaimed(self) -> None:
        client = _MemoryS3Client()
        key = "vault-data/versioned.bin"
        client.objects[key] = (b"versioned", '"etag"', "v1")

        assert (
            _bare_s3_backend(client).reclaim_unverified(
                key,
                expected_size=9,
                expected_etag='"etag"',
                expected_version_id="old-version",
            )
            is True
        )
        assert client.deleted == []

    def test_rejects_a_key_outside_the_s3_namespace(self) -> None:
        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            _bare_s3_backend(_MemoryS3Client()).reclaim_unverified(
                "other-prefix/blob.bin", expected_size=0, expected_etag=None
            )


class TestS3Namespace:
    def test_uses_the_configured_normalized_prefix_for_all_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            storage_backend, "settings", type("S", (), {"s3_root": "tenant/vault"})()
        )
        backend = _bare_s3_backend(_MemoryS3Client())

        assert backend._prefix() == "tenant/vault/"
        assert backend.blob_key("model", 1, "part.stl") == (
            "tenant/vault/files/model/v1/part.stl"
        )
        assert backend.namespace_for("tenant/vault/files/model/v1/part.stl") == (
            "vault/tenant/vault/"
        )

    def test_reports_the_managed_namespace_for_a_vault_key(self) -> None:
        backend = _bare_s3_backend(_MemoryS3Client())

        assert (
            backend.namespace_for("vault-data/files/model.stl") == "vault/vault-data/"
        )

    def test_rejects_a_key_before_the_managed_prefix(self) -> None:
        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            _bare_s3_backend(_MemoryS3Client()).namespace_for(
                "vault-data-old/model.stl"
            )


class TestS3ObjectInfo:
    def test_normalizes_an_unquoted_etag(self) -> None:
        class _Client:
            def head_object(self, **_kwargs: str) -> dict[str, object]:
                return {"ContentLength": 4, "ETag": "etag"}

        info = _bare_s3_backend(_Client()).object_info("vault-data/model.stl")  # type: ignore[arg-type]

        assert info is not None
        assert info.size == 4
        assert info.etag == '"etag"'

    def test_reports_a_missing_object_as_none(self) -> None:
        import botocore.exceptions

        class _Client:
            def head_object(self, **_kwargs: str) -> dict[str, object]:
                raise botocore.exceptions.ClientError(
                    {"Error": {"Code": "NoSuchKey"}}, "HeadObject"
                )

        assert _bare_s3_backend(_Client()).object_info("vault-data/missing.stl") is None  # type: ignore[arg-type]


class TestS3CapabilityProbe:
    def test_accepts_conditional_create_when_duplicate_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import botocore.exceptions

        class _Client:
            def __init__(self) -> None:
                self.payload = b""

            def put_object(self, **kwargs: object) -> None:
                if self.payload:
                    raise botocore.exceptions.ClientError(
                        {"Error": {"Code": "PreconditionFailed"}}, "PutObject"
                    )
                self.payload = bytes(kwargs["Body"])

            def get_object(self, **_kwargs: object) -> dict[str, BytesIO]:
                return {"Body": BytesIO(self.payload)}

            def delete_object(self, **_kwargs: object) -> None:
                self.payload = b""

        client = _Client()
        backend = _bare_s3_backend(client)  # type: ignore[arg-type]
        monkeypatch.setattr(
            storage_backend, "settings", type("S", (), {"s3_root": "vault-data"})()
        )

        assert backend._probe_conditional_create() is True  # type: ignore[attr-defined]

    def test_rejects_conditional_create_when_duplicate_is_overwritten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Client:
            def __init__(self) -> None:
                self.payload = b""

            def put_object(self, **kwargs: object) -> None:
                body = kwargs["Body"]
                self.payload = bytes(body)

            def get_object(self, **_kwargs: object) -> dict[str, BytesIO]:
                return {"Body": BytesIO(self.payload)}

            def delete_object(self, **_kwargs: object) -> None:
                self.payload = b""

        client = _Client()
        backend = _bare_s3_backend(client)  # type: ignore[arg-type]
        monkeypatch.setattr(
            storage_backend, "settings", type("S", (), {"s3_root": "vault-data"})()
        )

        assert backend._probe_conditional_create() is False  # type: ignore[attr-defined]

    def test_cleans_up_the_exact_probe_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import botocore.exceptions

        class _Client:
            def __init__(self) -> None:
                self.payload = b""
                self.deleted: list[dict[str, object]] = []

            def put_object(self, **kwargs: object) -> dict[str, str]:
                if self.payload:
                    raise botocore.exceptions.ClientError(
                        {"Error": {"Code": "PreconditionFailed"}}, "PutObject"
                    )
                self.payload = bytes(kwargs["Body"])
                return {"VersionId": "probe-version"}

            def get_object(self, **_kwargs: object) -> dict[str, BytesIO]:
                return {"Body": BytesIO(self.payload)}

            def delete_object(self, **kwargs: object) -> None:
                self.deleted.append(kwargs)

        client = _Client()
        backend = _bare_s3_backend(client)  # type: ignore[arg-type]
        monkeypatch.setattr(
            storage_backend, "settings", type("S", (), {"s3_root": "vault-data"})()
        )

        backend._probe_conditional_create()  # type: ignore[attr-defined]

        assert len(client.deleted) == 1
        assert client.deleted[0]["Bucket"] == "vault"
        assert str(client.deleted[0]["Key"]).startswith("vault-data/.printstash-probe/")
        assert client.deleted[0]["VersionId"] == "probe-version"

    def test_records_a_versioning_probe_failure_as_guarded(self) -> None:
        class _Client:
            def get_bucket_versioning(self, **_kwargs: str) -> dict[str, object]:
                raise OSError("versioning endpoint unavailable")

        backend = _bare_s3_backend(_Client())  # type: ignore[arg-type]

        backend._probe_capabilities()  # type: ignore[attr-defined]

        assert backend.capabilities.tier is StorageTier.GUARDED
        assert backend.probe_diagnostics == {
            "probed": True,
            "bucket_versioning": "unknown",
            "versioning_error": "OSError",
            "conditional_create": True,
        }


class _CoverageMemoryS3Pager:
    def __init__(self, objects: dict[str, tuple[bytes, dict[str, str], str]]) -> None:
        self.objects = objects

    def paginate(self, **_kwargs: object) -> Iterator[dict[str, object]]:
        yield {
            "Contents": [
                {"Key": key, "Size": len(payload)}
                for key, (payload, _metadata, _etag) in self.objects.items()
            ]
        }


class _CoverageMemoryS3Client:
    def __init__(self, *, versioned: bool = True) -> None:
        self.versioned = versioned
        self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}
        self.deleted: list[dict[str, object]] = []

    def head_bucket(self, **_kwargs: object) -> dict[str, object]:
        return {}

    def get_bucket_versioning(self, **_kwargs: object) -> dict[str, str]:
        return {"Status": "Enabled" if self.versioned else "Suspended"}

    def put_object(self, **kwargs: object) -> dict[str, str]:
        import botocore.exceptions

        key = str(kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "PreconditionFailed"}}, "PutObject"
            )
        if kwargs.get("IfMatch") is not None:
            current_etag = self.objects[key][2]
            if kwargs["IfMatch"] != current_etag:
                raise botocore.exceptions.ClientError(
                    {"Error": {"Code": "PreconditionFailed"}}, "PutObject"
                )
        body = kwargs["Body"]
        payload = body.read() if hasattr(body, "read") else bytes(body)  # type: ignore[arg-type]
        metadata = dict(kwargs.get("Metadata", {}))  # type: ignore[arg-type]
        etag = f'"etag-{len(self.objects) + 1}"'
        self.objects[key] = (payload, metadata, etag)
        response = {"ETag": etag}
        if self.versioned:
            response["VersionId"] = f"v{len(self.objects)}"
        return response

    def head_object(self, **kwargs: object) -> dict[str, object]:
        import botocore.exceptions

        key = str(kwargs["Key"])
        if key not in self.objects:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "NoSuchKey"}}, "HeadObject"
            )
        payload, metadata, etag = self.objects[key]
        return {
            "ContentLength": len(payload),
            "Metadata": metadata,
            "ETag": etag,
            "VersionId": "v1" if self.versioned else None,
        }

    def get_object(self, **kwargs: object) -> dict[str, BytesIO]:
        return {"Body": BytesIO(self.objects[str(kwargs["Key"])][0])}

    def delete_object(self, **kwargs: object) -> None:
        self.deleted.append(kwargs)
        self.objects.pop(str(kwargs["Key"]), None)

    def get_bucket_lifecycle_configuration(
        self, **_kwargs: object
    ) -> dict[str, object]:
        return {"Rules": []}

    def get_paginator(self, _name: str) -> _CoverageMemoryS3Pager:
        return _CoverageMemoryS3Pager(self.objects)

    def generate_presigned_url(self, *_args: object, **_kwargs: object) -> str:
        return "https://example.test/download"


def _memory_s3_backend(
    monkeypatch: pytest.MonkeyPatch,
    client: _CoverageMemoryS3Client | None = None,
) -> tuple[S3StorageBackend, _CoverageMemoryS3Client]:
    from app.services import storage_backend

    client = client or _CoverageMemoryS3Client()
    settings = type(
        "S3Settings",
        (),
        {
            "s3_bucket": "vault",
            "s3_region": "us-east-1",
            "s3_access_key": "access",
            "s3_secret_key": "secret",
            "s3_endpoint_url": "https://s3.example.test",
            "s3_root": "vault-data",
            "s3_addressing_style": "auto",
            "storage_provider": "s3",
            "s3_multipart_threshold_mb": 10,
            "s3_presigned_url_expire_seconds": 60,
        },
    )()
    monkeypatch.setattr(storage_backend, "settings", settings)
    monkeypatch.setattr("boto3.client", lambda **_kwargs: client)
    return S3StorageBackend(check_bucket=False), client


class TestS3ClientConfiguration:
    def test_uses_the_configured_addressing_style(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        fake = _CoverageMemoryS3Client()
        configured = type(
            "S3Settings",
            (),
            {
                "s3_bucket": "vault",
                "s3_region": "us-east-1",
                "s3_access_key": "access",
                "s3_secret_key": "secret",
                "s3_endpoint_url": "https://minio.example.test",
                "s3_root": "vault-data",
                "s3_addressing_style": "virtual",
                "storage_provider": "s3_self_hosted",
            },
        )()
        monkeypatch.setattr(storage_backend, "settings", configured)

        def client(**kwargs: object):
            captured.update(kwargs)
            return fake

        monkeypatch.setattr("boto3.client", client)

        S3StorageBackend(check_bucket=False)

        assert captured["region_name"] == "us-east-1"
        assert captured["config"].s3["addressing_style"] == "virtual"


class TestS3CompatibilityCoverage:
    def test_derives_all_object_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, _client = _memory_s3_backend(monkeypatch)

        assert backend.provider_id == "s3"
        assert backend.direct_path("vault-data/file") is None
        assert (
            backend.blob_key("model", 2, "part.stl")
            == "vault-data/files/model/v2/part.stl"
        )
        assert backend.thumbnail_key(1) == "vault-data/thumbs/1.webp"
        assert backend.source_cover_key(2) == "vault-data/source-covers/2.webp"
        assert (
            backend.capture_upload_slot_key("slot") == "vault-data/capture-slots/slot"
        )
        assert backend.legacy_thumbnail_key(3) == "vault-data/thumbs/3.png"
        assert backend.stl_cache_key("a" * 64) == f"vault-data/stl-cache/{'a' * 64}.stl"
        assert (
            backend.collection_image_key(4, "hero.webp")
            == "vault-data/collection-images/4/hero.webp"
        )
        assert (
            backend.document_file_key(5, "manual.pdf")
            == "vault-data/documents/5/manual.pdf"
        )
        assert (
            backend.document_image_key(6, "figure.webp")
            == "vault-data/document-images/6/figure.webp"
        )

    def test_round_trips_objects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/files/model/v1/part.stl"
        receipt = backend.create_bytes(b"payload", key)

        assert receipt.size == 7
        assert backend.read_bytes(key) == b"payload"
        assert list(backend.stream_chunks(key, chunk_size=3)) == [b"pay", b"loa", b"d"]
        destination = Path("/tmp") / f"printstash-s3-copy-{receipt.token}"
        try:
            assert backend.download_to_path(key, destination) == destination
            assert destination.read_bytes() == b"payload"
        finally:
            destination.unlink(missing_ok=True)
        assert backend.list_keys() == [key]
        assert list(backend.walk_keys()) == [key]
        assert backend.usage() == {
            "backend": "s3",
            "bucket": "vault",
            "prefix": "vault-data/",
            "object_count": 1,
            "total_size_bytes": 7,
        }
        assert backend.presigned_download_url(key, "part.stl")
        assert client.objects[key][0] == b"payload"

    def test_replaces_versioned_object(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/files/model/v1/part.stl"
        receipt = backend.create_bytes(b"old", key)
        replacement = backend.replace_bytes(b"new", receipt)
        assert replacement.size == 3
        assert backend.creation_matches(replacement)
        assert backend.reclaim_unverified(
            key,
            expected_size=3,
            expected_etag=replacement.etag,
            expected_version_id="v1",
        )
        assert client.deleted[-1]["VersionId"] == "v1"

    def test_adopts_existing_object_after_hash_proof(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/capture-slots/pending"
        payload = b"pending"
        client.objects[key] = (
            payload,
            {"printstash-create-token": "operation"},
            '"etag"',
        )
        receipt = backend.adopt_existing(
            key,
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
        assert receipt.token == "operation"
        assert backend.creation_matches(receipt)

    def test_reports_health_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, _client = _memory_s3_backend(
            monkeypatch, _CoverageMemoryS3Client(versioned=False)
        )
        backend.ensure_setup()
        assert backend.probe_diagnostics["bucket_versioning"] == "suspended"
        assert backend.health_probe()["ok"] is True
        assert backend.capabilities.object_identity.value == "etag"

    def test_marks_an_overwriting_provider_read_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class UnsafeClient(_CoverageMemoryS3Client):
            def put_object(self, **kwargs: object) -> dict[str, str]:
                key = str(kwargs["Key"])
                body = kwargs["Body"]
                payload = body.read() if hasattr(body, "read") else bytes(body)  # type: ignore[arg-type]
                self.objects[key] = (payload, {}, '"unsafe"')
                return {"ETag": '"unsafe"'}

        backend, _client = _memory_s3_backend(monkeypatch, UnsafeClient())
        backend.ensure_setup()

        assert backend._read_only is True
        assert backend.probe_diagnostics["read_only"] is True

    def test_requires_a_bucket_setting_before_constructing_s3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, _client = _memory_s3_backend(monkeypatch)
        from app.services import storage_backend

        storage_backend.settings.s3_bucket = ""
        with pytest.raises(RuntimeError, match="VAULT_S3_BUCKET"):
            S3StorageBackend(check_bucket=False)

    def test_rejects_unconfigured_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, _client = _memory_s3_backend(monkeypatch)
        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            backend.list_keys("other-root/")
        with pytest.raises(RuntimeError, match="unchecked_storage_move_disabled"):
            backend.move("vault-data/a", "vault-data/b")
        with pytest.raises(RuntimeError, match="unchecked_storage_delete_disabled"):
            backend.delete("vault-data/a")

    def test_rejects_duplicate_create(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, _client = _memory_s3_backend(monkeypatch)
        key = "vault-data/files/model/v1/part.stl"
        backend.create_bytes(b"payload", key)
        with pytest.raises(StorageCollisionError):
            backend.create_bytes(b"replacement", key)
        backend._read_only = True
        with pytest.raises(StorageConfigurationError, match="remote_storage_read_only"):
            backend.create_bytes(b"another", "vault-data/files/model/v2/part.stl")

    def test_uses_the_single_part_fallback_when_multipart_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, _client = _memory_s3_backend(monkeypatch)
        import botocore.exceptions

        def unsupported_multipart(*_args: object, **_kwargs: object) -> dict[str, str]:
            raise botocore.exceptions.ParamValidationError(
                report="multipart unsupported"
            )

        monkeypatch.setattr(backend, "_multipart_create", unsupported_multipart)
        from app.services import storage_backend

        storage_backend.settings.s3_multipart_threshold_mb = 0
        receipt = backend.create_bytes(b"payload", "vault-data/files/large.bin")
        assert receipt.size == 7

    def test_rejects_a_stale_s3_replacement_proof(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/files/model/v1/part.stl"
        receipt = backend.create_bytes(b"old", key)
        client.objects[key] = (b"other", {}, '"different"')

        with pytest.raises(StorageCollisionError):
            backend.replace_bytes(b"new", receipt)

    def test_reports_missing_lifecycle_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        import botocore.exceptions

        def missing_lifecycle(**_kwargs: object) -> dict[str, object]:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "NoSuchLifecycleConfiguration"}},
                "GetBucketLifecycleConfiguration",
            )

        monkeypatch.setattr(
            client, "get_bucket_lifecycle_configuration", missing_lifecycle
        )
        assert backend.destructive_lifecycle_findings() == []

        def unavailable_bucket(**_kwargs: object) -> dict[str, object]:
            raise OSError("endpoint unavailable")

        monkeypatch.setattr(client, "head_bucket", unavailable_bucket)
        assert backend.health_probe()["ok"] is False

    def test_rejects_invalid_s3_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, _client = _memory_s3_backend(monkeypatch)
        with pytest.raises(StorageConfigurationError, match="s3_root_invalid"):
            backend._normalized_root("../unsafe")
        with pytest.raises(FileNotFoundError):
            backend.adopt_existing(
                "vault-data/missing", expected_size=0, expected_sha256="0" * 64
            )

    def test_rejects_s3_reclaim_when_version_evidence_disagrees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/versioned"
        client.objects[key] = (b"payload", {"printstash-create-token": "token"}, "etag")

        assert (
            backend.reclaim_unverified(
                key, expected_size=999, expected_etag='"etag"', expected_version_id="v1"
            )
            is False
        )
        assert (
            backend.reclaim_unverified(
                key, expected_size=7, expected_etag='"other"', expected_version_id="v1"
            )
            is False
        )

        import botocore.exceptions

        def access_denied(**_kwargs: object) -> dict[str, object]:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "AccessDenied"}}, "HeadObject"
            )

        monkeypatch.setattr(client, "head_object", access_denied)
        with pytest.raises(botocore.exceptions.ClientError):
            backend.reclaim_unverified(
                key, expected_size=7, expected_etag=None, expected_version_id="v1"
            )

    def test_rejects_s3_reclaim_when_unversioned_evidence_disagrees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/unversioned"
        client.objects[key] = (b"payload", {}, "etag")

        assert (
            backend.reclaim_unverified(key, expected_size=999, expected_etag=None)
            is False
        )
        assert (
            backend.reclaim_unverified(key, expected_size=7, expected_etag='"other"')
            is False
        )
        assert (
            backend.reclaim_unverified(
                key, expected_size=7, expected_etag='"etag"', expected_sha256="0" * 64
            )
            is False
        )
        assert (
            backend.reclaim_unverified(key, expected_size=7, expected_etag='"etag"')
            is True
        )

    def test_handles_missing_s3_lifecycle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        import botocore.exceptions

        def denied_lifecycle(**_kwargs: object) -> dict[str, object]:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "AccessDenied"}},
                "GetBucketLifecycleConfiguration",
            )

        monkeypatch.setattr(
            client, "get_bucket_lifecycle_configuration", denied_lifecycle
        )
        assert backend.destructive_lifecycle_findings() == []

        monkeypatch.setattr(
            client,
            "get_bucket_lifecycle_configuration",
            lambda **_kwargs: {
                "Rules": [
                    {"Status": "Disabled", "Expiration": {"Days": 1}},
                    {"Status": "Enabled", "Filter": {"Prefix": "other/"}},
                ]
            },
        )
        assert backend.destructive_lifecycle_findings() == []

    def test_runs_s3_write_wrapper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/wrapped"
        assert backend.write_bytes(b"payload", key) == 7
        client.objects.pop(key)
        monkeypatch.setattr(backend, "object_info", lambda _key: None)
        with pytest.raises(RuntimeError, match="could not verify destination"):
            backend.write_stream(BytesIO(b"payload"), key)

    def test_aborts_multipart_when_abort_cleanup_also_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tempfile

        backend, client = _memory_s3_backend(monkeypatch)
        monkeypatch.setattr(
            client,
            "create_multipart_upload",
            lambda **_kwargs: {"UploadId": "upload-3"},
            raising=False,
        )
        monkeypatch.setattr(
            client,
            "upload_part",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("part failed")),
            raising=False,
        )
        monkeypatch.setattr(
            client,
            "abort_multipart_upload",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("abort failed")),
            raising=False,
        )
        with tempfile.SpooledTemporaryFile(max_size=1) as source:
            source.write(b"multipart")
            source.seek(0)
            with pytest.raises(OSError, match="part failed"):
                backend._multipart_create(source, key="vault-data/large", token="token")

    def test_maps_s3_replace_precondition_failure_to_collision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/replace"
        receipt = backend.create_bytes(b"old", key)
        import botocore.exceptions

        def precondition_failed(**_kwargs: object) -> dict[str, str]:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "PreconditionFailed"}}, "PutObject"
            )

        monkeypatch.setattr(backend, "creation_matches", lambda _receipt: True)
        monkeypatch.setattr(client, "put_object", precondition_failed)
        with pytest.raises(StorageCollisionError):
            backend.replace_bytes(b"new", receipt)

    def test_preserves_an_unexpected_s3_replace_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/replace"
        receipt = backend.create_bytes(b"old", key)
        import botocore.exceptions

        monkeypatch.setattr(backend, "creation_matches", lambda _receipt: True)
        monkeypatch.setattr(
            client,
            "put_object",
            lambda **_kwargs: (_ for _ in ()).throw(
                botocore.exceptions.ClientError(
                    {"Error": {"Code": "AccessDenied"}}, "PutObject"
                )
            ),
        )
        with pytest.raises(botocore.exceptions.ClientError):
            backend.replace_bytes(b"new", receipt)

    def test_reports_s3_replace_verification_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, _client = _memory_s3_backend(monkeypatch)
        key = "vault-data/replace"
        receipt = backend.create_bytes(b"old", key)
        monkeypatch.setattr(backend, "creation_matches", lambda _receipt: True)
        monkeypatch.setattr(backend, "object_info", lambda _key: None)
        with pytest.raises(RuntimeError, match="replace_verification_failed"):
            backend.replace_bytes(b"new", receipt)

    def test_validates_s3_receipt_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/object"
        receipt = backend.create_bytes(b"data", key)
        assert (
            backend.creation_matches(
                receipt.__class__(**{**receipt.__dict__, "namespace": "other/"})
            )
            is False
        )
        client.objects[key] = (b"data", client.objects[key][1], '"changed"')
        assert backend.creation_matches(receipt) is False

    def test_handles_missing_version_during_s3_rollback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        key = "vault-data/object"
        receipt = backend.create_bytes(b"data", key)
        import botocore.exceptions

        def missing_version(**_kwargs: object) -> dict[str, object]:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "NoSuchVersion"}}, "HeadObject"
            )

        monkeypatch.setattr(client, "head_object", missing_version)
        assert backend.rollback_create(receipt) is True

    def test_keeps_an_unversioned_s3_object_when_identity_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, _client = _memory_s3_backend(
            monkeypatch, _CoverageMemoryS3Client(versioned=False)
        )
        key = "vault-data/object"
        receipt = backend.create_bytes(b"data", key)
        assert receipt.version_id is None
        assert backend.rollback_create(receipt) is False

    def test_probes_s3_destructive_access_with_a_versioned_receipt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend, client = _memory_s3_backend(monkeypatch)
        backend.verify_destructive_access(["vault-data/object"])
        assert client.deleted

    def test_uploads_a_local_file_through_the_s3_create_seam(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        backend, _client = _memory_s3_backend(monkeypatch)
        source = tmp_path / "source.bin"
        source.write_bytes(b"payload")
        backend.upload_file(source, "vault-data/uploaded.bin")
        assert backend.read_bytes("vault-data/uploaded.bin") == b"payload"

    def test_completes_a_multipart_upload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tempfile

        backend, client = _memory_s3_backend(monkeypatch)
        parts: list[dict[str, object]] = []

        def create_multipart_upload(**_kwargs: object) -> dict[str, str]:
            return {"UploadId": "upload-1"}

        def upload_part(**kwargs: object) -> dict[str, str]:
            parts.append(kwargs)
            return {"ETag": '"part"'}

        def complete_multipart_upload(**kwargs: object) -> dict[str, str]:
            key = str(kwargs["Key"])
            client.objects[key] = (b"multipart", {}, '"multipart"')
            return {"ETag": '"multipart"'}

        monkeypatch.setattr(
            client, "create_multipart_upload", create_multipart_upload, raising=False
        )
        monkeypatch.setattr(client, "upload_part", upload_part, raising=False)
        monkeypatch.setattr(
            client,
            "complete_multipart_upload",
            complete_multipart_upload,
            raising=False,
        )
        with tempfile.SpooledTemporaryFile(max_size=1) as source:
            source.write(b"multipart")
            source.seek(0)
            result = backend._multipart_create(
                source, key="vault-data/large", token="token"
            )

        assert result["ETag"] == '"multipart"'
        assert len(parts) == 1

    def test_aborts_a_failed_multipart_upload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tempfile

        backend, client = _memory_s3_backend(monkeypatch)
        aborted: list[str] = []

        monkeypatch.setattr(
            client,
            "create_multipart_upload",
            lambda **_kwargs: {"UploadId": "upload-2"},
            raising=False,
        )

        def fail_upload(**_kwargs: object) -> dict[str, str]:
            raise OSError("part failed")

        monkeypatch.setattr(client, "upload_part", fail_upload, raising=False)
        monkeypatch.setattr(
            client,
            "abort_multipart_upload",
            lambda **kwargs: aborted.append(str(kwargs["UploadId"])),
            raising=False,
        )
        with tempfile.SpooledTemporaryFile(max_size=1) as source:
            source.write(b"multipart")
