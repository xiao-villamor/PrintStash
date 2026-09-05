"""Adapter edge cases that real-provider contracts cannot trigger on cue."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db.models import (
    LibrarySourceKind,
    StorageConnection,
)
from app.services import library_source
from app.services.library_source import (
    LibrarySourceError,
    RemoteLibrarySource,
    SourceEntry,
)
from app.services.remote_io import RemoteEntry as SourceDirectoryEntry
from app.services.storage_backend import StorageConfigurationError, StorageObjectInfo


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


@pytest.mark.usefixtures("db_session")
class TestInventoryListing:
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

    def test_durable_cursor_pages_all_directory_entries(self) -> None:
        source = RemoteLibrarySource(_DirectoryBackend())  # type: ignore[arg-type]

        first = source.list_page("", cursor=None, limit=1)
        second = source.list_page("", cursor=first.next_cursor, limit=1)
        third = source.list_page("", cursor=second.next_cursor, limit=1)

        assert [entry.key for entry in first.entries] == ["z.gcode"]
        assert [entry.key for entry in second.entries] == ["models/a.stl"]
        assert [entry.key for entry in third.entries] == ["models/b.stl"]
        assert first.complete is False
        assert second.complete is False
        assert third.complete is True
        assert third.next_cursor is None

    def test_invalid_opendal_inputs_are_rejected(self) -> None:
        source = RemoteLibrarySource(_DirectoryBackend())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="between 1 and 1000"):
            source.list_page("", cursor=None, limit=0)
        with pytest.raises(LibrarySourceError, match="library_source_cursor_invalid"):
            source.list_page("", cursor="not-json", limit=1)
        with pytest.raises(LibrarySourceError, match="library_source_cursor_invalid"):
            source.list_page("", cursor="[]", limit=1)

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
        monkeypatch.setattr(library_source, "paced_sleep", sleeps.append)
        source = RemoteLibrarySource(  # type: ignore[arg-type]
            _DirectoryBackend(), max_metadata_ops_per_second=4
        )

        page = source.list_page("", cursor=None, limit=1)

        assert page.entries[0].key == "z.gcode"
        assert sleeps == [0.25, 0.5]


@pytest.mark.usefixtures("db_session")
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
        monkeypatch.setattr(library_source, "paced_sleep", sleeps.append)
        source = RemoteLibrarySource(_StableDirectoryBackend(), max_bytes_per_second=2)
        with source.materialize("paced.stl") as content:
            assert content.path.read_bytes() == b"abc"
        assert sleeps == [1.5]
