"""Post-print outcome helpers shared by the live printer hub and history import.

Keeps the "what a finished print means for a revision" logic in one place:
material lookup for filament-mass derivation, and the auto-promotion of a
revision to ``known_good`` after a successful print.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.db.models import File, FileRevisionStatus, Metadata

logger = get_logger(__name__)


def material_type_for_file(session: Session, file_id: int) -> str | None:
    """Material type recorded on the printed file's metadata, if any."""
    meta = session.exec(select(Metadata).where(Metadata.file_id == file_id)).first()
    return meta.material_type if meta else None


def mark_known_good_if_eligible(session: Session, file_id: int) -> bool:
    """Promote a revision to known_good after a successful print.

    Only promotes when the status is unset or ``needs_test`` — a human's
    ``failed``/``archived`` verdict is never overridden. Returns True if it
    changed the status. The caller is responsible for the feature toggle.
    """
    f = session.get(File, file_id)
    if f is None or f.deleted_at is not None:
        return False
    if f.revision_status in (None, FileRevisionStatus.NEEDS_TEST):
        f.revision_status = FileRevisionStatus.KNOWN_GOOD
        session.add(f)
        session.commit()
        logger.info("auto-marked file %s known_good after successful print", file_id)
        return True
    return False
