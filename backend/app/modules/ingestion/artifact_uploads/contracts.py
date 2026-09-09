"""Deep provider-neutral contracts for resumable upload orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.db.models import ArtifactUploadPart, ArtifactUploadSession, User


@dataclass(frozen=True)
class UploadRequest:
    purpose: str
    target_role: str
    target_id: str | None
    filename: str
    media_type: str
    size_bytes: int
    client_sha256: str | None
    options: dict[str, object]


@dataclass(frozen=True)
class UploadPlan:
    mode: str
    chunk_size: int
    max_parallel: int
    upload_path: str


@dataclass(frozen=True)
class ChunkReceipt:
    index: int
    offset: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class VerifiedStagedArtifact:
    """Exact immutable staging evidence accepted by the ingestion boundary."""

    path: Path
    size_bytes: int
    sha256: str
    filename: str
    device: int
    inode: int
    ctime_ns: int

    def materialize(self) -> Path:
        """Return the verified local representation without copying it."""

        current = self.path.stat(follow_symlinks=False)
        if (
            current.st_size,
            current.st_dev,
            current.st_ino,
            current.st_ctime_ns,
        ) != (self.size_bytes, self.device, self.inode, self.ctime_ns):
            raise RuntimeError("verified_staging_identity_changed")
        digest = hashlib.sha256()
        with self.path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != self.sha256:
            raise RuntimeError("verified_staging_identity_changed")
        return self.path


class ArtifactUploadManager(Protocol):
    def create(self, request: UploadRequest, actor: User) -> ArtifactUploadSession: ...

    def plan(self, session_id: str, actor: User) -> UploadPlan: ...

    def record_chunk(
        self,
        session_id: str,
        receipt: ChunkReceipt,
        payload: bytes,
        actor: User,
    ) -> ArtifactUploadPart: ...

    def finalize(
        self, session_id: str, actor: User
    ) -> tuple[ArtifactUploadSession, VerifiedStagedArtifact]: ...

    def abort(self, session_id: str, actor: User) -> ArtifactUploadSession: ...
