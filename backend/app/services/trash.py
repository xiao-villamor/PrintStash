"""Trash lifecycle for the library — the single owner of soft-delete semantics.

Soft-delete → restore → expiry → hard delete (rows + blobs) → orphan-blob GC
all live here. Query-side filtering uses ``app.db.scopes.live/trashed``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    Collection,
    File,
    Metadata,
    Model,
    ModelTagLink,
    Printer,
    PrintJob,
    PrinterFile,
    Tag,
    User,
)
from app.db.scopes import trashed
from app.db.session import get_session_factory
from app.services.storage_backend import get_backend

logger = get_logger(__name__)


def trash_expires_at(
    deleted_at: datetime | None, retention_days: int
) -> datetime | None:
    if deleted_at is None or retention_days < 0:
        return None
    return deleted_at + timedelta(days=retention_days)


def soft_delete_model(session: Session, model: Model) -> None:
    """Move a model to the trash."""
    model.deleted_at = utcnow()
    model.updated_at = utcnow()
    session.add(model)
    session.commit()


def restore_model(session: Session, model: Model) -> None:
    """Bring a model back from the trash. No-op when it is live."""
    if model.deleted_at is None:
        return
    model.deleted_at = None
    model.deleted_by = None
    model.updated_at = utcnow()
    session.add(model)
    session.commit()


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
            trashed(Model),
            Model.deleted_at <= cutoff,  # type: ignore[operator]
        )
    ).all()
    purged_ids = [model.id for model in models if model.id is not None]
    for model in models:
        hard_delete_model(session, model)
    return [int(model_id) for model_id in purged_ids]


def _cleanup_orphan_blobs(session: Session) -> int:
    backend = get_backend()
    file_paths = set(session.exec(select(File.path)).all())
    removed = 0
    if settings.storage_backend == "s3":
        walker = backend.walk_keys("vault-data/files/")
    else:
        walker = backend.walk_keys(str(settings.data_dir))
    for key in walker:
        if key not in file_paths:
            backend.delete(key)
            removed += 1
    return removed


def gc_soft_deleted(retention_days: int | None = None) -> dict[str, int]:
    """Hourly GC: purge expired trash rows across all soft-deletable tables,
    then sweep orphaned blobs."""
    effective_retention = (
        int(settings.trash_retention_days) if retention_days is None else retention_days
    )
    if effective_retention < 0:
        logger.info("gc skipped: trash retention is disabled")
        return {"rows": 0, "orphan_blobs": 0}
    cutoff = utcnow() - timedelta(days=effective_retention)
    purged = {"rows": 0, "orphan_blobs": 0}
    with get_session_factory().scoped_session() as session:
        purged_model_ids = hard_delete_expired_models(session, effective_retention)
        purged["rows"] += len(purged_model_ids)
        for model in (File, Tag, Collection, Printer, User):
            result = session.exec(
                delete(model).where(
                    trashed(model),
                    model.deleted_at < cutoff,  # type: ignore[attr-defined]
                )
            )
            purged["rows"] += int(result.rowcount or 0)
        session.commit()
        purged["orphan_blobs"] = _cleanup_orphan_blobs(session)
    logger.info(
        "gc complete: rows=%s orphan_blobs=%s", purged["rows"], purged["orphan_blobs"]
    )
    return purged
