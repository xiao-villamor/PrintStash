"""Trash and hard-delete helpers for soft-deleted library models."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, delete, select

from app.core.time import utcnow
from app.db.models import File, Metadata, Model, ModelTagLink, PrintJob, PrinterFile
from app.services.storage_backend import get_backend


def trash_expires_at(deleted_at: datetime | None, retention_days: int) -> datetime | None:
    if deleted_at is None or retention_days < 0:
        return None
    return deleted_at + timedelta(days=retention_days)


def hard_delete_model(session: Session, model: Model) -> None:
    """Permanently remove a model, related DB rows, and stored blobs."""
    if model.id is None:
        return

    backend = get_backend()
    file_rows = session.exec(select(File).where(File.model_id == model.id)).all()
    file_ids = [row.id for row in file_rows if row.id is not None]

    for file_row in file_rows:
        backend.delete(file_row.path)
        if file_row.id is not None:
            backend.delete(backend.thumbnail_key(file_row.id))

    model.thumbnail_file_id = None
    model.thumbnail_path = None
    session.add(model)
    session.flush()

    if file_ids:
        session.exec(delete(PrinterFile).where(PrinterFile.file_id.in_(file_ids)))  # type: ignore[call-overload, union-attr]
        session.exec(delete(PrintJob).where(PrintJob.file_id.in_(file_ids)))  # type: ignore[call-overload, union-attr]
        session.exec(delete(Metadata).where(Metadata.file_id.in_(file_ids)))  # type: ignore[call-overload, union-attr]
        session.exec(delete(File).where(File.id.in_(file_ids)))  # type: ignore[call-overload, union-attr]

    session.exec(delete(ModelTagLink).where(ModelTagLink.model_id == model.id))  # type: ignore[call-overload]
    session.delete(model)


def hard_delete_expired_models(session: Session, retention_days: int) -> list[int]:
    if retention_days < 0:
        return []

    cutoff = utcnow() - timedelta(days=retention_days)
    models = session.exec(
        select(Model).where(
            Model.deleted_at.is_not(None),  # type: ignore[union-attr]
            Model.deleted_at <= cutoff,  # type: ignore[operator]
        )
    ).all()
    purged_ids = [model.id for model in models if model.id is not None]
    for model in models:
        hard_delete_model(session, model)
    return [int(model_id) for model_id in purged_ids]
