"""OpenDAL adapter storage operations preserve remote object safety locally.

The contract suite exercises real WebDAV and SFTP servers. This unit suite
drives transport-neutral branches with a deterministic operator, so namespace
validation, collision cleanup, stream handling, and diagnostics remain covered
without requiring a remote service.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO
from unittest.mock import Mock

import httpx
import pytest

from app.services import storage_opendal
from app.services.storage_backend import (
    StorageCollisionError,
    StorageConfigurationError,
)
from app.services.storage_providers import TransportKind, TransportSpec


def _spec(
    kind: TransportKind = TransportKind.WEBDAV,
    *,
    options: dict[str, str | int | bool] | None = None,
) -> TransportSpec:
    return TransportSpec(
        kind=kind,
        provider="test-remote",
        namespace="vault/data",
        options=options or {},
    )


class _Writer:
    def __init__(self, operator: "_MemoryOperator", key: str) -> None:
        self._operator = operator
        self._key = key
        self._parts: list[bytes] = []

    def __enter__(self) -> "_Writer":
        return self

    def __exit__(self, *_args: object) -> None:
        self._operator.objects[self._key] = b"".join(self._parts)

    def write(self, data: bytes) -> None:
        self._parts.append(bytes(data))


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._stream = BytesIO(data)

    def __enter__(self) -> "_Reader":
        return self

    def __exit__(self, *_args: object) -> None:
        self._stream.close()

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class _MemoryOperator:
    _printstash_test_double = True

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def check(self) -> None:
        return None

    def exists(self, key: str) -> bool:
        return key in self.objects

    def open(self, key: str, mode: str) -> _Writer | _Reader:
        if mode == "wb":
            return _Writer(self, key)
        return _Reader(self.objects[key])

    def write(self, key: str, data: bytes | memoryview) -> None:
        self.objects[key] = bytes(data)

    def write_exclusive(self, key: str, source: BinaryIO) -> None:
        if key in self.objects:
            raise FileExistsError(key)
        self.objects[key] = source.read()

    def rename(self, source: str, destination: str) -> None:
        self.objects[destination] = self.objects.pop(source)

    def stat(self, key: str) -> SimpleNamespace:
        return SimpleNamespace(
            content_length=len(self.objects[key]), etag=f"etag:{key}"
        )

    def read(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def scan(self, prefix: str):
        return [
            SimpleNamespace(path=key)
            for key in sorted(self.objects)
            if key.startswith(prefix)
        ]


class _StreamOperator(_MemoryOperator):
    def write_stream(self, key: str, source: BinaryIO) -> None:
        self.objects[key] = source.read()


class _ChunkOperator(_MemoryOperator):
    def stream_chunks(self, key: str, chunk_size: int):
        data = self.objects[key]
        return (
            data[offset : offset + chunk_size]
            for offset in range(0, len(data), chunk_size)
        )


class _OpenDALWriteOperator(_MemoryOperator):
    def __init__(self, *, conditional_create: bool) -> None:
        super().__init__()
        self.conditional_create = conditional_create
        self.open_options: list[dict[str, object]] = []

    def capability(self):
        return SimpleNamespace(write_with_if_not_exists=self.conditional_create)

    def open(self, key: str, mode: str, **options: object) -> _Writer | _Reader:
        self.open_options.append(options)
        return super().open(key, mode)


class _OneShotWriter(_Writer):
    def write(self, data: bytes) -> None:
        if self._parts:
            raise OSError("OneShotWriter doesn't support multiple write")
        super().write(data)


class _GoogleDriveOperator(_OpenDALWriteOperator):
    def open(self, key: str, mode: str, **options: object) -> _Writer | _Reader:
        self.open_options.append(options)
        if mode == "wb":
            return _OneShotWriter(self, key)
        return _Reader(self.objects[key])


class _RenameFailureOperator(_MemoryOperator):
    def rename(self, source: str, destination: str) -> None:
        del source, destination
        raise OSError("rename failed")


class _CleanupFailureOperator(_RenameFailureOperator):
    def delete(self, key: str) -> None:
        del key
        raise OSError("cleanup failed")


class _CheckFailureOperator(_MemoryOperator):
    def check(self) -> None:
        raise OSError("remote unavailable")


class TestOpenDALStorageBackend:
    def test_rejects_a_non_remote_transport(self) -> None:
        with pytest.raises(StorageConfigurationError, match="unsupported remote"):
            storage_opendal.OpenDALStorageBackend(
                _spec(TransportKind.LOCAL), operator=_MemoryOperator()
            )

    def test_derives_all_storage_keys_inside_its_namespace(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        assert backend.legacy_thumbnail_key(7) == "vault/data/thumbs/7.png"
        assert backend.source_cover_key(8) == "vault/data/source-covers/8.webp"
        assert (
            backend.capture_upload_slot_key("slot") == "vault/data/capture-slots/slot"
        )
        assert backend.stl_cache_key("a" * 64) == f"vault/data/cache/stl/{'a' * 64}.stl"
        assert (
            backend.collection_image_key(9, "cover.webp")
            == "vault/data/collection-images/9/cover.webp"
        )
        assert (
            backend.document_file_key(10, "manual.pdf")
            == "vault/data/documents/10/manual.pdf"
        )
        assert (
            backend.document_image_key(11, "image.webp")
            == "vault/data/document-images/11/image.webp"
        )
        assert (
            backend.multipart_model_cover_key(12, "cover.webp")
            == "vault/data/multipart-covers/12/cover.webp"
        )

    def test_missing_remote_object_raises_file_not_found(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        with pytest.raises(FileNotFoundError, match="vault/data/missing.stl"):
            backend.stat_size("vault/data/missing.stl")

    @pytest.mark.parametrize(
        "key",
        [
            pytest.param("outside/file.stl", id="outside-namespace"),
            pytest.param("vault/data/", id="empty-relative"),
            pytest.param("vault/data/../file.stl", id="parent-relative"),
        ],
    )
    def test_rejects_a_key_that_is_not_a_safe_namespace_member(self, key: str) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        with pytest.raises(ValueError):
            backend.namespace_for(key)

    def test_publishes_a_nonempty_webdav_stream(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(1)

        receipt = backend.create_stream(BytesIO(b"webdav"), key)

        assert operator.objects["thumbs/1.webp"] == b"webdav"
        assert (receipt.key, receipt.size, receipt.etag) == (
            key,
            6,
            "etag:thumbs/1.webp",
        )

    def test_publishes_an_empty_webdav_stream(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)

        receipt = backend.create_stream(BytesIO(), backend.thumbnail_key(2))

        assert receipt.size == 0
        assert operator.objects["thumbs/2.webp"] == b""

    def test_publishes_a_stream_through_the_sftp_writer(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.SFTP), operator=operator
        )

        backend.create_stream(BytesIO(b"sftp"), backend.thumbnail_key(3))

        assert operator.objects["thumbs/3.webp"] == b"sftp"

    def test_s3_requests_opendal_conditional_creation_when_available(self) -> None:
        operator = _OpenDALWriteOperator(conditional_create=True)
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.S3), operator=operator
        )

        backend.create_stream(BytesIO(b"s3"), backend.thumbnail_key(40))

        assert operator.objects["thumbs/40.webp"] == b"s3"
        assert operator.open_options == [{"if_not_exists": True}]

    def test_google_drive_never_overwrites_an_observed_existing_object(self) -> None:
        operator = _OpenDALWriteOperator(conditional_create=False)
        operator.objects["thumbs/41.webp"] = b"existing"
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.GDRIVE), operator=operator
        )

        with pytest.raises(StorageCollisionError):
            backend.create_stream(BytesIO(b"replacement"), backend.thumbnail_key(41))

        assert operator.objects["thumbs/41.webp"] == b"existing"
        assert operator.open_options == []

    def test_google_drive_publishes_a_multi_chunk_stream_with_one_write(self) -> None:
        operator = _GoogleDriveOperator(conditional_create=False)
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.GDRIVE), operator=operator
        )
        payload = b"g" * (1024 * 1024 + 17)

        receipt = backend.create_stream(BytesIO(payload), backend.thumbnail_key(42))

        assert operator.objects["thumbs/42.webp"] == payload
        assert receipt.size == len(payload)

    def test_google_drive_publishes_an_empty_stream(self) -> None:
        operator = _GoogleDriveOperator(conditional_create=False)
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.GDRIVE), operator=operator
        )

        receipt = backend.create_stream(BytesIO(), backend.thumbnail_key(45))

        assert operator.objects["thumbs/45.webp"] == b""
        assert receipt.size == 0

    def test_explicitly_deletes_an_unversioned_owned_object(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.GDRIVE), operator=operator
        )
        key = backend.thumbnail_key(43)
        operator.objects["thumbs/43.webp"] = b"owned"

        backend.delete_owned_unversioned(
            key,
            expected_size=5,
            expected_etag="etag:thumbs/43.webp",
        )

        assert not backend.exists(key)

    def test_refuses_to_delete_a_changed_unversioned_object(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.GDRIVE), operator=operator
        )
        key = backend.thumbnail_key(44)
        operator.objects["thumbs/44.webp"] = b"replacement"

        with pytest.raises(StorageConfigurationError, match="identity_changed"):
            backend.delete_owned_unversioned(
                key,
                expected_size=5,
                expected_etag="old-etag",
            )

        assert backend.read_bytes(key) == b"replacement"

    def test_uses_a_stream_writer_when_the_operator_exposes_one(self) -> None:
        operator = _StreamOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)

        backend.create_stream(BytesIO(b"stream"), backend.thumbnail_key(4))

        assert operator.objects["thumbs/4.webp"] == b"stream"

    def test_removes_a_temporary_object_after_a_collision(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(5)
        backend.create_bytes(b"original", key)

        with pytest.raises(StorageCollisionError):
            backend.create_bytes(b"replacement", key)

        assert operator.objects["thumbs/5.webp"] == b"original"
        assert not any(name.startswith(".printstash-tmp/") for name in operator.objects)

    def test_preserves_the_original_error_when_temporary_cleanup_fails(self) -> None:
        operator = _CleanupFailureOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)

        with pytest.raises(OSError, match="rename failed"):
            backend.create_bytes(b"payload", backend.thumbnail_key(6))

    def test_moves_a_remote_object_without_replacement(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        source = backend.thumbnail_key(7)
        destination = backend.thumbnail_key(8)
        backend.create_bytes(b"payload", source)

        backend.move(source, destination)

        assert operator.objects["thumbs/8.webp"] == b"payload"
        assert not backend.exists(source)

    def test_rejects_a_move_to_an_existing_object(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        source = backend.thumbnail_key(9)
        destination = backend.thumbnail_key(10)
        backend.create_bytes(b"source", source)
        backend.create_bytes(b"destination", destination)

        with pytest.raises(StorageCollisionError):
            backend.move(source, destination)

        assert operator.objects["thumbs/9.webp"] == b"source"

    def test_reports_remote_object_size(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(11)
        backend.create_bytes(b"12345", key)

        assert backend.stat_size(key) == 5

    def test_reports_remote_object_metadata(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(11)
        backend.create_bytes(b"12345", key)

        assert backend.object_info(key).etag == "etag:thumbs/11.webp"  # type: ignore[union-attr]

    def test_returns_no_object_metadata_for_a_missing_key(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        assert backend.object_info(backend.thumbnail_key(12)) is None

    def test_reads_stream_chunks_from_an_operator_stream(self) -> None:
        operator = _ChunkOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(13)
        backend.create_bytes(b"abcdefgh", key)

        assert list(backend.stream_chunks(key, 3)) == [b"abc", b"def", b"gh"]

    def test_reads_stream_chunks_from_an_open_reader(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(14)
        backend.create_bytes(b"abcdefgh", key)

        assert list(backend.stream_chunks(key, 3)) == [b"abc", b"def", b"gh"]

    def test_downloads_an_object_to_a_new_path(self, tmp_path: Path) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(15)
        backend.create_bytes(b"download", key)

        destination = backend.download_to_path(key, tmp_path / "nested" / "file.webp")

        assert destination.read_bytes() == b"download"

    def test_uploads_a_file_into_remote_storage(self, tmp_path: Path) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        source = tmp_path / "source.webp"
        source.write_bytes(b"upload")

        backend.upload_file(source, backend.thumbnail_key(16))

        assert operator.objects["thumbs/16.webp"] == b"upload"

    def test_refuses_unchecked_remote_delete(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        with pytest.raises(RuntimeError, match="unchecked_storage_delete_disabled"):
            backend.delete(backend.thumbnail_key(17))

    def test_lists_remote_objects(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        backend.create_bytes(b"one", backend.thumbnail_key(18))
        backend.create_bytes(b"two", backend.thumbnail_key(19))

        assert backend.list_keys("vault/data/thumbs") == [
            "vault/data/thumbs/18.webp",
            "vault/data/thumbs/19.webp",
        ]

    def test_summarises_remote_object_usage(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        backend.create_bytes(b"one", backend.thumbnail_key(18))
        backend.create_bytes(b"two", backend.thumbnail_key(19))

        assert backend.usage("vault/data/thumbs") == {"bytes": 6, "objects": 2}

    def test_returns_an_empty_usage_summary_for_no_objects(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        assert backend.usage() == {"bytes": 0, "objects": 0}

    def test_reports_a_healthy_remote_operator(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        result = backend.health_probe()

        assert result["ok"] is True
        assert result["provider"] == "test-remote"
        assert result["diagnostics"]["verified_mutation"] is False  # type: ignore[index]

    def test_reports_a_failed_remote_operator(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_CheckFailureOperator()
        )

        result = backend.health_probe()

        assert result["ok"] is False
        assert result["error"] == "OSError"

    def test_setup_refuses_a_provider_that_overwrites_duplicate_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        calls = 0

        def overwrite(_source: BinaryIO, key: str):
            nonlocal calls
            calls += 1
            operator.objects[backend._relative(key)] = b"overwritten"
            return SimpleNamespace(
                key=key,
                size=len(b"overwritten"),
                token="probe",
                backend=backend.backend_name,
                namespace=backend.namespace_for(key),
            )

        monkeypatch.setattr(backend, "create_stream", overwrite)

        backend.ensure_setup()

        assert calls == 2
        assert backend.capabilities.conditional_create is False
        assert backend.probe_diagnostics["conditional_create"] is False
        monkeypatch.undo()
        with pytest.raises(StorageConfigurationError, match="remote_storage_read_only"):
            backend.create_bytes(b"new", backend.thumbnail_key(99))

    def test_exposes_no_direct_path(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        key = backend.thumbnail_key(20)

        assert backend.direct_path(key) is None

    def test_exposes_no_presigned_url(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        key = backend.thumbnail_key(20)

        assert backend.presigned_download_url(key, "thumb.webp") is None

    def test_reclaims_a_missing_object(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        assert (
            backend.reclaim_unverified(
                backend.thumbnail_key(21), expected_size=1, expected_etag=None
            )
            is True
        )

    def test_refuses_reclaim_when_size_does_not_match(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(22)
        backend.create_bytes(b"payload", key)

        assert (
            backend.reclaim_unverified(key, expected_size=1, expected_etag=None)
            is False
        )

    def test_refuses_reclaim_when_etag_does_not_match(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(23)
        backend.create_bytes(b"payload", key)

        assert (
            backend.reclaim_unverified(key, expected_size=7, expected_etag="wrong")
            is False
        )

    def test_refuses_reclaim_when_hash_does_not_match(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(24)
        backend.create_bytes(b"payload", key)

        assert (
            backend.reclaim_unverified(
                key,
                expected_size=7,
                expected_etag=None,
                expected_sha256="0" * 64,
            )
            is False
        )

    def test_reclaims_when_all_object_evidence_matches(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(25)
        backend.create_bytes(b"payload", key)

        assert (
            backend.reclaim_unverified(
                key,
                expected_size=7,
                expected_etag="etag:thumbs/25.webp",
                expected_sha256=hashlib.sha256(b"payload").hexdigest().upper(),
                expected_version_id="ignored",
            )
            is False
        )
        assert operator.exists("thumbs/25.webp")

    def test_reclaims_when_object_evidence_matches(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(26)
        backend.create_bytes(b"payload", key)

        assert (
            backend.reclaim_unverified(
                key, expected_size=7, expected_etag="etag:thumbs/26.webp"
            )
            is False
        )
        assert operator.exists("thumbs/26.webp")

    def test_builds_an_escaped_webdav_url(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(
                options={
                    "endpoint_url": "https://dav.example/base/",
                    "root": "remote root",
                }
            ),
            operator=_MemoryOperator(),
        )

        assert backend._webdav_url("nested/file name") == (
            "https://dav.example/base/remote%20root/nested/file%20name"
        )

    def test_rejects_a_missing_webdav_endpoint(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        with pytest.raises(StorageConfigurationError, match="endpoint_required"):
            backend._webdav_url("file")

    def test_maps_a_webdav_move_collision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=_MemoryOperator(),
        )
        monkeypatch.setattr(
            httpx, "request", lambda *_args, **_kwargs: httpx.Response(412)
        )

        with pytest.raises(StorageCollisionError):
            backend._webdav_move_create_only("tmp", "destination")

    def test_rejects_a_webdav_move_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=_MemoryOperator(),
        )
        monkeypatch.setattr(
            httpx, "request", lambda *_args, **_kwargs: httpx.Response(500)
        )

        with pytest.raises(StorageConfigurationError, match="webdav_move_failed"):
            backend._webdav_move_create_only("tmp", "destination")

    def test_maps_a_webdav_move_failure_when_destination_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _MemoryOperator()
        operator.objects["destination"] = b"winning publication"
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=operator,
        )
        monkeypatch.setattr(
            httpx, "request", lambda *_args, **_kwargs: httpx.Response(500)
        )

        with pytest.raises(StorageCollisionError):
            backend._webdav_move_create_only("tmp", "destination")

    def test_accepts_a_confirmed_webdav_collection_race(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _MemoryOperator()
        operator.objects["a"] = b"collection"
        operator.objects["a/b"] = b"collection"
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=operator,
        )
        monkeypatch.setattr(
            httpx, "request", lambda *_args, **_kwargs: httpx.Response(500)
        )

        backend._webdav_ensure_parent("a/b/file")

    def test_rejects_an_unconfirmed_webdav_collection_race(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=_MemoryOperator(),
        )
        monkeypatch.setattr(
            httpx, "request", lambda *_args, **_kwargs: httpx.Response(500)
        )

        with pytest.raises(StorageConfigurationError, match="webdav_mkcol_failed"):
            backend._webdav_ensure_parent("a/file")

    def test_creates_a_nonempty_webdav_http_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=operator,
        )
        monkeypatch.setattr(backend, "_webdav_ensure_parent", lambda _relative: None)

        def request(_method: str, *_args, **_kwargs) -> httpx.Response:
            temporary = next(
                key for key in operator.objects if key.startswith(".printstash-tmp-")
            )
            operator.objects["thumbs/30.webp"] = operator.objects.pop(temporary)
            return httpx.Response(201)

        monkeypatch.setattr(httpx, "request", request)
        receipt = backend.create_bytes(b"http", backend.thumbnail_key(30))

        assert receipt.size == 4
        assert operator.objects["thumbs/30.webp"] == b"http"

    def test_creates_an_empty_webdav_http_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=operator,
        )
        monkeypatch.setattr(backend, "_webdav_ensure_parent", lambda _relative: None)

        def request(_method: str, *_args, **_kwargs) -> httpx.Response:
            temporary = next(
                key for key in operator.objects if key.startswith(".printstash-tmp-")
            )
            operator.objects["thumbs/31.webp"] = operator.objects.pop(temporary)
            return httpx.Response(204)

        monkeypatch.setattr(httpx, "request", request)
        receipt = backend.create_stream(BytesIO(), backend.thumbnail_key(31))

        assert receipt.size == 0
        assert operator.objects["thumbs/31.webp"] == b""

    def test_preserves_http_publication_failure_when_cleanup_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=operator,
        )
        monkeypatch.setattr(backend, "_webdav_ensure_parent", lambda _relative: None)

        def request(_method: str, *_args, **_kwargs) -> httpx.Response:
            return httpx.Response(500)

        def fail_delete(_key: str) -> None:
            raise OSError("cleanup failed")

        monkeypatch.setattr(httpx, "request", request)
        monkeypatch.setattr(operator, "delete", fail_delete)

        with pytest.raises(StorageConfigurationError, match="webdav_move_failed"):
            backend.create_bytes(b"payload", backend.thumbnail_key(32))

    def test_rejects_webdav_without_a_protocol_endpoint(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=SimpleNamespace()
        )

        with pytest.raises(StorageConfigurationError, match="protocol_endpoint"):
            backend.create_bytes(b"payload", backend.thumbnail_key(33))

    def test_moves_a_webdav_object_through_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _MemoryOperator()
        operator.objects["thumbs/source.webp"] = b"data"
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(options={"endpoint_url": "https://dav.example", "root": "vault"}),
            operator=operator,
        )
        monkeypatch.setattr(backend, "_webdav_ensure_parent", lambda _relative: None)

        def request(_method: str, *_args, **_kwargs) -> httpx.Response:
            operator.objects["thumbs/dest.webp"] = operator.objects.pop(
                "thumbs/source.webp"
            )
            return httpx.Response(204)

        monkeypatch.setattr(httpx, "request", request)
        backend.move(backend.thumbnail_key(34), backend.thumbnail_key(35))

        assert operator.objects["thumbs/dest.webp"] == b"data"

    def test_rejects_sftp_atomic_move(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.SFTP), operator=_MemoryOperator()
        )

        with pytest.raises(StorageConfigurationError, match="atomic_move"):
            backend.move(backend.thumbnail_key(36), backend.thumbnail_key(37))

    def test_rejects_sftp_without_exclusive_creation(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.SFTP), operator=SimpleNamespace()
        )

        with pytest.raises(StorageConfigurationError, match="exclusive_create"):
            backend.create_bytes(b"payload", backend.thumbnail_key(38))

    def test_marks_setup_failed_when_the_first_probe_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        monkeypatch.setattr(
            backend,
            "create_stream",
            Mock(side_effect=OSError("create failed")),
        )

        with pytest.raises(OSError, match="create failed"):
            backend.ensure_setup()
        assert backend.probe_diagnostics["conditional_create"] is False

    def test_marks_setup_failed_when_the_second_probe_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        monkeypatch.setattr(
            backend,
            "create_stream",
            Mock(side_effect=[SimpleNamespace(), OSError("second failed")]),
        )

        with pytest.raises(OSError, match="second failed"):
            backend.ensure_setup()
        assert backend.probe_diagnostics["conditional_create"] is False

    def test_marks_setup_failed_when_probe_read_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        monkeypatch.setattr(
            backend,
            "create_stream",
            Mock(side_effect=[SimpleNamespace(), SimpleNamespace()]),
        )
        monkeypatch.setattr(
            backend, "read_bytes", Mock(side_effect=OSError("read failed"))
        )

        with pytest.raises(OSError, match="read failed"):
            backend.ensure_setup()
        assert backend.probe_diagnostics["conditional_create"] is False

    def test_rejects_an_unlisted_setup_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        probe_key: list[str] = []
        monkeypatch.setattr(
            backend,
            "create_stream",
            Mock(side_effect=[SimpleNamespace(), StorageCollisionError("collision")]),
        )
        monkeypatch.setattr(
            backend, "read_bytes", lambda key: (probe_key.append(key), b"first")[1]
        )
        monkeypatch.setattr(backend, "stat_size", lambda _key: 5)
        monkeypatch.setattr(backend, "walk_keys", lambda: iter(()))

        with pytest.raises(StorageConfigurationError, match="unproven"):
            backend.ensure_setup()

    def test_rejects_a_size_mismatch_after_a_probe_collision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        probe_key: list[str] = []
        monkeypatch.setattr(
            backend,
            "create_stream",
            Mock(side_effect=[SimpleNamespace(), StorageCollisionError("collision")]),
        )
        monkeypatch.setattr(
            backend, "read_bytes", lambda key: (probe_key.append(key), b"first")[1]
        )
        monkeypatch.setattr(backend, "stat_size", lambda _key: 1)
        monkeypatch.setattr(backend, "walk_keys", lambda: iter(probe_key))

        with pytest.raises(StorageConfigurationError, match="unproven"):
            backend.ensure_setup()

    def test_enters_read_only_mode_when_probe_bytes_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        first = b"printstash-conditional-create-proof"
        probe_key: list[str] = []
        monkeypatch.setattr(
            backend,
            "create_stream",
            Mock(side_effect=[SimpleNamespace(), SimpleNamespace()]),
        )
        monkeypatch.setattr(
            backend,
            "read_bytes",
            lambda key: (probe_key.append(key), b"x" * len(first))[1],
        )
        monkeypatch.setattr(backend, "stat_size", lambda _key: len(first))
        monkeypatch.setattr(backend, "walk_keys", lambda: iter(probe_key))

        backend.ensure_setup()

        assert backend._read_only is True
        assert backend.probe_diagnostics["read_only"] is True

    def test_enters_read_only_mode_when_probe_size_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )
        probe_key: list[str] = []
        monkeypatch.setattr(
            backend,
            "create_stream",
            Mock(side_effect=[SimpleNamespace(), SimpleNamespace()]),
        )
        monkeypatch.setattr(
            backend, "read_bytes", lambda key: (probe_key.append(key), b"x")[1]
        )
        monkeypatch.setattr(backend, "stat_size", lambda _key: 1)
        monkeypatch.setattr(backend, "walk_keys", lambda: iter(probe_key))

        backend.ensure_setup()

        assert backend._read_only is True

    def test_accepts_a_conditional_setup_probe(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        backend.ensure_setup()

        assert backend.capabilities.conditional_create is True
        assert backend.probe_diagnostics["destructive_access"] is True

    def test_provisioning_requires_sftp(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(), operator=_MemoryOperator()
        )

        with pytest.raises(StorageConfigurationError, match="provisioning_unsupported"):
            backend.provision_root()

    def test_provisioning_requires_operator_support(self) -> None:
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.SFTP), operator=SimpleNamespace()
        )

        with pytest.raises(StorageConfigurationError, match="provisioning_unavailable"):
            backend.provision_root()

    def test_provisions_an_sftp_root(self) -> None:
        provision = Mock()
        check = Mock()
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.SFTP),
            operator=SimpleNamespace(provision_root=provision, check=check),
        )

        backend.provision_root()

        provision.assert_called_once_with()
        check.assert_called_once_with()

    def test_verifies_destructive_access_for_sftp(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.SFTP), operator=operator
        )

        backend.verify_destructive_access([])

        assert operator.objects == {}

    def test_verifies_destructive_access_for_webdav(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)

        backend.verify_destructive_access([])

        assert operator.objects == {}

    def test_wraps_destructive_access_failure(self) -> None:
        def fail(*_args: object) -> None:
            raise OSError("remote unavailable")

        backend = storage_opendal.OpenDALStorageBackend(
            _spec(TransportKind.SFTP),
            operator=SimpleNamespace(write_exclusive=fail, delete=fail),
        )

        with pytest.raises(StorageConfigurationError, match="destructive_access"):
            backend.verify_destructive_access([])

    def test_lists_only_the_requested_prefix(self) -> None:
        operator = _MemoryOperator()
        operator.objects["folder/object"] = b"data"
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)

        assert backend.list_prefix("vault/data/folder") == ["vault/data/folder/object"]

    def test_usage_ignores_a_missing_scanned_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _MemoryOperator()
        operator.objects["folder/object"] = b"data"
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        monkeypatch.setattr(
            operator,
            "scan",
            lambda _prefix: [
                SimpleNamespace(path="folder/object"),
                SimpleNamespace(path="folder/ghost"),
            ],
        )

        assert backend.usage("vault/data/folder") == {"bytes": 4, "objects": 1}
