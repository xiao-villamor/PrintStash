"""Adapter edge cases that real-provider contracts cannot trigger on cue."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from app.db.models import ExternalLibrary, LibrarySourceKind, StorageConnection
from app.services import library_source
from app.services.library_source import (
    LibrarySourceError,
    OpenDalLibrarySource,
    S3LibrarySource,
)
from app.services.storage_backend import StorageConfigurationError, StorageObjectInfo
from app.services.storage_opendal import SourceDirectoryEntry
from tests.factories import detached_file


class _PagedClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def list_objects_v2(self, **request):
        self.requests.append(request)
        if request.get("ContinuationToken") == "next-page":
            return {
                "Contents": [{"Key": "models/b.gcode", "Size": 2}],
                "IsTruncated": False,
            }
        return {
            "Contents": [
                {"Key": "models/a.stl", "Size": 1},
                {"Key": "models/folder/", "Size": 0},
            ],
            "IsTruncated": True,
            "NextContinuationToken": "next-page",
        }


class _ChangingClient:
    def get_object(self, **_request):
        return {
            "Body": io.BytesIO(b"original"),
            "ContentLength": 8,
            "ETag": '"etag-before"',
            "VersionId": "version-before",
        }

    def head_object(self, **request):
        assert request["VersionId"] == "version-before"
        return {
            "ContentLength": 8,
            "ETag": '"etag-after"',
            "VersionId": "version-before",
        }


class _DeniedClient:
    def get_object(self, **_request):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "GetObject",
        )


class _RootedClient:
    def __init__(self) -> None:
        self.list_request = None

    def list_objects_v2(self, **request):
        self.list_request = request
        return {
            "Contents": [{"Key": "library-root/models/a.stl", "Size": 1}],
            "IsTruncated": False,
        }


class _DirectoryBackend:
    def list_source_directory(self, relative: str, *, max_entries: int):
        assert max_entries == 10_000
        return {
            "": [
                SourceDirectoryEntry("models", 0, True),
                SourceDirectoryEntry("z.gcode", 3, False),
            ],
            "models": [
                SourceDirectoryEntry("models/a.stl", 1, False),
                SourceDirectoryEntry("models/b.stl", 2, False),
            ],
        }[relative]


class _StableClient:
    def __init__(self, payload: bytes = b"stable") -> None:
        self.payload = payload
        self.closed = False

    def get_object(self, **_request):
        body = io.BytesIO(self.payload)
        original_close = body.close

        def close() -> None:
            self.closed = True
            original_close()

        body.close = close  # type: ignore[method-assign]
        return {"Body": body, "ETag": '"stable"', "VersionId": "v1"}

    def head_object(self, **request):
        assert request["VersionId"] == "v1"
        return {
            "ContentLength": len(self.payload),
            "ETag": '"stable"',
            "VersionId": "v1",
        }


class _MissingClient:
    def get_object(self, **_request):
        raise ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
            "GetObject",
        )


class _StableDirectoryBackend:
    def __init__(self, *, changed: bool = False, missing: bool = False) -> None:
        self.changed = changed
        self.missing = missing

    def source_key(self, key: str) -> str:
        return f"root/{key}"

    def object_info(self, _key: str):
        if self.missing:
            return None
        if self.changed:
            self.changed = False
            return StorageObjectInfo(size=3, etag="after")
        return StorageObjectInfo(size=3, etag="before")

    def stream_chunks(self, _key: str):
        yield b"abc"


class TestS3LibrarySourceAdapter:
    def test_constructor_builds_a_sigv4_client_with_explicit_addressing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def client(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("boto3.client", client)

        source = S3LibrarySource(
            {
                "bucket": "library",
                "region": "us-east-1",
                "endpoint_url": "https://minio.example.test",
                "addressing_style": "path",
                "access_key": "access",
                "secret_key": "secret",
            }
        )

        assert source.client is not None
        assert captured["service_name"] == "s3"
        assert captured["endpoint_url"] == "https://minio.example.test"
        assert captured["aws_access_key_id"] == "access"

    def test_provider_cursor_survives_prefixed_pagination(self) -> None:
        client = _PagedClient()
        source = S3LibrarySource({"bucket": "library"}, client=client)

        first = source.list_page("models", cursor=None, limit=1000)
        second = source.list_page("models", cursor=first.next_cursor, limit=1000)

        assert [entry.key for entry in first.entries] == ["models/a.stl"]
        assert first.complete is False
        assert second.complete is True
        assert [entry.key for entry in second.entries] == ["models/b.gcode"]
        assert client.requests == [
            {"Bucket": "library", "Prefix": "models", "MaxKeys": 1000},
            {
                "Bucket": "library",
                "Prefix": "models",
                "MaxKeys": 1000,
                "ContinuationToken": "next-page",
            },
        ]

    def test_materialization_rejects_an_object_that_changes_mid_read(self) -> None:
        source = S3LibrarySource({"bucket": "library"}, client=_ChangingClient())

        with pytest.raises(LibrarySourceError, match="library_source_changed"):
            with source.materialize("models/a.stl"):
                pass

    def test_access_denied_is_not_misreported_as_a_missing_object(self) -> None:
        source = S3LibrarySource({"bucket": "library"}, client=_DeniedClient())

        with pytest.raises(LibrarySourceError, match="library_source_read_failed"):
            with source.materialize("models/private.stl"):
                pass

    def test_connection_root_is_applied_without_leaking_into_logical_keys(self) -> None:
        client = _RootedClient()
        source = S3LibrarySource(
            {"bucket": "library", "root": "library-root"}, client=client
        )

        page = source.list_page("models", cursor=None, limit=1000)

        assert client.list_request["Prefix"] == "library-root/models"
        assert [entry.key for entry in page.entries] == ["models/a.stl"]

    def test_invalid_s3_adapter_inputs_are_rejected(self) -> None:
        source = S3LibrarySource({"bucket": "library", "root": "root"}, client=object())

        with pytest.raises(LibrarySourceError, match="library_source_key_invalid"):
            source._provider_key("../escape.stl")  # noqa: SLF001
        with pytest.raises(LibrarySourceError, match="library_source_key_outside_root"):
            source._logical_key("another/a.stl")  # noqa: SLF001
        with pytest.raises(ValueError, match="positive"):
            source.list_page("", cursor=None, limit=0)

        class MissingCursor:
            def list_objects_v2(self, **_request):
                return {"Contents": [], "IsTruncated": True}

        with pytest.raises(LibrarySourceError, match="library_source_cursor_missing"):
            S3LibrarySource({"bucket": "library"}, client=MissingCursor()).list_page(
                "", cursor=None, limit=1
            )

    def test_list_provider_failure_is_normalized(self) -> None:
        class FailedList:
            def list_objects_v2(self, **_request):
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                    "ListObjectsV2",
                )

        with pytest.raises(LibrarySourceError, match="library_source_list_failed"):
            S3LibrarySource({"bucket": "library"}, client=FailedList()).list_page(
                "", cursor=None, limit=1
            )

    def test_stable_versioned_object_materializes_then_is_removed(self) -> None:
        client = _StableClient()
        source = S3LibrarySource({"bucket": "library"}, client=client)

        with source.materialize("models/a.stl") as path:
            assert path.read_bytes() == b"stable"
            materialized = path

        assert not materialized.exists()
        assert client.closed is True

    def test_download_bandwidth_limit_paces_large_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(library_source.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(library_source.time, "sleep", sleeps.append)
        source = S3LibrarySource(
            {"bucket": "library"},
            client=_StableClient(b"paced"),
            max_bytes_per_second=2,
        )

        with source.materialize("paced.stl") as path:
            assert path.read_bytes() == b"paced"

        assert sleeps == [2.5]

    def test_head_failure_is_treated_as_an_unstable_read(self) -> None:
        class FailedHead(_StableClient):
            def head_object(self, **_request):
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                    "HeadObject",
                )

        with pytest.raises(LibrarySourceError, match="library_source_changed"):
            with S3LibrarySource(
                {"bucket": "library"}, client=FailedHead()
            ).materialize("a.stl"):
                pass

    def test_missing_or_invalid_object_body_fails_closed(self) -> None:
        with pytest.raises(LibrarySourceError, match="library_source_missing"):
            with S3LibrarySource(
                {"bucket": "library"}, client=_MissingClient()
            ).materialize("missing.stl"):
                pass

        class InvalidBody:
            def get_object(self, **_request):
                return {"Body": object()}

        with pytest.raises(LibrarySourceError, match="library_source_read_failed"):
            with S3LibrarySource(
                {"bucket": "library"}, client=InvalidBody()
            ).materialize("invalid.stl"):
                pass

    def test_constructor_requires_a_bucket(self) -> None:
        with pytest.raises(LibrarySourceError, match="storage_connection_invalid"):
            S3LibrarySource({}, client=object())


class TestOpenDalLibrarySourceAdapter:
    def test_depth_first_cursor_pages_without_a_recursive_full_listing(self) -> None:
        source = OpenDalLibrarySource(_DirectoryBackend())  # type: ignore[arg-type]

        first = source.list_page("", cursor=None, limit=1)
        second = source.list_page("", cursor=first.next_cursor, limit=1)
        third = source.list_page("", cursor=second.next_cursor, limit=1)

        assert [entry.key for entry in first.entries] == ["models/a.stl"]
        assert [entry.key for entry in second.entries] == ["models/b.stl"]
        assert [entry.key for entry in third.entries] == ["z.gcode"]
        assert first.complete is False
        assert second.complete is False
        assert third.complete is False
        final = source.list_page("", cursor=third.next_cursor, limit=1)
        assert final.complete is True
        assert final.entries == ()

    def test_invalid_opendal_inputs_are_rejected(self) -> None:
        source = OpenDalLibrarySource(_DirectoryBackend())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="positive"):
            source.list_page("", cursor=None, limit=0)
        with pytest.raises(LibrarySourceError, match="library_source_cursor_invalid"):
            source.list_page("", cursor="not-json", limit=1)
        assert source.list_page("", cursor="[]", limit=1).complete is True

        class FailedBackend:
            def list_source_directory(self, *_args, **_kwargs):
                raise StorageConfigurationError("provider_unavailable")

        with pytest.raises(LibrarySourceError, match="provider_unavailable"):
            OpenDalLibrarySource(FailedBackend()).list_page(  # type: ignore[arg-type]
                "", cursor=None, limit=1
            )

    def test_metadata_limit_paces_each_directory_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(library_source.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(library_source.time, "sleep", sleeps.append)
        source = OpenDalLibrarySource(  # type: ignore[arg-type]
            _DirectoryBackend(), max_metadata_ops_per_second=4
        )

        page = source.list_page("", cursor=None, limit=1)

        assert page.entries[0].key == "models/a.stl"
        assert sleeps == [0.25, 0.5]

    def test_stable_materialization_cleans_the_temporary_copy(self) -> None:
        backend = _StableDirectoryBackend()
        source = OpenDalLibrarySource(backend)  # type: ignore[arg-type]

        with source.materialize("models/a.stl") as path:
            assert path.read_bytes() == b"abc"
            materialized = path

        assert not materialized.exists()

    def test_materialization_rejects_missing_or_changed_content(self) -> None:
        with pytest.raises(LibrarySourceError, match="library_source_missing"):
            with OpenDalLibrarySource(  # type: ignore[arg-type]
                _StableDirectoryBackend(missing=True)
            ).materialize("missing.stl"):
                pass

        class ChangedBackend(_StableDirectoryBackend):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            def object_info(self, _key: str):
                self.calls += 1
                return StorageObjectInfo(
                    size=3 if self.calls == 1 else 4,
                    etag="before" if self.calls == 1 else "after",
                )

        with pytest.raises(LibrarySourceError, match="library_source_changed"):
            with OpenDalLibrarySource(ChangedBackend()).materialize(  # type: ignore[arg-type]
                "changed.stl"
            ):
                pass


class TestConnectionResolution:
    def _connection(
        self,
        kind: LibrarySourceKind,
        config: dict[str, object],
        secrets: dict[str, str],
        *,
        enabled: bool = True,
    ) -> StorageConnection:
        return StorageConnection(
            id=7,
            name="NAS",
            kind=kind,
            config_json=json.dumps(config),
            secret_json=json.dumps(secrets),
            enabled=enabled,
        )

    def test_unsafe_connection_profiles_are_rejected(self) -> None:
        disabled = self._connection(
            LibrarySourceKind.S3, {"bucket": "models"}, {}, enabled=False
        )
        with pytest.raises(LibrarySourceError, match="storage_connection_disabled"):
            library_source.source_from_connection(disabled)

        invalid = self._connection(LibrarySourceKind.S3, {}, {})
        invalid.config_json = "[]"
        with pytest.raises(LibrarySourceError, match="storage_connection_invalid"):
            library_source.source_from_connection(invalid)

        mounted = self._connection(LibrarySourceKind.MOUNTED, {}, {})
        with pytest.raises(
            LibrarySourceError, match="storage_connection_kind_invalid"
        ):
            library_source.source_from_connection(mounted)

        malformed = self._connection(LibrarySourceKind.S3, {"bucket": ""}, {})
        with pytest.raises(LibrarySourceError, match="storage_connection_invalid"):
            library_source.source_from_connection(malformed)

    @pytest.mark.parametrize("kind", [LibrarySourceKind.WEBDAV, LibrarySourceKind.SFTP])
    def test_opendal_connections_resolve_supported_transports(
        self,
        kind: LibrarySourceKind,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = (
            {
                "provider": "webdav",
                "endpoint_url": "https://nas.example.test/dav",
                "username": "reader",
                "root": "models",
            }
            if kind == LibrarySourceKind.WEBDAV
            else {
                "host": "nas.example.test",
                "username": "reader",
                "host_key": "nas.example.test ssh-ed25519 AAAATEST",
                "root": "models",
            }
        )
        secrets = {"password": "secret"}
        captured: list[object] = []
        monkeypatch.setattr(
            library_source,
            "OpenDALStorageBackend",
            lambda spec: captured.append(spec) or SimpleNamespace(),
        )

        source = library_source.source_from_connection(
            self._connection(kind, config, secrets), scan_limits=True
        )

        assert isinstance(source, OpenDalLibrarySource)
        assert source.max_metadata_ops_per_second == 4
        assert source.max_bytes_per_second == 8 * 1024 * 1024
        assert len(captured) == 1

    def test_s3_connection_applies_scan_bandwidth_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created: list[tuple[object, object]] = []

        class FakeS3:
            def __init__(self, options, *, max_bytes_per_second=None):
                created.append((options, max_bytes_per_second))

        monkeypatch.setattr(library_source, "S3LibrarySource", FakeS3)
        source = library_source.source_from_connection(
            self._connection(
                LibrarySourceKind.S3,
                {"bucket": "models", "root": "library"},
                {"access_key": "access", "secret_key": "secret"},
            ),
            scan_limits=True,
        )

        assert isinstance(source, FakeS3)
        assert created[0][1] == 8 * 1024 * 1024

    def test_source_guards_precede_any_remote_read(self) -> None:
        mounted = ExternalLibrary(
            id=1,
            name="Mounted",
            root_path="/mnt/models",
            source_kind=LibrarySourceKind.MOUNTED,
        )
        with pytest.raises(LibrarySourceError, match="library_source_is_mounted"):
            library_source.source_for_library(mounted)

        missing_source = detached_file(
            id=1,
            model_id=1,
            path="source://missing",
            original_filename="missing.stl",
            file_type="STL",
            size_bytes=1,
            sha256="0" * 64,
            is_external=True,
        )
        with pytest.raises(LibrarySourceError, match="remote_file_source_missing"):
            library_source.source_for_file(missing_source)

    def test_library_resolution_requires_a_persisted_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        library = ExternalLibrary(
            id=2,
            name="Remote",
            root_path="source://2",
            source_kind=LibrarySourceKind.S3,
            connection_id=7,
        )
        factory = _SessionFactory({})
        monkeypatch.setattr(library_source, "get_session_factory", lambda: factory)

        with pytest.raises(LibrarySourceError, match="storage_connection_missing"):
            library_source.source_for_library(library)

    def test_library_resolution_decrypts_before_the_session_closes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        library = ExternalLibrary(
            id=2,
            name="Remote",
            root_path="source://2",
            source_kind=LibrarySourceKind.S3,
            connection_id=7,
        )
        connection = self._connection(
            LibrarySourceKind.S3,
            {"bucket": "models"},
            {"access_key": "access", "secret_key": "secret"},
        )
        marker = object()
        monkeypatch.setattr(
            library_source, "get_session_factory", lambda: _SessionFactory({7: connection})
        )
        monkeypatch.setattr(
            library_source,
            "source_from_connection",
            lambda row, *, scan_limits=False: marker
            if row is connection and scan_limits
            else None,
        )

        assert library_source.source_for_library(library) is marker

    def test_file_resolution_enforces_graph_before_returning_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        file = detached_file(
            id=3,
            model_id=1,
            path="source://2/models/a.stl",
            original_filename="a.stl",
            file_type="STL",
            size_bytes=3,
            sha256="0" * 64,
            is_external=True,
            external_library_id=2,
            source_key="models/a.stl",
        )
        monkeypatch.setattr(
            library_source, "get_session_factory", lambda: _SessionFactory({})
        )
        with pytest.raises(LibrarySourceError, match="remote_file_source_missing"):
            library_source.source_for_file(file)

        library = ExternalLibrary(
            id=2,
            name="Remote",
            root_path="source://2",
            source_kind=LibrarySourceKind.S3,
            connection_id=7,
        )
        monkeypatch.setattr(
            library_source,
            "get_session_factory",
            lambda: _TypedSessionFactory({(ExternalLibrary, 2): library}),
        )
        with pytest.raises(LibrarySourceError, match="storage_connection_missing"):
            library_source.source_for_file(file)

        connection = self._connection(
            LibrarySourceKind.S3,
            {"bucket": "models"},
            {"access_key": "access", "secret_key": "secret"},
        )
        marker = object()
        monkeypatch.setattr(
            library_source,
            "get_session_factory",
            lambda: _TypedSessionFactory(
                {
                    (ExternalLibrary, 2): library,
                    (StorageConnection, 7): connection,
                }
            ),
        )
        monkeypatch.setattr(
            library_source, "source_from_connection", lambda row: marker
        )

        assert library_source.source_for_file(file) == (marker, "models/a.stl")


class TestSourceHelpers:
    def test_source_helpers_normalize_or_reject_values(self) -> None:
        assert library_source._json_object('{"answer": 42}') == {"answer": 42}  # noqa: SLF001
        with pytest.raises(LibrarySourceError, match="storage_connection_invalid"):
            library_source._json_object("{")  # noqa: SLF001
        with pytest.raises(LibrarySourceError, match="library_source_key_invalid"):
            library_source._safe_key("")  # noqa: SLF001

        assert library_source.timestamp(None) == 0.0
        naive = datetime(2026, 1, 1)
        aware = naive.replace(tzinfo=timezone.utc)
        assert library_source.timestamp(naive) == library_source.timestamp(aware)


class _SessionFactory:
    def __init__(self, rows: dict[int, object]) -> None:
        self.rows = rows

    @contextmanager
    def scoped_session(self):
        rows = self.rows

        class Session:
            def get(self, _model, identifier):
                return rows.get(identifier)

        yield Session()


class _TypedSessionFactory:
    def __init__(self, rows: dict[tuple[type, int], object]) -> None:
        self.rows = rows

    @contextmanager
    def scoped_session(self):
        rows = self.rows

        class Session:
            def get(self, model, identifier):
                return rows.get((model, identifier))

        yield Session()
