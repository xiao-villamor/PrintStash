"""Prepare viewer STL as an owned file, retaining bounded parent memory.

The worker exports into a caller-owned workspace and reports a fixed size/digest
manifest. The parent opens the output without following symlinks, hashes that
same descriptor in bounded blocks, and retains it through publication. A file's
path is for staging cleanup; publication consumes the verified open stream.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import struct
from collections.abc import Buffer
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from app.core.cancellation import checkpoint
from app.modules.media import mesh_isolation
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.mesh_policy import canonical_suffix
from app.modules.media.native_budget import GeometryWork, MeshSource

MANIFEST_MAGIC = b"STL2"
FAILURE_MAGIC = b"FAIL"
MAX_STL_BYTES = 256 * 1024 * 1024
_IO_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def of(cls, value: os.stat_result) -> FileIdentity:
        return cls(
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )


@dataclass(frozen=True)
class STLManifest:
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if type(self.size) is not int or not 0 <= self.size < 2**64:
            raise ValueError("STL manifest size must fit an unsigned64 integer")
        if len(self.sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.sha256
        ):
            raise ValueError("STL manifest SHA256 must be a lowercase hex digest")


def manifest_for(stream: BinaryIO) -> STLManifest:
    """Hash bounded blocks from the current position, then rewind for publication."""
    hasher = hashlib.sha256()
    size = 0
    remaining = os.fstat(stream.fileno()).st_size
    while remaining:
        checkpoint()
        chunk = stream.read(min(_IO_CHUNK_BYTES, remaining))
        if not chunk:
            break
        size += len(chunk)
        remaining -= len(chunk)
        hasher.update(chunk)
    stream.seek(0)
    return STLManifest(size, hasher.hexdigest())


def encode_manifest(manifest: STLManifest) -> bytes:
    return (
        MANIFEST_MAGIC
        + struct.pack("!Q", manifest.size)
        + bytes.fromhex(manifest.sha256)
    )


def decode_manifest(payload: bytes) -> STLManifest:
    if payload.startswith(FAILURE_MAGIC):
        try:
            reason = ThumbnailFailureReason(
                payload[len(FAILURE_MAGIC) :].decode("ascii")
            )
        except ValueError, UnicodeDecodeError:
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from None
        raise MeshWorkerError(reason)
    if len(payload) != 44 or not payload.startswith(MANIFEST_MAGIC):
        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
    return STLManifest(struct.unpack_from("!Q", payload, 4)[0], payload[12:].hex())


@dataclass(frozen=True)
class PreparedSTL:
    path: Path
    size: int
    sha256: str
    stream: BinaryIO
    _identity: FileIdentity
    _source: Path
    _source_identity: FileIdentity

    def verify(self) -> None:
        """Recheck the same descriptor after storage streaming, before adoption."""
        if FileIdentity.of(os.fstat(self.stream.fileno())) != self._identity:
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
        _check_source(self._source, self._source_identity)
        checkpoint(force=True)


def _source_identity(path: Path) -> FileIdentity:
    try:
        return FileIdentity.of(path.stat())
    except OSError as exc:
        raise MeshWorkerError(ThumbnailFailureReason.STORAGE) from exc


def _check_source(path: Path, expected: FileIdentity) -> None:
    if _source_identity(path) != expected:
        raise MeshWorkerError(ThumbnailFailureReason.SOURCE_CHANGED)


class _PublicationReader(io.BufferedReader):
    """Verified file descriptor with cooperative publication checkpoints.

    All reads preserve BinaryIO semantics. Memory bounds come from the caller
    using explicit chunks, as hashing, copying and publication already do.
    File-backed providers using pread bypass these cooperative read checkpoints.
    """

    def read(self, size: int | None = -1) -> bytes:
        checkpoint()
        return super().read(size)

    def read1(self, size: int = -1) -> bytes:
        checkpoint()
        return super().read1(size)

    def readinto(self, buffer: Buffer) -> int:
        checkpoint()
        return super().readinto(buffer)

    def readinto1(self, buffer: Buffer) -> int:
        checkpoint()
        return super().readinto1(buffer)


def _open_output(path: Path) -> BinaryIO:
    try:
        directory_fd = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            fd = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        finally:
            os.close(directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
            return _PublicationReader(os.fdopen(fd, "rb", buffering=0))
        except BaseException:
            os.close(fd)
            raise
    except OSError as exc:
        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc


@contextmanager
def prepare_stl(
    path: Path,
    *,
    file_type: str | None,
    expected_sha256: str,
    workspace: Path,
) -> Iterator[PreparedSTL]:
    """Prepare a verified stream under the caller's recoverable byte reservation."""
    source = path.absolute()
    identity = _source_identity(source)
    if canonical_suffix(source, file_type) == ".stl":
        if identity.size > MAX_STL_BYTES:
            raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
        with mesh_isolation.worker_output_directory(workspace) as directory:
            output = directory / "mesh.stl"
            try:
                with source.open("rb") as original, output.open("xb") as destination:
                    remaining = identity.size
                    while remaining:
                        checkpoint()
                        chunk = original.read(min(_IO_CHUNK_BYTES, remaining))
                        if not chunk:
                            break
                        destination.write(chunk)
                        remaining -= len(chunk)
            except OSError as exc:
                raise MeshWorkerError(ThumbnailFailureReason.STORAGE) from exc
            with _open_output(output) as stream:
                manifest = manifest_for(stream)
                if manifest.sha256 != expected_sha256:
                    raise MeshWorkerError(ThumbnailFailureReason.SOURCE_CHANGED)
                _check_source(source, identity)
                prepared = PreparedSTL(
                    output,
                    manifest.size,
                    manifest.sha256,
                    stream,
                    FileIdentity.of(os.fstat(stream.fileno())),
                    source,
                    identity,
                )
                prepared.verify()
                yield prepared
        return
    spec = {
        "path": str(source),
        "file_type": file_type,
        "expected_sha256": expected_sha256,
    }
    with mesh_isolation.prepared_worker_result(
        "app.modules.media.stl_worker",
        spec,
        workspace=workspace,
        sources=(MeshSource(source, file_type or source.suffix),),
        work=GeometryWork(),
        reply_limit=44,
    ) as (reply, directory):
        manifest = decode_manifest(reply.payload)
        if manifest.size > MAX_STL_BYTES:
            raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
        output = directory / "mesh.stl"
        with _open_output(output) as stream:
            output_identity = FileIdentity.of(os.fstat(stream.fileno()))
            # Converted outputs are binary STL; a matching digest alone cannot
            # certify an empty file or a truncated facet payload.
            header = stream.read(84)
            if len(header) != 84:
                raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
            face_count = struct.unpack_from("<I", header, 80)[0]
            if face_count == 0 or output_identity.size != 84 + 50 * face_count:
                raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
            stream.seek(0)
            if (
                output_identity.size != manifest.size
                or manifest_for(stream) != manifest
            ):
                raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
            if FileIdentity.of(os.fstat(stream.fileno())) != output_identity:
                raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
            _check_source(source, identity)
            prepared = PreparedSTL(
                output,
                manifest.size,
                manifest.sha256,
                stream,
                output_identity,
                source,
                identity,
            )
            prepared.verify()
            yield prepared
