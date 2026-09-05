"""Read-only, bounded content seam for mounted and remote library sources."""

from __future__ import annotations

import json
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping, Protocol

from app.db.models import (
    ExternalLibrary,
    File,
    LibrarySourceKind,
    StorageConnection,
    StorageConnectionPurpose,
)
from app.db.session import get_session_factory
from app.services.storage_backend import StorageConfigurationError
from app.services.storage_connections import (
    StorageConnectionConfigError,
    load_connection_config,
)
from app.services.storage_opendal import OpenDALStorageBackend, SourceDirectoryEntry
from app.services.storage_providers import resolve_transport


class LibrarySourceError(RuntimeError):
    """A remote source could not provide a complete, stable observation."""


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


class LibrarySource(Protocol):
    """The only interface discovery and ArtifactContent use for remote bytes."""

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


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError) as exc:
        raise LibrarySourceError("storage_connection_invalid") from exc
    if not isinstance(parsed, dict):
        raise LibrarySourceError("storage_connection_invalid")
    return {str(key): item for key, item in parsed.items()}


class S3LibrarySource:
    def __init__(
        self,
        options: Mapping[str, object],
        *,
        client=None,
        max_bytes_per_second: int | None = None,
    ) -> None:
        self.bucket = str(options.get("bucket") or "")
        self.root = str(options.get("root") or "").strip("/")
        if not self.bucket:
            raise LibrarySourceError("storage_connection_invalid")
        if client is None:
            import boto3
            from botocore.config import Config as BotoConfig

            style = str(options.get("addressing_style") or "auto")
            kwargs: dict[str, object] = {
                "service_name": "s3",
                "region_name": str(options.get("region") or "auto"),
                "aws_access_key_id": str(options.get("access_key") or "") or None,
                "aws_secret_access_key": str(options.get("secret_key") or "") or None,
                "config": BotoConfig(
                    signature_version="s3v4", s3={"addressing_style": style}
                ),
            }
            endpoint = str(options.get("endpoint_url") or "")
            if endpoint:
                kwargs["endpoint_url"] = endpoint
            client = boto3.client(**kwargs)
        self.client = client
        self.max_bytes_per_second = max_bytes_per_second

    def _provider_key(self, logical_key: str) -> str:
        safe_key = _safe_key(logical_key)
        return f"{self.root}/{safe_key}" if self.root else safe_key

    def _logical_key(self, provider_key: str) -> str:
        safe_key = _safe_key(provider_key)
        if not self.root:
            return safe_key
        prefix = f"{self.root}/"
        if not safe_key.startswith(prefix):
            raise LibrarySourceError("library_source_key_outside_root")
        return _safe_key(safe_key[len(prefix) :])

    def list_page(self, prefix: str, *, cursor: str | None, limit: int) -> SourcePage:
        from botocore.exceptions import BotoCoreError, ClientError

        if limit < 1:
            raise ValueError("limit must be positive")
        logical_prefix = prefix.strip("/")
        provider_prefix = "/".join(part for part in (self.root, logical_prefix) if part)
        request: dict[str, object] = {
            "Bucket": self.bucket,
            "Prefix": provider_prefix,
            "MaxKeys": limit,
        }
        if cursor:
            request["ContinuationToken"] = cursor
        try:
            response = self.client.list_objects_v2(**request)
        except (BotoCoreError, ClientError) as exc:
            raise LibrarySourceError("library_source_list_failed") from exc
        entries = tuple(
            SourceEntry(
                key=self._logical_key(str(item["Key"])),
                size=int(item.get("Size") or 0),
                modified_at=item.get("LastModified"),
                etag=str(item.get("ETag") or "") or None,
            )
            for item in response.get("Contents", [])
            if not str(item.get("Key") or "").endswith("/")
        )
        complete = not bool(response.get("IsTruncated"))
        next_cursor = (
            None if complete else str(response.get("NextContinuationToken") or "")
        )
        if not complete and not next_cursor:
            raise LibrarySourceError("library_source_cursor_missing")
        return SourcePage(entries, next_cursor, complete, metadata_ops=1)

    @contextmanager
    def materialize(
        self, key: str, *, expected: SourceEntry | None = None
    ) -> Iterator[SourceContent]:
        from botocore.exceptions import BotoCoreError, ClientError

        safe_key = _safe_key(key)
        provider_key = self._provider_key(safe_key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=provider_key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            detail = (
                "library_source_missing"
                if code in {"404", "NoSuchKey", "NotFound"}
                else "library_source_read_failed"
            )
            raise LibrarySourceError(detail) from exc
        except BotoCoreError as exc:
            raise LibrarySourceError("library_source_read_failed") from exc
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise LibrarySourceError("library_source_read_failed")
        fd, raw = tempfile.mkstemp(suffix=Path(safe_key).suffix)
        path = Path(raw)
        written = 0
        started = time.monotonic()
        try:
            with open(fd, "wb", closefd=True) as output:
                while chunk := body.read(1024 * 1024):
                    output.write(chunk)
                    written += len(chunk)
                    if self.max_bytes_per_second:
                        target_elapsed = written / self.max_bytes_per_second
                        remaining = target_elapsed - (time.monotonic() - started)
                        if remaining > 0:
                            time.sleep(remaining)
            if hasattr(body, "close"):
                body.close()
            head_request: dict[str, object] = {
                "Bucket": self.bucket,
                "Key": provider_key,
            }
            version_id = response.get("VersionId")
            if version_id:
                head_request["VersionId"] = version_id
            try:
                current = self.client.head_object(**head_request)
            except (BotoCoreError, ClientError) as exc:
                raise LibrarySourceError("library_source_changed") from exc
            if (
                written != int(current.get("ContentLength") or 0)
                or (
                    response.get("ETag") and current.get("ETag") != response.get("ETag")
                )
                or (version_id and current.get("VersionId") != version_id)
            ):
                raise LibrarySourceError("library_source_changed")
            observation = SourceEntry(
                key=safe_key,
                size=written,
                modified_at=current.get("LastModified"),
                etag=current.get("ETag"),
                version_id=current.get("VersionId"),
            )
            _require_observation(expected, observation)
            yield SourceContent(path, observation)
        finally:
            if hasattr(body, "close"):
                body.close()
            path.unlink(missing_ok=True)


class OpenDalLibrarySource:
    def __init__(
        self,
        backend: OpenDALStorageBackend,
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
            time.sleep(remaining)

    def list_page(self, prefix: str, *, cursor: str | None, limit: int) -> SourcePage:
        if limit < 1:
            raise ValueError("limit must be positive")
        started = time.monotonic()
        if cursor:
            try:
                decoded = json.loads(cursor)
                stack = [
                    {"directory": str(frame["directory"]), "after": str(frame["after"])}
                    for frame in decoded
                ]
            except (KeyError, TypeError, ValueError) as exc:
                raise LibrarySourceError("library_source_cursor_invalid") from exc
        else:
            stack = [{"directory": prefix.strip("/"), "after": ""}]
        if not stack:
            return SourcePage((), None, True, metadata_ops=0)
        entries_list: list[SourceEntry] = []
        directory_cache: dict[str, list[SourceDirectoryEntry]] = {}
        operations = 0
        while stack and len(entries_list) < limit:
            frame = stack[-1]
            directory = frame["directory"]
            if directory not in directory_cache:
                try:
                    directory_cache[directory] = sorted(
                        self.backend.list_source_directory(
                            directory, max_entries=10_000
                        ),
                        key=lambda entry: entry.key,
                    )
                except StorageConfigurationError as exc:
                    raise LibrarySourceError(str(exc)) from exc
                operations += 1
                self._pace_metadata(started, operations)
            child = next(
                (
                    entry
                    for entry in directory_cache[directory]
                    if entry.key > frame["after"]
                ),
                None,
            )
            if child is None:
                stack.pop()
                directory_cache.pop(directory, None)
                continue
            frame["after"] = child.key
            if child.is_dir:
                stack.append({"directory": child.key, "after": ""})
                continue
            entries_list.append(
                SourceEntry(
                    key=child.key,
                    size=child.size,
                    modified_at=child.modified_at,
                    etag=child.etag,
                    version_id=child.version_id,
                )
            )
        complete = not stack
        next_cursor = None if complete else json.dumps(stack, separators=(",", ":"))
        if next_cursor is not None and len(next_cursor) > 2048:
            raise LibrarySourceError("library_source_cursor_too_large")
        return SourcePage(
            tuple(entries_list),
            next_cursor,
            complete,
            metadata_ops=operations,
        )

    @contextmanager
    def materialize(
        self, key: str, *, expected: SourceEntry | None = None
    ) -> Iterator[SourceContent]:
        safe_key = _safe_key(key)
        provider_key = self.backend.source_key(safe_key)
        before = self.backend.object_info(provider_key)
        if before is None:
            raise LibrarySourceError("library_source_missing")
        observation = SourceEntry(
            safe_key, before.size, before.modified_at, before.etag, before.version_id
        )
        _require_observation(expected, observation)
        fd, raw = tempfile.mkstemp(suffix=Path(safe_key).suffix)
        path = Path(raw)
        written = 0
        started = time.monotonic()
        try:
            with open(fd, "wb", closefd=True) as output:
                for chunk in self.backend.stream_chunks(provider_key, expected=before):
                    written += len(chunk)
                    if written > before.size:
                        raise LibrarySourceError("library_source_size_mismatch")
                    output.write(chunk)
                    if self.max_bytes_per_second:
                        target_elapsed = written / self.max_bytes_per_second
                        remaining = target_elapsed - (time.monotonic() - started)
                        if remaining > 0:
                            time.sleep(remaining)
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
        return OpenDalLibrarySource(
            OpenDALStorageBackend(resolve_transport(parsed)),
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
