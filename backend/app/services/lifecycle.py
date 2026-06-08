from __future__ import annotations

from datetime import timedelta

from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import Category, File, Printer, Tag, User
from app.db.session import get_session_factory
from app.services.storage_backend import get_backend
from app.services.trash import hard_delete_expired_models

logger = get_logger(__name__)

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
        for model in (File, Tag, Category, Printer, User):
            result = session.exec(
                delete(model).where(
                    model.deleted_at.is_not(None),  # type: ignore[attr-defined]
                    model.deleted_at < cutoff,  # type: ignore[attr-defined]
                )
            )
            purged["rows"] += int(result.rowcount or 0)
        session.commit()
        purged["orphan_blobs"] = _cleanup_orphan_blobs(session)
    logger.info("gc complete: rows=%s orphan_blobs=%s", purged["rows"], purged["orphan_blobs"])
    return purged
