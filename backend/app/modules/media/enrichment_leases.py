"""Keep a shared media compute permit and its output claims alive together."""
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread

from sqlalchemy import update
from sqlmodel import Session

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    ArtifactAnalysisGeneration,
    ThumbnailGeneration,
    ThumbnailRenderSlot,
)
from app.db.session import SessionFactory

logger = get_logger(__name__)
LEASE_SECONDS = 900


def renew(session: Session, slot_id: int, token: str, metadata, preview) -> bool:
    now = utcnow()
    owners = [(ThumbnailRenderSlot, slot_id, token)]
    if metadata:
        owners.append((ArtifactAnalysisGeneration, metadata.generation_id, metadata.token))
    if preview:
        owners.append((ThumbnailGeneration, preview.generation_id, preview.token))
    for model, identity, claim_token in owners:
        result = session.connection().execute(update(model).where(
            model.id == identity, model.lease_token == claim_token,
            model.lease_expires_at > now,
        ).values(lease_expires_at=now + timedelta(seconds=LEASE_SECONDS)))
        if result.rowcount != 1:
            session.rollback()
            return False
    session.commit()
    return True


@contextmanager
def keep_alive(sessions: SessionFactory, slot_id: int, token: str, metadata, preview):
    """No SQL session crosses threads or survives a heartbeat transaction."""
    stopped = Event()

    def heartbeat():
        while not stopped.wait(30):
            try:
                with sessions.scoped_session() as session:
                    if not renew(session, slot_id, token, metadata, preview):
                        return
            except Exception:
                # Output publication remains fenced by unexpired leases. Retry a
                # transient database failure while the original lease is alive.
                logger.warning("Could not renew enrichment ownership")

    thread = Thread(target=heartbeat, name="enrichment-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()
