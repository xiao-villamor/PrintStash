"""Library-wide image preparation counts, including work not yet discovered.

One task produces one cover image or one multi-angle set for an Artifact.
Counts describe the current recipes of enabled producers, not the bounded Job
queue. Retried failures stay remaining; exhausted failures never look complete.
"""

from sqlalchemy import and_, case, func, or_, true
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import ensure_utc
from app.db.models import ArtifactDerivative, DerivativeKind, DerivativeState, File
from app.db.scopes import live
from app.schemas.jobs import PreviewActivity, PreviewActivityFailure

from . import policy
from .kinds import GROUPS
from .records import regenerations


def preview_activity(session: Session) -> PreviewActivity:
    controls = policy.resolve(session)
    total = completed = skipped = failed = 0
    latest_failure: PreviewActivityFailure | None = None
    for group in GROUPS:
        if not controls[group.definition].enabled:
            continue
        requested = regenerations(session, definitions=[group.definition])
        for kind in group.kinds:
            if kind not in (DerivativeKind.THUMBNAIL, DerivativeKind.PREVIEWS):
                continue
            d = ArtifactDerivative
            recipe_matches = and_(
                col(d.file_id) == col(File.id),
                col(d.kind) == kind,
                col(d.recipe_version) == group.recipe_column(kind),
            )
            eligible = (live(File), group.applies())
            exhausted = and_(
                col(d.state) == DerivativeState.FAILED,
                col(d.attempts) >= settings.derivative_max_attempts,
            )
            regenerated_at = requested.get(kind)
            current = (
                true()
                if regenerated_at is None
                else col(d.updated_at) >= regenerated_at
            )
            counts = session.exec(
                select(
                    func.count(col(File.id)),
                    func.count(
                        case((and_(col(d.state) == DerivativeState.READY, current), 1))
                    ),
                    func.count(
                        case(
                            (
                                or_(
                                    and_(
                                        col(d.state) == DerivativeState.SKIPPED, current
                                    ),
                                    col(d.state) == DerivativeState.CANCELLED,
                                ),
                                1,
                            )
                        )
                    ),
                    func.count(case((exhausted, 1))),
                )
                .select_from(File)
                .outerjoin(d, recipe_matches)
                .where(*eligible)
            ).one()
            total += counts[0]
            completed += counts[1]
            skipped += counts[2]
            failed += counts[3]
            if counts[3]:
                newest = session.exec(
                    select(d.failure_reason, d.updated_at)
                    .select_from(File)
                    .join(d, recipe_matches)
                    .where(*eligible, exhausted)
                    .order_by(col(d.updated_at).desc(), col(d.id).desc())
                    .limit(1)
                ).first()
                if newest is not None:
                    reason, updated_at = newest
                    assert reason is not None
                    at = ensure_utc(updated_at)
                    if latest_failure is None or at > latest_failure.at:
                        latest_failure = PreviewActivityFailure(reason=reason, at=at)
    return PreviewActivity(
        total=total,
        completed=completed,
        remaining=total - completed - skipped - failed,
        skipped=skipped,
        failed=failed,
        latest_failure=latest_failure,
    )
