"""Optional mesh derivatives, bound outside the Artifact ingestion owner."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypedDict

from sqlmodel import Session

from app.db.models import File
from app.db.session import SessionFactory
from app.modules.media.fingerprints import FingerprintResult, FingerprintResultState
from app.modules.work.contracts import JobExecution


class MeshExtractionOptions(TypedDict, total=False):
    include_fingerprint: bool
    triangle_cap: int


@dataclass(frozen=True)
class MeshFingerprintPublished:
    state: FingerprintResultState


@dataclass(frozen=True)
class MeshFingerprintDeferred:
    available_at: datetime


@dataclass(frozen=True)
class MeshFingerprintRejected:
    pass


MeshFingerprintPublication = (
    MeshFingerprintPublished | MeshFingerprintDeferred | MeshFingerprintRejected
)


class MeshDerivatives(Protocol):
    def extraction_options(self, sessions: SessionFactory) -> MeshExtractionOptions: ...

    def publish_mesh_fingerprint_continuation(
        self, session: Session, file: File, result: FingerprintResult
    ) -> MeshFingerprintPublication: ...

    def after_commit(
        self,
        sessions: SessionFactory,
        file_id: int,
        actor_id: int | None,
        result: FingerprintResult,
        *,
        source_sha256: str,
        execution: JobExecution | None = None,
    ) -> str: ...


_derivatives: MeshDerivatives | None = None


def bind_derivatives(provider: MeshDerivatives | None) -> None:
    global _derivatives
    _derivatives = provider


def extraction_options(sessions: SessionFactory) -> MeshExtractionOptions:
    return _derivatives.extraction_options(sessions) if _derivatives else {}


def after_commit(
    sessions: SessionFactory,
    file_id: int,
    actor_id: int | None,
    result: FingerprintResult,
    *,
    source_sha256: str,
    execution: JobExecution | None = None,
) -> str | None:
    return (
        _derivatives.after_commit(
            sessions,
            file_id,
            actor_id,
            result,
            source_sha256=source_sha256,
            execution=execution,
        )
        if _derivatives
        else None
    )


def publish_mesh_fingerprint_continuation(
    session: Session, file: File, result: FingerprintResult
) -> MeshFingerprintPublication:
    """Persist optional evidence and follow-up intent inside the owner's fence."""
    if _derivatives is None:
        raise RuntimeError("mesh_derivative_provider_unbound")
    return _derivatives.publish_mesh_fingerprint_continuation(session, file, result)
