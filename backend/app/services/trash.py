"""Trash lifecycle for the library — the single owner of soft-delete semantics.

Soft-delete → restore → expiry → hard delete (rows + blobs) → orphan-blob GC
all live here. Query-side filtering uses ``app.db.scopes.live/trashed``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable

from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    Collection,
    Document,
    File,
    FileType,
    Metadata,
    Model,
    Printer,
    PrinterFile,
    PrintJob,
    Tag,
    User,
)
from app.db.scopes import live, trashed
from app.db.session import get_session_factory
from app.services.storage_backend import get_backend
from app.services.storage_utils import ownership_snapshot

logger = get_logger(__name__)
_DOCUMENT_IMAGE_RE = re.compile(
    r"/api/v1/documents/(\d+)/images/([0-9a-f]{64}\.(?:png|jpe?g|gif|webp))"
)


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


def soft_delete_models(session: Session, models: Iterable[Model]) -> None:
    """Move several models to the trash without committing.

    Caller is responsible for the single ``session.commit()`` so a batch is
    persisted atomically.
    """
    now = utcnow()
    for model in models:
        model.deleted_at = now
        model.updated_at = now
        session.add(model)


def restore_model(session: Session, model: Model) -> None:
    """Bring a model back from the trash. No-op when it is live."""
    if model.deleted_at is None:
        return
    model.deleted_at = None
    model.deleted_by = None
    model.updated_at = utcnow()
    session.add(model)
    session.commit()


def hard_delete_file(
    session: Session,
    file_row: File,
    *,
    maintain_revision_invariant: bool = True,
) -> None:
    """Permanently remove one Artifact and every vault-owned dependent.

    Linked external bytes belong to the user and are never deleted. The caller
    owns the surrounding transaction and commit.
    """
    if file_row.id is None:
        return

    backend = get_backend()
    file_id = int(file_row.id)
    if not file_row.is_external:
        backend.delete(file_row.path)
    backend.delete(backend.thumbnail_key(file_id))
    backend.delete(backend.legacy_thumbnail_key(file_id))
    shared_cache_owner = session.exec(
        select(File.id).where(
            File.id != file_id,
            File.sha256 == file_row.sha256,
        )
    ).first()
    if shared_cache_owner is None and file_row.sha256:
        backend.delete(backend.stl_cache_key(file_row.sha256))

    model = session.get(Model, file_row.model_id)
    if model is not None and model.thumbnail_file_id == file_id:
        model.thumbnail_file_id = None
        model.thumbnail_path = None
        model.updated_at = utcnow()
        session.add(model)

    was_live_recommended = (
        maintain_revision_invariant
        and file_row.file_type == FileType.GCODE
        and file_row.deleted_at is None
        and file_row.is_recommended
    )
    if was_live_recommended:
        file_row.is_recommended = False
        session.add(file_row)
        session.flush()
        replacement = session.exec(
            select(File)
            .where(
                File.model_id == file_row.model_id,
                File.id != file_id,
                File.file_type == FileType.GCODE,
                live(File),
            )
            .order_by(File.version.desc())  # type: ignore[attr-defined]
        ).first()
        if replacement is not None:
            replacement.is_recommended = True
            session.add(replacement)

    session.exec(delete(PrinterFile).where(PrinterFile.file_id == file_id))
    session.exec(delete(PrintJob).where(PrintJob.file_id == file_id))
    session.exec(delete(Metadata).where(Metadata.file_id == file_id))
    session.delete(file_row)


def hard_delete_document(session: Session, document: Document) -> None:
    """Permanently remove a Document row and every vault-owned blob."""
    if document.id is None:
        return
    backend = get_backend()
    if document.filename:
        backend.delete(backend.document_file_key(document.id, document.filename))
    for document_id, name in _DOCUMENT_IMAGE_RE.findall(document.body or ""):
        if int(document_id) == document.id:
            backend.delete(backend.document_image_key(document.id, name))
    session.delete(document)


def restore_document(session: Session, document: Document) -> None:
    document.deleted_at = None
    document.deleted_by = None
    document.updated_at = utcnow()
    session.add(document)


def hard_delete_collection(session: Session, collection: Collection) -> None:
    """Permanently remove a Collection and its namespaced readme images."""
    if collection.id is None:
        return
    backend = get_backend()
    prefix = backend.collection_image_key(collection.id, "")
    for key in backend.walk_keys(prefix):
        backend.delete(key)
    session.delete(collection)


def hard_delete_model(session: Session, model: Model) -> None:
    """Permanently remove a model, related DB rows, and stored blobs."""
    if model.id is None:
        return

    file_rows = session.exec(select(File).where(File.model_id == model.id)).all()
    model.thumbnail_file_id = None
    model.thumbnail_path = None
    session.add(model)
    session.flush()
    for file_row in file_rows:
        hard_delete_file(session, file_row, maintain_revision_invariant=False)
    session.flush()

    # Don't bulk-delete the tag links here: ``Model.tags`` is a link_model
    # (many-to-many) relationship, so deleting the model already removes its
    # ModelTagLink rows. Doing both makes the ORM's cascade try to delete rows
    # this manual DELETE already removed -> StaleDataError on commit (purging any
    # *tagged* model, including the expired-trash cron, would 500).
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
    snapshot = ownership_snapshot(session)
    protected = snapshot.claimed_keys | {blob.key for blob in snapshot.external}
    removed = 0
    for key in snapshot.discovered_keys:
        if key not in protected:
            backend.delete(key)
            removed += 1
    return removed


def gc_soft_deleted(retention_days: int | None = None) -> dict[str, int]:
    """Hourly GC: purge expired trash rows across all soft-deletable tables,
    then sweep orphaned blobs.

    No-ops while a backup restore is in progress — restore replaces the DB
    file and disposes the engine, so a GC pass racing it would run queries
    against a database that no longer matches its connection.
    """
    from app.services.backup import restore_in_progress

    if restore_in_progress():
        logger.info("gc skipped: backup restore in progress")
        return {"rows": 0, "orphan_blobs": 0}
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
        expired_documents = session.exec(
            select(Document).where(
                trashed(Document),
                Document.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        for document in expired_documents:
            hard_delete_document(session, document)
        purged["rows"] += len(expired_documents)
        expired_files = session.exec(
            select(File).where(
                trashed(File),
                File.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        for file_row in expired_files:
            hard_delete_file(session, file_row)
        purged["rows"] += len(expired_files)
        expired_collections = session.exec(
            select(Collection).where(
                trashed(Collection),
                Collection.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        for collection in expired_collections:
            hard_delete_collection(session, collection)
        purged["rows"] += len(expired_collections)
        for model in (Tag, Printer, User):
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
