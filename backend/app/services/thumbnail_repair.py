from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import File, FileType, Model
from app.db.scopes import live
from app.services import mesh_processing, thumbnail
from app.services.storage_backend import get_backend

_MESH_TYPES = (FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP)


def regenerate_model_thumbnail(session: Session, model_id: int) -> bool:
    """Idempotently rebuild one Model thumbnail from newest readable mesh."""
    model = session.exec(select(Model).where(Model.id == model_id, live(Model))).first()
    if model is None:
        return False
    mesh = session.exec(
        select(File)
        .where(
            File.model_id == model_id,
            File.file_type.in_(_MESH_TYPES),  # type: ignore[attr-defined]
            live(File),
        )
        .order_by(File.version.desc(), File.id.desc())  # type: ignore[attr-defined]
    ).first()
    if mesh is None or mesh.id is None:
        return False
    backend = get_backend()
    if not backend.exists(mesh.path):
        return False
    with backend.local_path(mesh.path) as path:
        data = mesh_processing.render_thumbnail(path)
    if not data:
        return False
    key = backend.thumbnail_key(mesh.id)
    backend.write_bytes(thumbnail.to_webp(data), key)
    backend.delete(backend.legacy_thumbnail_key(mesh.id))
    model.thumbnail_file_id = mesh.id
    model.thumbnail_path = key
    session.add(model)
    session.commit()
    return True
