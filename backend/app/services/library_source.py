"""Read-only, bounded content seam for mounted and remote library sources."""

from __future__ import annotations

import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Protocol

from app.db.models import (
    ExternalLibrary,
    File,
    LibrarySourceKind,
    StorageConnection,
    StorageConnectionPurpose,
)
from app.db.session import get_session_factory
from app.services.remote_deadline import paced_sleep, remote_budget
from app.services.remote_io import RemoteIO
from app.services.remote_io_adapters import remote_io_for
from app.services.storage_backend import StorageConfigurationError
from app.services.storage_connections import (
    StorageConnectionConfigError,
    load_connection_config,
)
from app.services.storage_providers import resolve_transport


class LibrarySourceError(RuntimeError):
    """A remote source could not provide a complete, stable observation."""

    discovery_cursor: str | None = None


@dataclass(frozen=True)
class SourceEntry:
    key: str
    size: int
    modified_at: datetime | None = None
    etag: str | None = None
    version_id: str | None = None


@dataclass(frozen=True)
class SourceContent:
    """Temporary bytes and the metadata verified for that exact read."""

    path: Path
    entry: SourceEntry


def _require_observation(expected: SourceEntry | None, actual: SourceEntry) -> None:
    if expected is not None and (
        expected.key != actual.key
        or expected.size != actual.size
        or (
            expected.modified_at is not None
            and timestamp(expected.modified_at) != timestamp(actual.modified_at)
        )
        or (expected.etag is not None and expected.etag != actual.etag)
        or (
            expected.version_id is not None and expected.version_id != actual.version_id
        )
    ):
        raise LibrarySourceError("library_source_changed")


@dataclass(frozen=True)
class SourcePage:
    entries: tuple[SourceEntry, ...]
    next_cursor: str | None
    complete: bool
    metadata_ops: int
    entry_cursors: tuple[str, ...] = ()
    inventory_id: str | None = None


class LibrarySource(Protocol):
    """The only interface discovery and ArtifactContent use for remote bytes."""

    def probe(self) -> int: ...

    def list_page(
        self, prefix: str, *, cursor: str | None, limit: int
    ) -> SourcePage: ...

    @contextmanager
    def materialize(
        self, key: str, *, expected: SourceEntry | None = None
    ) -> Iterator[SourceContent]: ...


def _safe_key(value: str) -> str:
    stripped = value.strip("/")
    if not stripped:
        raise LibrarySourceError("library_source_key_invalid")
    key = PurePosixPath(stripped).as_posix()
    if not key or any(part in {"", ".", ".."} for part in PurePosixPath(key).parts):
        raise LibrarySourceError("library_source_key_invalid")
    return key


class RemoteLibrarySource:
    def __init__(
        self,
        backend: RemoteIO,
        *,
        max_metadata_ops_per_second: int | None = None,
        max_bytes_per_second: int | None = None,
    ) -> None:
        self.backend = backend
        self.max_metadata_ops_per_second = max_metadata_ops_per_second
        self.max_bytes_per_second = max_bytes_per_second

    def _pace_metadata(self, started: float, operations: int) -> None:
        if not self.max_metadata_ops_per_second:
            return
        target_elapsed = operations / self.max_metadata_ops_per_second
        remaining = target_elapsed - (time.monotonic() - started)
        if remaining > 0:
            paced_sleep(remaining)

    def probe(self) -> int:
        with remote_budget(deadline=time.monotonic() + 30):
            with self.backend.iter_directory("") as entries:
                return int(next(entries, None) is not None)

    def list_page(self, prefix: str, *, cursor: str | None, limit: int) -> SourcePage:
        from app.services.remote_discovery import inventory_page

        started = time.monotonic()
        with remote_budget(deadline=started + 900):
            return inventory_page(
                self.backend,
                prefix,
                cursor=cursor,
                limit=limit,
                pace=lambda operations: self._pace_metadata(started, operations),
            )

    @contextmanager
    def materialize(
        self, key: str, *, expected: SourceEntry | None = None
    ) -> Iterator[SourceContent]:
        with remote_budget(deadline=time.monotonic() + 900):
            safe_key = _safe_key(key)
            provider_key = self.backend.source_key(safe_key)
            before = self.backend.object_info(provider_key)
            if before is None:
                raise LibrarySourceError("library_source_missing")
            observation = SourceEntry(
                safe_key,
                before.size,
                before.modified_at,
                before.etag,
                before.version_id,
            )
            _require_observation(expected, observation)
            fd, raw = tempfile.mkstemp(suffix=Path(safe_key).suffix)
            path = Path(raw)
            written = 0
            started = time.monotonic()
            try:
                with (
                    open(fd, "wb", closefd=True) as output,
                    self.backend.open_reader(provider_key, expected=before) as reader,
                ):
                    while chunk := reader.read(
                        min(1024 * 1024, before.size - written + 1)
                    ):
                        written += len(chunk)
                        if written > before.size:
                            raise LibrarySourceError("library_source_size_mismatch")
                        output.write(chunk)
                        if self.max_bytes_per_second:
                            target_elapsed = written / self.max_bytes_per_second
                            remaining = target_elapsed - (time.monotonic() - started)
                            if remaining > 0:
                                paced_sleep(remaining)
                if written != before.size:
                    raise LibrarySourceError("library_source_size_mismatch")
                after = self.backend.object_info(provider_key)
                if (
                    after is None
                    or after.size != before.size
                    or after.etag != before.etag
                    or after.version_id != before.version_id
                    or timestamp(after.modified_at) != timestamp(before.modified_at)
                ):
                    raise LibrarySourceError("library_source_changed")
                yield SourceContent(path, observation)
            finally:
                path.unlink(missing_ok=True)


def source_from_connection(
    connection: StorageConnection, *, scan_limits: bool = False
) -> LibrarySource:
    if not connection.enabled:
        raise LibrarySourceError("storage_connection_disabled")
    if not connection.purpose.allows(StorageConnectionPurpose.LIBRARY):
        raise LibrarySourceError("storage_connection_not_library")
    if connection.kind == LibrarySourceKind.MOUNTED:
        raise LibrarySourceError("storage_connection_kind_invalid")
    try:
        parsed = load_connection_config(connection)
        return RemoteLibrarySource(
            remote_io_for(resolve_transport(parsed)),
            max_metadata_ops_per_second=4 if scan_limits else None,
            max_bytes_per_second=8 * 1024 * 1024 if scan_limits else None,
        )
    except (StorageConnectionConfigError, StorageConfigurationError) as exc:
        raise LibrarySourceError("storage_connection_invalid") from exc


def source_for_library(library: ExternalLibrary) -> LibrarySource:
    if (
        library.source_kind == LibrarySourceKind.MOUNTED
        or library.connection_id is None
    ):
        raise LibrarySourceError("library_source_is_mounted")
    with get_session_factory().scoped_session() as session:
        connection = session.get(StorageConnection, library.connection_id)
        if connection is None:
            raise LibrarySourceError("storage_connection_missing")
        # Materialize the decrypted values before the session closes.
        connection.config_json = str(connection.config_json)
        connection.secret_json = str(connection.secret_json)
        return source_from_connection(connection, scan_limits=True)


def source_for_file(file: File) -> tuple[LibrarySource, str]:
    if file.external_library_id is None or not file.source_key:
        raise LibrarySourceError("remote_file_source_missing")
    with get_session_factory().scoped_session() as session:
        library = session.get(ExternalLibrary, file.external_library_id)
        if library is None:
            raise LibrarySourceError("remote_file_source_missing")
        connection = (
            session.get(StorageConnection, library.connection_id)
            if library.connection_id is not None
            else None
        )
        if connection is None:
            raise LibrarySourceError("storage_connection_missing")
        connection.config_json = str(connection.config_json)
        connection.secret_json = str(connection.secret_json)
        return source_from_connection(connection), file.source_key


def timestamp(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()
