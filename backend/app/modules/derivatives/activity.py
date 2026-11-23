"""Library-wide image preparation counts, including work not yet discovered.

One task produces one cover image or one multi-angle set for an Artifact.
Counts describe the current recipes of enabled producers, not the bounded Job
queue. Retried failures stay remaining; exhausted failures never look complete.
"""

from sqlalchemy import and_, case, func, or_, true
from sqlmodel import Session, col, select

from app.core.config import settings
from app.db.models import ArtifactDerivative, DerivativeKind, DerivativeState, File
from app.db.scopes import live
from app.schemas.jobs import PreviewActivity

from . import policy
from .kinds import GROUPS
from .records import regenerations


def preview_activity(session: Session) -> PreviewActivity:
    controls = policy.resolve(session)
    total = completed = skipped = failed = 0
    for group in GROUPS:
        if not controls[group.definition].enabled:
            continue
        requested = regenerations(session, definitions=[group.definition])
        for kind in group.kinds:
            if kind not in (DerivativeKind.THUMBNAIL, DerivativeKind.PREVIEWS):
                continue
            d = ArtifactDerivative
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
                    func.count(
                        case(
                            (
                                and_(
                                    col(d.state) == DerivativeState.FAILED,
                                    col(d.attempts) >= settings.derivative_max_attempts,
                                ),
                                1,
                            )
                        )
                    ),
                )
                .select_from(File)
                .outerjoin(
                    d,
                    and_(
                        col(d.file_id) == col(File.id),
                        col(d.kind) == kind,
                        col(d.recipe_version) == group.recipe_column(kind),
                    ),
                )
                .where(live(File), group.applies())
            ).one()
            total += counts[0]
            completed += counts[1]
            skipped += counts[2]
            failed += counts[3]
    return PreviewActivity(
        total=total,
        completed=completed,
        remaining=total - completed - skipped - failed,
        skipped=skipped,
        failed=failed,
    )
