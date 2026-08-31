from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import File, FileType, Model
from app.db.scopes import live
from app.services.storage_backend import get_backend
from app.services.thumbnail_engine import ThumbnailEngine
from app.services.thumbnail_generations import (
    ThumbnailEnsureOutcome,
    ThumbnailEnsureResult,
    ensure_thumbnail,
)

_MESH_TYPES = (FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP)


def regenerate_model_thumbnail_result(
    session: Session, model_id: int, *, force: bool = False
) -> ThumbnailEnsureResult:
    """Ensure one Model thumbnail, trying live revisions newest to oldest."""
    model = session.exec(select(Model).where(Model.id == model_id, live(Model))).first()
    if model is None:
        return ThumbnailEnsureResult(
            ThumbnailEnsureOutcome.FAILED, None, failure_reason="model_not_found"
        )
    meshes = session.exec(
        select(File)
        .where(
            File.model_id == model_id,
            File.file_type.in_(_MESH_TYPES),  # type: ignore[attr-defined]
            live(File),
        )
        .order_by(File.version.desc(), File.id.desc())  # type: ignore[attr-defined]
    ).all()
    last_result: ThumbnailEnsureResult | None = None
    for mesh in meshes:
        if mesh.id is None:
            continue
        result = ensure_thumbnail(
            session,
            mesh,
            force=force,
            promote=True,
            backend=get_backend(),
            engine=ThumbnailEngine(),
        )
        last_result = result
        if result.available:
            return result
        if result.outcome is ThumbnailEnsureOutcome.COALESCED:
            return result
    return last_result or ThumbnailEnsureResult(
        ThumbnailEnsureOutcome.FAILED, None, failure_reason="no_readable_mesh"
    )


def regenerate_model_thumbnail(session: Session, model_id: int) -> bool:
    """Backwards-compatible boolean repair API."""
    return regenerate_model_thumbnail_result(session, model_id).available
