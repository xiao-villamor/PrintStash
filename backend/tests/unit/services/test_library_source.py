"""Adapter edge cases that real-provider contracts cannot trigger on cue."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.models import (
    ExternalLibrary,
    LibrarySourceKind,
    StorageConnection,
    StorageConnectionPurpose,
)
from app.services import library_source
from app.services.library_source import (
    LibrarySourceError,
    RemoteLibrarySource,
    SourceEntry,
)
from app.services.remote_io import RemoteEntry as SourceDirectoryEntry
from app.services.storage_backend import StorageConfigurationError, StorageObjectInfo
from tests.factories import detached_file


class _DirectoryBackend:
    @contextmanager
    def iter_directory(self, relative):
        yield iter(self.list_source_directory(relative, max_entries=10_000))

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


class _StableDirectoryBackend:
    @contextmanager
    def open_reader(self, key, *, expected=None):
        from app.services.remote_io_adapters import _ChunkReader

        with io.BufferedReader(
            _ChunkReader(self.stream_chunks(key, expected=expected))
        ) as reader:
            yield reader

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

    def stream_chunks(self, _key: str, *, expected=None):
        yield b"abc"


class TestRemoteLibrarySourceAdapter:
    @pytest.mark.parametrize(
        "after",
        [
            None,
            StorageObjectInfo(size=4, etag="before"),
            StorageObjectInfo(size=3, etag="changed"),
            StorageObjectInfo(size=3, etag="before", version_id="replacement"),
        ],
        ids=["disappeared", "size-changed", "etag-changed", "version-changed"],
    )
    def test_rejects_changed_metadata_before_exposing_downloaded_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        after: StorageObjectInfo | None,
    ) -> None:
        backend = _StableDirectoryBackend()
        observations = iter([StorageObjectInfo(size=3, etag="before"), after])
        monkeypatch.setattr(backend, "object_info", lambda _key: next(observations))
        monkeypatch.setattr(library_source.tempfile, "tempdir", str(tmp_path))
        source = RemoteLibrarySource(backend)  # type: ignore[arg-type]

        with pytest.raises(LibrarySourceError, match="library_source_changed"):
            with source.materialize("models/a.stl"):
                pytest.fail("changed source content was exposed to indexing")

        assert list(tmp_path.iterdir()) == []

    def test_cleans_the_temporary_download_after_a_read_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        backend = _StableDirectoryBackend()

        def broken_stream(_key: str, **_kwargs):
            yield b"a"
            raise OSError("remote read failed")

        monkeypatch.setattr(backend, "stream_chunks", broken_stream)
        monkeypatch.setattr(library_source.tempfile, "tempdir", str(tmp_path))
        source = RemoteLibrarySource(backend)  # type: ignore[arg-type]

        with pytest.raises(
            StorageConfigurationError, match="remote_storage_read_failed"
        ):
            with source.materialize("models/a.stl"):
                pytest.fail("failed remote content was exposed to indexing")

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        "payload",
        [b"", b"ab", b"abcd"],
        ids=["empty", "short", "oversized"],
    )
    def test_rejects_a_stream_with_the_wrong_length(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        payload: bytes,
    ) -> None:
        backend = _StableDirectoryBackend()
        monkeypatch.setattr(
            backend, "stream_chunks", lambda _key, **_kwargs: iter([payload])
        )
        monkeypatch.setattr(library_source.tempfile, "tempdir", str(tmp_path))
        source = RemoteLibrarySource(backend)  # type: ignore[arg-type]

        with pytest.raises(LibrarySourceError, match="library_source_size_mismatch"):
            with source.materialize("models/a.stl"):
                pytest.fail("an incomplete source was exposed to indexing")

        assert list(tmp_path.iterdir()) == []

    def test_stops_reading_when_the_advertised_length_is_exceeded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        backend = _StableDirectoryBackend()

        def oversized_stream(_key: str, **_kwargs):
            yield b"abcd"
            pytest.fail("continued reading an oversized source")

        monkeypatch.setattr(backend, "stream_chunks", oversized_stream)
        monkeypatch.setattr(library_source.tempfile, "tempdir", str(tmp_path))
        source = RemoteLibrarySource(backend)  # type: ignore[arg-type]

        with pytest.raises(LibrarySourceError, match="library_source_size_mismatch"):
            with source.materialize("models/a.stl"):
                pytest.fail("an oversized source was exposed to indexing")

        assert list(tmp_path.iterdir()) == []

    def test_listing_preserves_nullable_remote_observations(self) -> None:
        modified = datetime(2026, 9, 1, tzinfo=timezone.utc)

        class MetadataBackend:
            @contextmanager
            def iter_directory(self, relative):
                yield iter(self.list_source_directory(relative))

            def list_source_directory(self, *_args, **_kwargs):
                return [
                    SourceDirectoryEntry("known.stl", 3, False, modified, "tag", "v1"),
                    SourceDirectoryEntry("unknown.stl", 4, False),
                ]

        page = RemoteLibrarySource(MetadataBackend()).list_page(  # type: ignore[arg-type]
            "", cursor=None, limit=1000
        )

        assert page.entries == (
            SourceEntry("known.stl", 3, modified, "tag", "v1"),
            SourceEntry("unknown.stl", 4),
        )

    @pytest.mark.parametrize(
        "marker", ["key", "size", "modified_at", "etag", "version_id"]
    )
    def test_listing_drift_prevents_any_download(self, marker: str) -> None:
        values = {
            "key": "models/a.stl",
            "size": 3,
            "modified_at": None,
            "etag": None,
            "version_id": None,
        }
        values[marker] = {
            "key": "models/b.stl",
            "size": 4,
            "modified_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "etag": "old",
            "version_id": "old",
        }[marker]

        class NeverDownload(_StableDirectoryBackend):
            def stream_chunks(self, *_args, **_kwargs):
                pytest.fail("stale discovery must not start a download")

        source = RemoteLibrarySource(NeverDownload())  # type: ignore[arg-type]
        with pytest.raises(LibrarySourceError, match="library_source_changed"):
            with source.materialize("models/a.stl", expected=SourceEntry(**values)):
                pytest.fail("stale discovery yielded content")

    @pytest.mark.parametrize(
        "drift", ["missing", "size", "modified_at", "etag", "version_id"]
    )
    def test_read_drift_removes_the_temporary_copy(
        self, drift: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        modified = datetime(2026, 9, 1, tzinfo=timezone.utc)

        class ChangingBackend(_StableDirectoryBackend):
            calls = 0

            def object_info(self, _key):
                self.calls += 1
                if self.calls == 2 and drift == "missing":
                    return None
                values = dict(size=3, modified_at=modified, etag="tag", version_id="v1")
                if self.calls == 2:
                    values[drift] = {
                        "size": 4,
                        "modified_at": None,
                        "etag": "other",
                        "version_id": "v2",
                    }[drift]
                return StorageObjectInfo(**values)

        monkeypatch.setattr(library_source.tempfile, "tempdir", str(tmp_path))
        source = RemoteLibrarySource(ChangingBackend())  # type: ignore[arg-type]
        with pytest.raises(LibrarySourceError, match="library_source_changed"):
            with source.materialize("models/a.stl"):
                pytest.fail("unstable read yielded content")
        assert list(tmp_path.iterdir()) == []

    def test_materialized_content_carries_verified_metadata(self) -> None:
        modified = datetime(2026, 9, 1, tzinfo=timezone.utc)
        info = StorageObjectInfo(
            size=3, modified_at=modified, etag="tag", version_id="v1"
        )

        class ObservedBackend(_StableDirectoryBackend):
            def object_info(self, _key):
                return info

            def stream_chunks(self, _key, *, expected):
                assert expected == info
                yield b"abc"

        source = RemoteLibrarySource(ObservedBackend())  # type: ignore[arg-type]
        with source.materialize(
            "models/a.stl", expected=SourceEntry("models/a.stl", 3)
        ) as content:
            assert content.path.read_bytes() == b"abc"
            assert content.entry == SourceEntry(
                "models/a.stl", 3, modified, "tag", "v1"
            )

    def test_depth_first_cursor_pages_without_a_recursive_full_listing(self) -> None:
        source = RemoteLibrarySource(_DirectoryBackend())  # type: ignore[arg-type]

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
        source = RemoteLibrarySource(_DirectoryBackend())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="positive"):
            source.list_page("", cursor=None, limit=0)
        with pytest.raises(LibrarySourceError, match="library_source_cursor_invalid"):
            source.list_page("", cursor="not-json", limit=1)
        assert source.list_page("", cursor="[]", limit=1).complete is True

        class FailedBackend:
            @contextmanager
            def iter_directory(self, *_args, **_kwargs):
                raise StorageConfigurationError("provider_unavailable")

        with pytest.raises(LibrarySourceError, match="provider_unavailable"):
            RemoteLibrarySource(FailedBackend()).list_page(  # type: ignore[arg-type]
                "", cursor=None, limit=1
            )

    def test_metadata_limit_paces_each_directory_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(library_source.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(library_source.time, "sleep", sleeps.append)
        source = RemoteLibrarySource(  # type: ignore[arg-type]
            _DirectoryBackend(), max_metadata_ops_per_second=4
        )

        page = source.list_page("", cursor=None, limit=1)

        assert page.entries[0].key == "models/a.stl"
        assert sleeps == [0.25, 0.5]

    def test_stable_materialization_cleans_the_temporary_copy(self) -> None:
        backend = _StableDirectoryBackend()
        source = RemoteLibrarySource(backend)  # type: ignore[arg-type]

        with source.materialize("models/a.stl") as content:
            path = content.path
            assert path.read_bytes() == b"abc"
            materialized = path

        assert not materialized.exists()

    def test_materialization_rejects_missing_or_changed_content(self) -> None:
        with pytest.raises(LibrarySourceError, match="library_source_missing"):
            with RemoteLibrarySource(  # type: ignore[arg-type]
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
            with RemoteLibrarySource(ChangedBackend()).materialize(  # type: ignore[arg-type]
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
        with pytest.raises(LibrarySourceError, match="storage_connection_kind_invalid"):
            library_source.source_from_connection(mounted)

        malformed = self._connection(LibrarySourceKind.S3, {"bucket": ""}, {})
        with pytest.raises(LibrarySourceError, match="storage_connection_invalid"):
            library_source.source_from_connection(malformed)

    @pytest.mark.parametrize(
        "kind",
        [
            LibrarySourceKind.WEBDAV,
            LibrarySourceKind.SFTP,
            LibrarySourceKind.GDRIVE,
        ],
    )
    def test_opendal_connections_resolve_supported_transports(
        self,
        kind: LibrarySourceKind,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = (
            {
                "bucket": "models",
                "root": "library",
            }
            if kind == LibrarySourceKind.S3
            else {
                "provider": "webdav",
                "endpoint_url": "https://nas.example.test/dav",
                "username": "reader",
                "root": "models",
            }
            if kind == LibrarySourceKind.WEBDAV
            else {
                "client_id": "client-id",
                "root": "models",
            }
            if kind == LibrarySourceKind.GDRIVE
            else {
                "host": "nas.example.test",
                "username": "reader",
                "host_key": "nas.example.test ssh-ed25519 AAAATEST",
                "root": "models",
            }
        )
        secrets = (
            {"access_key": "access", "secret_key": "secret"}
            if kind == LibrarySourceKind.S3
            else {"client_secret": "secret", "refresh_token": "refresh"}
            if kind == LibrarySourceKind.GDRIVE
            else {"password": "secret"}
        )
        captured: list[object] = []
        monkeypatch.setattr(
            library_source,
            "remote_io_for",
            lambda spec: captured.append(spec) or SimpleNamespace(),
        )

        source = library_source.source_from_connection(
            self._connection(kind, config, secrets), scan_limits=True
        )

        assert isinstance(source, RemoteLibrarySource)
        assert source.max_metadata_ops_per_second == 4
        assert source.max_bytes_per_second == 8 * 1024 * 1024
        assert len(captured) == 1

    def test_s3_connections_use_the_opendal_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[object] = []
        monkeypatch.setattr(
            library_source,
            "remote_io_for",
            lambda spec: captured.append(spec) or SimpleNamespace(),
        )

        source = library_source.source_from_connection(
            self._connection(
                LibrarySourceKind.S3,
                {"bucket": "models", "root": "library"},
                {"access_key": "access", "secret_key": "secret"},
            ),
            scan_limits=True,
        )

        assert isinstance(source, RemoteLibrarySource)
        assert source.max_bytes_per_second == 8 * 1024 * 1024
        assert len(captured) == 1

    def test_backup_connection_is_not_accepted_as_a_library(self) -> None:
        connection = self._connection(
            LibrarySourceKind.S3,
            {"bucket": "models", "root": "library"},
            {"access_key": "access", "secret_key": "secret"},
        )
        connection.purpose = StorageConnectionPurpose.BACKUP

        with pytest.raises(LibrarySourceError, match="storage_connection_not_library"):
            library_source.source_from_connection(connection)

    def test_shared_connection_is_accepted_as_a_library(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = self._connection(
            LibrarySourceKind.S3,
            {"bucket": "models", "root": "library"},
            {"access_key": "access", "secret_key": "secret"},
        )
        connection.purpose = StorageConnectionPurpose.BOTH
        monkeypatch.setattr(
            library_source,
            "remote_io_for",
            lambda _spec: SimpleNamespace(),
        )

        source = library_source.source_from_connection(connection)

        assert isinstance(source, RemoteLibrarySource)

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
            library_source,
            "get_session_factory",
            lambda: _SessionFactory({7: connection}),
        )
        monkeypatch.setattr(
            library_source,
            "source_from_connection",
            lambda row, *, scan_limits=False: (
                marker if row is connection and scan_limits else None
            ),
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
        with pytest.raises(LibrarySourceError, match="library_source_key_invalid"):
            library_source._safe_key("")  # noqa: SLF001

        assert library_source.timestamp(None) is None
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


class TestProductionS3Source:
    def _source(self, monkeypatch, *, failure=None):
        from app.services import remote_io_adapters

        calls = []

        class Operator:
            def capability(self):
                return SimpleNamespace(read_with_version=True)

            def exists(self, key):
                return True

            def stat(self, key):
                if failure == "metadata":
                    raise OSError("example-secret")
                return SimpleNamespace(content_length=3, etag="etag", version="v1")

            def open(self, key, mode, **options):
                calls.append((key, options))
                if failure == "read":
                    raise OSError("example-secret")
                return io.BytesIO(b"abc")

            def list(self, directory):
                if failure == "list":
                    raise OSError("example-secret")
                yield SimpleNamespace(
                    path="models/a.stl", metadata=self.stat("models/a.stl")
                )

        monkeypatch.setattr(remote_io_adapters, "_operator_for", lambda _: Operator())
        connection = StorageConnection(
            id=7,
            name="S3 source",
            kind=LibrarySourceKind.S3,
            config_json=json.dumps({"bucket": "library", "root": "library-root"}),
            secret_json=json.dumps({"access_key": "access", "secret_key": "secret"}),
        )
        return library_source.source_from_connection(connection), calls

    def test_production_factory_materializes_prefixed_versioned_content(
        self, monkeypatch
    ):
        source, calls = self._source(monkeypatch)
        page = source.list_page("models", cursor=None, limit=1000)
        assert page.entries == (
            SourceEntry("models/a.stl", 3, etag="etag", version_id="v1"),
        )
        with source.materialize(
            page.entries[0].key, expected=page.entries[0]
        ) as content:
            assert content.path.read_bytes() == b"abc"
            path = content.path
        assert not path.exists()
        assert calls == [("models/a.stl", {"version": "v1"})]

    @pytest.mark.parametrize("operation", ["list", "read", "metadata"])
    def test_production_transport_errors_do_not_expose_credentials(
        self, monkeypatch, operation
    ):
        source, _ = self._source(monkeypatch, failure=operation)
        with pytest.raises(
            (StorageConfigurationError, LibrarySourceError),
            match=f"remote_storage_{operation}_failed",
        ) as failure:
            if operation == "list":
                source.list_page("models", cursor=None, limit=1000)
            else:
                with source.materialize("models/a.stl"):
                    pytest.fail("failed remote read was published")
        assert "example-secret" not in str(failure.value)

    def test_read_bandwidth_limit_survives_transport_extraction(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(library_source.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(library_source.time, "sleep", sleeps.append)
        source = RemoteLibrarySource(_StableDirectoryBackend(), max_bytes_per_second=2)
        with source.materialize("paced.stl") as content:
            assert content.path.read_bytes() == b"abc"
        assert sleeps == [1.5]
