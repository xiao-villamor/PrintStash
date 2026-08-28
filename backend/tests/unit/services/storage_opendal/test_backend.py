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
            is True
        )
        assert not operator.exists("thumbs/25.webp")

    def test_reclaims_when_object_evidence_matches(self) -> None:
        operator = _MemoryOperator()
        backend = storage_opendal.OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(26)
        backend.create_bytes(b"payload", key)

        assert (
            backend.reclaim_unverified(
                key, expected_size=7, expected_etag="etag:thumbs/26.webp"
            )
            is True
        )
        assert not operator.exists("thumbs/26.webp")
