"""Bound foreground preference so old optional work always gets a turn.

Queue age is durable and survives scheduler restarts. This is only admission
policy: each capability still owns its claim, compute permits and retry rules.
"""

from datetime import timedelta
from typing import Literal

from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import ArtifactAnalysisGeneration, SimilarityRun, ThumbnailGeneration
from app.modules.ingestion.commands import foreground_pending

MAX_BACKGROUND_WAIT_SECONDS = 60


def defer_background(
    session: Session, capability: Literal["enrichment", "similarity"]
) -> bool:
    if not foreground_pending(session):
        return False
    cutoff = utcnow() - timedelta(seconds=MAX_BACKGROUND_WAIT_SECONDS)
    if capability == "similarity":
        aged = session.exec(
            select(SimilarityRun.id)
            .where(
                col(SimilarityRun.state).in_(("queued", "running")),
                SimilarityRun.created_at <= cutoff,
            )
            .limit(1)
        ).first()
        return aged is None
    aged = session.exec(
        select(ArtifactAnalysisGeneration.id)
        .where(
            col(ArtifactAnalysisGeneration.state).in_(("pending", "running")),
            ArtifactAnalysisGeneration.created_at <= cutoff,
            ArtifactAnalysisGeneration.next_attempt_at <= utcnow(),
        )
        .limit(1)
    ).first()
    if aged is not None:
        return False
    return (
        session.exec(
            select(ThumbnailGeneration.id)
            .where(
                col(ThumbnailGeneration.state).in_(("pending", "running")),
                ThumbnailGeneration.processing_policy == "background",
                ThumbnailGeneration.created_at <= cutoff,
            )
            .limit(1)
        ).first()
        is None
    )
