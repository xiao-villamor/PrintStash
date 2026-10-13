"""On-demand STL previews: durable demand and production in a supervised Job."""

from __future__ import annotations

from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import DerivativeKind, File, JobKind

from . import policy, records
from .kinds import VIEWER_STL_RECIPE


def request(session: Session, file: File) -> None:
    """Persist demand once; polling never resets a failure or starts native work."""
    from app.modules.work import nudge

    policy.lock(session)
    policy.require_enabled(session, JobKind.DERIVATIVES_VIEWER_STL)
    session.refresh(file)
    if file.viewer_requested_at is None:
        file.viewer_requested_at = utcnow()
        session.add(file)
    needed = records.needed(
        session, file, {DerivativeKind.VIEWER_STL: VIEWER_STL_RECIPE}, now=utcnow()
    )
    # Release the request connection before the hint opens its own Session.
    # The persisted demand remains authoritative if the hint is lost.
    session.commit()
    if needed:
        nudge(JobKind.DERIVATIVES_VIEWER_STL)
