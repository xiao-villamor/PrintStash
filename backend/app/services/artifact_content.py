"""Resolve Artifact content without leaking storage layout into callers.

Managed Artifacts use the configured vault backend. External Artifacts keep an
absolute source path owned by the user's library and must never be passed to
that backend, especially when the vault itself is S3/OpenDAL.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.db.models import File
from app.services.library_source import LibrarySourceError, SourceEntry, source_for_file
from app.services.storage_backend import StorageBackend, get_backend

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

    def stream(self, chunk_size: int = _CHUNK_SIZE) -> Iterator[bytes]:
        """Return a streaming iterator over immutable content.

        External bytes are copied and verified before the iterator is returned,
        so an error is reported before an HTTP response starts sending data.
        """
        if self.file.is_external:
            temp = self._verified_external_content()

            def external_chunks() -> Iterator[bytes]:
                try:
                    with temp.open("rb") as source:
                        while chunk := source.read(chunk_size):
                            yield chunk
                finally:
                    temp.unlink(missing_ok=True)

            return external_chunks()

        if self.backend is None or not self.backend.exists(self.file.path):
            raise ArtifactContentMissingError(self.file.path)
        chunks = iter(self.backend.stream_chunks(self.file.path, chunk_size))
        try:
            first = next(chunks)
        except StopIteration:
            return iter(())
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ArtifactContentMissingError(self.file.path) from exc

        def managed_chunks() -> Iterator[bytes]:
            try:
                yield first
                yield from chunks
            finally:
                close = getattr(chunks, "close", None)
                if close is not None:
                    close()

        return managed_chunks()

    @contextmanager
    def materialize(self) -> Iterator[Path]:
        """Yield a stable local file for consumers that require a path."""
        if self.file.is_external:
            temp = self._verified_external_content()
            try:
                yield temp
            finally:
                temp.unlink(missing_ok=True)
            return

        if self.backend is None or not self.backend.exists(self.file.path):
            raise ArtifactContentMissingError(self.file.path)
        with self.backend.local_path(self.file.path) as path:
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


def presigned_download_url(file: File, filename: str) -> str | None:
    """Return a vault-native URL only for vault-managed Artifact content."""
    if file.is_external:
        return None
    return get_backend().presigned_download_url(file.path, filename)
