"""Crash-safe fixed-size chunk storage below the private staging root."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from app.db.models import ArtifactUploadSession

from .contracts import ChunkReceipt, VerifiedStagedArtifact

CHUNK_SIZE = 8 * 1024 * 1024
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ApiChunkError(ValueError):
    pass


class ApiChunkUploadAdapter:
    adapter_id = "api_chunks"

    def __init__(self, root: Path) -> None:
        self.root = root

    def session_directory(self, session_id: str, *, create: bool = False) -> Path:
        if not _SESSION_ID.fullmatch(session_id):
            raise ApiChunkError("artifact_upload_id_invalid")
        directory = self.root / session_id
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.mkdir(mode=0o700)
        return directory

    @staticmethod
    def expected_chunk(session: ArtifactUploadSession, index: int) -> tuple[int, int]:
        if index < 0:
            raise ApiChunkError("artifact_upload_chunk_index_invalid")
        offset = index * CHUNK_SIZE
        if offset >= session.declared_size:
            raise ApiChunkError("artifact_upload_chunk_index_invalid")
        return offset, min(CHUNK_SIZE, session.declared_size - offset)

    def write_chunk(
        self,
        session: ArtifactUploadSession,
        receipt: ChunkReceipt,
        payload: bytes,
    ) -> None:
        offset, expected_size = self.expected_chunk(session, receipt.index)
        digest = hashlib.sha256(payload).hexdigest()
        if receipt.offset != offset:
            raise ApiChunkError("artifact_upload_chunk_offset_invalid")
        if receipt.size_bytes != expected_size or len(payload) != expected_size:
            raise ApiChunkError("artifact_upload_chunk_length_invalid")
        if digest != receipt.sha256.lower():
            raise ApiChunkError("artifact_upload_chunk_hash_invalid")

        directory = self.session_directory(session.id)
        destination = directory / f"{receipt.index:08d}.chunk"
        fd, temporary_name = tempfile.mkstemp(prefix=".chunk-", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                existing = destination.read_bytes()
                if (
                    len(existing) != expected_size
                    or hashlib.sha256(existing).hexdigest() != digest
                ):
                    raise ApiChunkError("artifact_upload_chunk_conflict") from None
            self._fsync_directory(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def assemble(self, session: ArtifactUploadSession) -> VerifiedStagedArtifact:
        directory = self.session_directory(session.id)
        expected_parts = (session.declared_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        destination = directory / "assembled.upload"
        fd, temporary_name = tempfile.mkstemp(prefix=".assembly-", dir=directory)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as output:
                for index in range(expected_parts):
                    chunk = directory / f"{index:08d}.chunk"
                    if not chunk.is_file():
                        raise ApiChunkError("artifact_upload_incomplete")
                    with chunk.open("rb") as source:
                        while data := source.read(1024 * 1024):
                            output.write(data)
                            digest.update(data)
                            size += len(data)
                output.flush()
                os.fsync(output.fileno())
            if size != session.declared_size:
                raise ApiChunkError("artifact_upload_size_mismatch")
            sha256 = digest.hexdigest()
            if session.client_sha256 and sha256 != session.client_sha256.lower():
                raise ApiChunkError("artifact_upload_hash_mismatch")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                existing = self._verified_existing(destination, session)
                temporary.unlink(missing_ok=True)
                return existing
            self._fsync_directory(directory)
            # Removing the temporary hardlink changes the inode ctime. Do it
            # before capturing immutable identity for the surviving name.
            temporary.unlink(missing_ok=True)
            return self._verified(destination, session.filename, size, sha256)
        finally:
            temporary.unlink(missing_ok=True)

    def abort_owned(self, session: ArtifactUploadSession) -> None:
        directory = self.session_directory(session.id)
        if not directory.is_dir():
            return
        expected_parts = (session.declared_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        for index in range(expected_parts):
            (directory / f"{index:08d}.chunk").unlink(missing_ok=True)
        (directory / "assembled.upload").unlink(missing_ok=True)
        for temporary in directory.glob(".chunk-*"):
            temporary.unlink(missing_ok=True)
        for temporary in directory.glob(".assembly-*"):
            temporary.unlink(missing_ok=True)
        directory.rmdir()

    @staticmethod
    def _verified(
        path: Path, filename: str, size: int, sha256: str
    ) -> VerifiedStagedArtifact:
        stat = path.stat(follow_symlinks=False)
        return VerifiedStagedArtifact(
            path=path,
            size_bytes=size,
            sha256=sha256,
            filename=filename,
            device=stat.st_dev,
            inode=stat.st_ino,
            ctime_ns=stat.st_ctime_ns,
        )

    def _verified_existing(
        self, path: Path, session: ArtifactUploadSession
    ) -> VerifiedStagedArtifact:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while data := stream.read(1024 * 1024):
                digest.update(data)
                size += len(data)
        sha256 = digest.hexdigest()
        if size != session.declared_size or (
            session.client_sha256 and sha256 != session.client_sha256.lower()
        ):
            raise ApiChunkError("artifact_upload_assembly_conflict")
        return self._verified(path, session.filename, size, sha256)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
