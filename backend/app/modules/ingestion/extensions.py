"""Optional mesh derivatives, bound outside the Artifact ingestion owner."""

from typing import Protocol, TypedDict

from app.db.session import SessionFactory
from app.modules.media.fingerprints import FingerprintResult


class MeshExtractionOptions(TypedDict, total=False):
    include_fingerprint: bool
    triangle_cap: int


class MeshDerivatives(Protocol):
    def extraction_options(self, sessions: SessionFactory) -> MeshExtractionOptions: ...

    def after_commit(
        self,
        sessions: SessionFactory,
        file_id: int,
        actor_id: int | None,
        result: FingerprintResult,
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
) -> str | None:
    return (
        _derivatives.after_commit(sessions, file_id, actor_id, result)
        if _derivatives
        else None
    )
