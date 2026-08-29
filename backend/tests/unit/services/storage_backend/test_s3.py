"""S3 storage safety reactions are deterministic without opening a socket.

These tests exercise the provider-specific evidence checks with a tiny client
that models S3 responses. The real wire protocol remains covered by the
contract tier.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

import pytest

from app.services import storage_backend
from app.services.storage_backend import (
    S3StorageBackend,
    StorageCollisionError,
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
