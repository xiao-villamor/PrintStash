"""Resolve Artifact content without leaking storage layout into callers.

Managed Artifacts use the configured vault backend. External Artifacts keep an
absolute source path owned by the user's library and must never be passed to
that backend, especially when the vault itself is S3/OpenDAL.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import secrets
import sqlite3
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from app.core.errors import ErrorKind, OperationError
from app.db.models import ExternalLibrary, File, LibrarySourceKind
from app.db.session import get_session_factory
from app.modules.sources.library_source import (
    LibrarySourceError,
    SourceEntry,
    source_for_file,
)
from app.modules.storage.artifact_materializer import (
    CacheLease,
    CacheUnavailable,
    Representation,
    RepresentationChanged,
)
from app.modules.storage.materializer_runtime import get_materializer
from app.modules.storage.storage_backend.contracts import StorageBackend
from app.modules.storage.storage_backend.runtime import get_backend

_CHUNK_SIZE = 1024 * 1024


class ArtifactContentError(RuntimeError):
    """Base error for an Artifact whose bytes cannot be resolved safely."""


class ArtifactContentMissingError(ArtifactContentError):
    """The catalog row is live but its exact content is unavailable."""


class ArtifactContentChangedError(ArtifactContentError):
    """The external source changed while its bytes were being pinned."""


@dataclass(frozen=True)
class ArtifactHandle:
    """Small interface over managed and externally-owned Artifact bytes."""

    file: File
    backend: StorageBackend | None

    def _temporary_capacity_claim(self, operation: str):
        from app.modules.storage.capacity import CapacityManager, CapacityResource

        return CapacityManager(get_session_factory()).reserve(
            f"artifact-{operation}:{secrets.token_hex(12)}",
            [
                CapacityResource.for_path(
                    Path(tempfile.gettempdir()),
                    self.file.size_bytes,
                    role=f"Artifact {operation}",
                )
            ],
        )

    def _verified_remote_copy(self) -> Path:
        try:
            source, key = source_for_file(self.file)
            with source.materialize(
                key, expected=SourceEntry(key, self.file.size_bytes)
            ) as content:
                materialized = content.path
                fd, raw_temp = tempfile.mkstemp(suffix=materialized.suffix)
                temp = Path(raw_temp)
                digest = hashlib.sha256()
                copied = 0
                try:
                    with (
                        materialized.open("rb") as incoming,
                        os.fdopen(fd, "wb") as output,
                    ):
                        while chunk := incoming.read(_CHUNK_SIZE):
                            copied += len(chunk)
                            if copied > self.file.size_bytes:
                                raise ArtifactContentChangedError(self.file.path)
                            output.write(chunk)
                            digest.update(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    if (
                        copied != self.file.size_bytes
                        or digest.hexdigest() != self.file.sha256.lower()
                    ):
                        raise ArtifactContentChangedError(self.file.path)
                    return temp
                except Exception:
                    temp.unlink(missing_ok=True)
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    raise
        except LibrarySourceError as exc:
            raise ArtifactContentMissingError(self.file.path) from exc

    def _verified_external_content(self) -> Path:
        if self.file.source_key and self.file.external_library_id is not None:
            # Mounted scans also retain source_key for reconciliation.
            with get_session_factory().scoped_session() as session:
                library = session.get(ExternalLibrary, self.file.external_library_id)
                mounted = (
                    library is not None
                    and library.source_kind == LibrarySourceKind.MOUNTED
                )
            if mounted:
                return self._verified_external_copy()
        if self.file.source_key:
            return self._verified_remote_copy()
        return self._verified_external_copy()

    def _verified_external_copy(self) -> Path:
        source = Path(self.file.path)
        try:
            before = source.stat(follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ArtifactContentMissingError(self.file.path) from exc
        if not source.is_file() or source.is_symlink():
            raise ArtifactContentMissingError(self.file.path)
        if before.st_size != self.file.size_bytes:
            raise ArtifactContentChangedError(self.file.path)

        fd, raw_temp = tempfile.mkstemp(suffix=source.suffix)
        temp = Path(raw_temp)
        digest = hashlib.sha256()
        copied = 0
        try:
            source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(source_fd, "rb") as incoming, os.fdopen(fd, "wb") as output:
                while chunk := incoming.read(_CHUNK_SIZE):
                    copied += len(chunk)
                    if copied > self.file.size_bytes:
                        raise ArtifactContentChangedError(self.file.path)
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            after = source.stat(follow_symlinks=False)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or copied != self.file.size_bytes
                or digest.hexdigest() != self.file.sha256.lower()
            ):
                raise ArtifactContentChangedError(self.file.path)
            return temp
        except FileNotFoundError as exc:
            temp.unlink(missing_ok=True)
            raise ArtifactContentMissingError(self.file.path) from exc
        except Exception:
            temp.unlink(missing_ok=True)
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def cache_representation(self) -> Representation | None:
        """Only original immutable managed remote Artifact bytes are eligible."""
        if self.file.is_external or self.backend is None:
            return None
        direct_path = getattr(self.backend, "direct_path", None)
        if (
            direct_path is None
            or inspect.iscoroutinefunction(direct_path)
            or direct_path(self.file.path) is not None
        ):
            return None
        try:
            return Representation(
                "artifact", 1, self.file.sha256.lower(), self.file.size_bytes
            )
        except ValueError:
            return None

    def cached_path(self) -> CacheLease | None:
        """Acquire before returning: the HTTP adapter closes after the response."""
        cache = get_materializer()
        representation = self.cache_representation()
        if cache is None or representation is None:
            return None
        try:
            return cache.acquire(representation)
        except (CacheUnavailable, OSError, sqlite3.Error):
            return None

    def stream(
        self, chunk_size: int = _CHUNK_SIZE, *, authoritative: bool = False
    ) -> Iterator[bytes]:
        """Stream owned bytes, coalescing eligible full reads before provider IO."""
        if self.file.is_external:
            claim = self._temporary_capacity_claim("stream")
            try:
                temp = self._verified_external_content()
            except BaseException:
                claim.release()
                raise

            def cleanup_external() -> None:
                temp.unlink(missing_ok=True)
                claim.release()

            return _PathChunks(temp, chunk_size, cleanup_external)

        lease = None if authoritative else self.cached_path()
        if lease:
            return _PathChunks(lease.path, chunk_size, lease.close)
        backend = self.backend
        if backend is None or not backend.exists(self.file.path):
            raise ArtifactContentMissingError(self.file.path)
        cache = get_materializer()
        representation = None if authoritative else self.cache_representation()
        fill = None
        if cache is not None and representation is not None:
            waiting = ExitStack()
            try:
                fill = cache.begin_fill(representation)
                if fill is None:
                    # A racing reader waits on the durable same-key claim before
                    # asking the provider for bytes. Refused admission falls back.
                    path = waiting.enter_context(
                        cache.materialize(
                            representation,
                            lambda: iter(
                                backend.stream_chunks(self.file.path, chunk_size)
                            ),
                        )
                    )
                    return _PathChunks(path, chunk_size, waiting.close)
            except RepresentationChanged as exc:
                waiting.close()
                raise ArtifactContentChangedError(
                    "artifact_representation_changed"
                ) from exc
            except OperationError as exc:
                waiting.close()
                if exc.kind is not ErrorKind.CAPACITY:
                    raise
            except (CacheUnavailable, OSError, sqlite3.Error):
                waiting.close()

        chunks = iter(backend.stream_chunks(self.file.path, chunk_size))
        try:
            first = next(chunks, b"")
        except BaseException:
            if fill is not None:
                fill.close()
            close = getattr(chunks, "close", None)
            if close:
                close()
            raise

        def managed_chunks() -> Iterator[bytes]:
            current_fill = fill
            try:
                for chunk in _prepend(first, chunks):
                    if current_fill is not None:
                        try:
                            current_fill.write(chunk)
                        except RepresentationChanged as exc:
                            raise ArtifactContentChangedError(
                                "artifact_representation_changed"
                            ) from exc
                        except (CacheUnavailable, OSError, sqlite3.Error):
                            current_fill.close()
                            current_fill = None
                    yield chunk
                if current_fill is not None:
                    try:
                        completed = current_fill.complete()
                        if completed:
                            completed.close()
                    except RepresentationChanged as exc:
                        # Headers may already be sent: abort the body instead of
                        # silently finishing a transfer known to be corrupt.
                        raise ArtifactContentChangedError(
                            "artifact_representation_changed"
                        ) from exc
                    except (CacheUnavailable, OSError, sqlite3.Error):
                        pass
            finally:
                if current_fill is not None:
                    current_fill.close()
                close = getattr(chunks, "close", None)
                if close:
                    close()

        return _ClosingIterator(managed_chunks(), chunks, fill.close if fill else None)

    @contextmanager
    def materialize(
        self,
        *,
        authoritative: bool = False,
        capacity_claimed: bool = False,
    ) -> Iterator[Path]:
        """Yield a stable local file for consumers that require a path."""
        if self.file.is_external:
            claim = (
                None
                if capacity_claimed
                else self._temporary_capacity_claim("materialization")
            )
            try:
                temp = self._verified_external_content()
                try:
                    yield temp
                finally:
                    temp.unlink(missing_ok=True)
            finally:
                if claim is not None:
                    claim.release()
            return

        if self.backend is None or not self.backend.exists(self.file.path):
            raise ArtifactContentMissingError(self.file.path)
        backend = self.backend
        cache = get_materializer()
        representation = None if authoritative else self.cache_representation()
        with ExitStack() as stack:
            path = None
            if cache is not None and representation is not None:
                try:
                    path = stack.enter_context(
                        cache.materialize(
                            representation,
                            lambda: iter(
                                backend.stream_chunks(self.file.path, _CHUNK_SIZE)
                            ),
                        )
                    )
                except RepresentationChanged as exc:
                    raise ArtifactContentChangedError(
                        "artifact_representation_changed"
                    ) from exc
                except OperationError as exc:
                    if exc.kind is not ErrorKind.CAPACITY:
                        raise
                    # The optional cache can occupy a different volume. The
                    # fallback below independently reserves its actual temp root.
                except (CacheUnavailable, OSError, sqlite3.Error):
                    pass
            if path is None:
                direct_path = getattr(backend, "direct_path", lambda _key: None)
                direct = (
                    None
                    if inspect.iscoroutinefunction(direct_path)
                    else direct_path(self.file.path)
                )
                if direct is None and not capacity_claimed:
                    claim = self._temporary_capacity_claim("materialization")
                    stack.callback(claim.release)
                path = stack.enter_context(backend.local_path(self.file.path))
            yield path


def resolve(file: File, *, backend: StorageBackend | None = None) -> ArtifactHandle:
    """Resolve one Artifact without exposing its storage kind to the caller."""
    return ArtifactHandle(
        file=file,
        backend=(
            None
            if file.is_external
            else (backend if backend is not None else get_backend())
        ),
    )


def _prepend(first: bytes, chunks: Iterator[bytes]) -> Iterator[bytes]:
    if first:
        yield first
    yield from chunks


class _ClosingIterator:
    """Always close the primed source, including before the first consumer read."""

    def __init__(
        self,
        chunks: Iterator[bytes],
        source: Iterator[bytes],
        cleanup: Callable[[], None] | None = None,
    ):
        self.cleanup = cleanup
        self.chunks = chunks
        self.source = source

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        return next(self.chunks)

    def close(self) -> None:
        try:
            close = getattr(self.chunks, "close", None)
            if close:
                close()
        finally:
            try:
                close = getattr(self.source, "close", None)
                if close:
                    close()
            finally:
                if self.cleanup:
                    cleanup, self.cleanup = self.cleanup, None
                    cleanup()


class _PathChunks:
    def __init__(self, path: Path, chunk_size: int, cleanup: Callable[[], None]):
        self.path = path
        self.chunk_size = chunk_size
        self.cleanup = cleanup
        self.source: BinaryIO | None = None
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self.closed:
            raise StopIteration
        try:
            if self.source is None:
                self.source = self.path.open("rb")
            chunk = self.source.read(self.chunk_size)
            if not chunk:
                self.close()
                raise StopIteration
            return chunk
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                if self.source:
                    self.source.close()
            finally:
                self.cleanup()
