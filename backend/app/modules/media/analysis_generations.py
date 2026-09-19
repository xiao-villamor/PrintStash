"""Durable metadata requests and fenced publication for committed Artifacts.

Registration belongs to the source transaction. Claims and output updates are
short independent transactions; no parser, renderer or storage read runs here.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, or_, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import ArtifactAnalysisGeneration, File, Metadata, Model
from app.db.projections import content_changed
from app.db.scopes import live
from app.modules.media.thumbnail_generations import request_thumbnail

RECIPE = "metadata-v1"
LEASE_SECONDS = 900


@dataclass(frozen=True)
class AnalysisClaim:
    generation_id: int
    token: str
    file_id: int
    source_sha256: str
    preserve_metadata: bool
    actor_user_id: int | None


def request_enrichment(
    session: Session,
    file: File,
    *,
    promote_thumbnail: bool,
    preserve_metadata: bool = False,
    actor_user_id: int | None = None,
) -> None:
    """Atomically register requested outputs; the caller owns commit/rollback."""
    assert file.id is not None
    existing = session.exec(
        select(ArtifactAnalysisGeneration).where(
            ArtifactAnalysisGeneration.file_id == file.id,
            ArtifactAnalysisGeneration.source_sha256 == file.sha256,
            ArtifactAnalysisGeneration.recipe == RECIPE,
        )
    ).first()
    if existing is None:
        session.add(
            ArtifactAnalysisGeneration(
                file_id=file.id,
                source_sha256=file.sha256,
                recipe=RECIPE,
                preserve_metadata=preserve_metadata,
                actor_user_id=actor_user_id,
            )
        )
    request_thumbnail(
        session, file, promote=promote_thumbnail, policy=settings.thumbnail_processing
    )
    session.flush()


def claim_next(session: Session) -> AnalysisClaim | None:
    """Claim oldest eligible live work; expired work needs no startup rewrite."""
    now = utcnow()
    eligible = (
        ArtifactAnalysisGeneration.recipe == RECIPE,
        col(ArtifactAnalysisGeneration.state).in_(("pending", "running")),
        ArtifactAnalysisGeneration.next_attempt_at <= now,
        or_(
            col(ArtifactAnalysisGeneration.lease_expires_at).is_(None),
            ArtifactAnalysisGeneration.lease_expires_at <= now,
        ),
    )
    generation = session.exec(
        select(ArtifactAnalysisGeneration)
        .join(File, File.id == ArtifactAnalysisGeneration.file_id)
        .join(Model, Model.id == File.model_id)
        .where(
            *eligible,
            File.sha256 == ArtifactAnalysisGeneration.source_sha256,
            live(File),
            live(Model),
        )
        .order_by(
            ArtifactAnalysisGeneration.next_attempt_at, ArtifactAnalysisGeneration.id
        )
        .limit(1)
    ).first()
    if generation is None:
        return None
    token = secrets.token_hex(32)
    changed = session.connection().execute(
        update(ArtifactAnalysisGeneration)
        .where(col(ArtifactAnalysisGeneration.id) == generation.id, *eligible)
        .values(
            state="running",
            lease_token=token,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            attempts=ArtifactAnalysisGeneration.attempts + 1,
            updated_at=now,
        )
    )
    session.commit()
    if changed.rowcount != 1:
        return None
    session.refresh(generation)
    return AnalysisClaim(
        generation.id,
        token,
        generation.file_id,
        generation.source_sha256,
        generation.preserve_metadata,
        generation.actor_user_id,
    )


def _lock_current(session: Session, claim: AnalysisClaim) -> File | None:
    locked = session.connection().execute(
        update(ArtifactAnalysisGeneration)
        .where(
            col(ArtifactAnalysisGeneration.id) == claim.generation_id,
            ArtifactAnalysisGeneration.recipe == RECIPE,
            ArtifactAnalysisGeneration.lease_token == claim.token,
            ArtifactAnalysisGeneration.lease_expires_at > utcnow(),
        )
        .values(lease_token=claim.token)
    )
    if locked.rowcount != 1:
        return None
    return session.exec(
        select(File)
        .join(Model, Model.id == File.model_id)
        .where(
            File.id == claim.file_id,
            File.sha256 == claim.source_sha256,
            live(File),
            live(Model),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def publish_metadata(
    session: Session, claim: AnalysisClaim, values: dict[str, Any]
) -> bool:
    """Stage current metadata and search notification without overwriting supplied facts."""
    file = _lock_current(session, claim)
    if file is None:
        session.rollback()
        return False
    metadata = session.exec(select(Metadata).where(Metadata.file_id == file.id)).first()
    if metadata is None:
        metadata = Metadata(file_id=file.id)
    if not claim.preserve_metadata:
        for field, value in values.items():
            if field in Metadata.model_fields and field not in {
                "id",
                "file_id",
                "created_at",
            }:
                setattr(metadata, field, value)
        session.add(metadata)
    model = session.get(Model, file.model_id)
    assert model is not None
    model.updated_at = utcnow()
    session.add(model)
    content_changed(session, "model", [model.id])
    session.commit()
    return True


def finish_analysis(session: Session, claim: AnalysisClaim) -> bool:
    if _lock_current(session, claim) is None:
        session.rollback()
        return False
    session.execute(
        update(ArtifactAnalysisGeneration)
        .where(
            col(ArtifactAnalysisGeneration.id) == claim.generation_id,
            ArtifactAnalysisGeneration.lease_token == claim.token,
        )
        .values(
            state="ready",
            lease_token=None,
            lease_expires_at=None,
            error_code=None,
            updated_at=utcnow(),
            finished_at=utcnow(),
        )
    )
    session.commit()
    return True


def fail_analysis(
    session: Session, claim: AnalysisClaim, reason: str, *, retryable: bool = True
) -> None:
    row = session.get(ArtifactAnalysisGeneration, claim.generation_id)
    if row is None:
        return
    retry = retryable and row.attempts < 5
    session.execute(
        update(ArtifactAnalysisGeneration)
        .where(
            col(ArtifactAnalysisGeneration.id) == claim.generation_id,
            ArtifactAnalysisGeneration.lease_token == claim.token,
        )
        .values(
            state="pending" if retry else "failed",
            lease_token=None,
            lease_expires_at=None,
            next_attempt_at=utcnow() + timedelta(seconds=min(2**row.attempts, 60)),
            error_code=reason,
            updated_at=utcnow(),
        )
    )
    session.commit()


def defer_analysis(session: Session, claim: AnalysisClaim, reason: str) -> None:
    """Resource admission never exhausts the retry budget for actual attempts."""
    session.execute(
        update(ArtifactAnalysisGeneration)
        .where(
            col(ArtifactAnalysisGeneration.id) == claim.generation_id,
            ArtifactAnalysisGeneration.lease_token == claim.token,
        )
        .values(
            state="pending",
            lease_token=None,
            lease_expires_at=None,
            attempts=ArtifactAnalysisGeneration.attempts - 1,
            next_attempt_at=utcnow() + timedelta(seconds=2),
            error_code=reason,
            updated_at=utcnow(),
        )
    )
    session.commit()


def retry_enrichment(
    session: Session, file: File, *, metadata: bool, thumbnail: bool, actor_id: int
) -> None:
    """Register missing work and retry failed outputs without stealing live claims."""
    from app.db.models import ThumbnailGeneration
    from app.modules.media.thumbnail_generations import recipe_fingerprint

    if (
        metadata
        and session.exec(
            select(ArtifactAnalysisGeneration).where(
                ArtifactAnalysisGeneration.file_id == file.id,
                ArtifactAnalysisGeneration.source_sha256 == file.sha256,
                ArtifactAnalysisGeneration.recipe == RECIPE,
            )
        ).first()
        is None
    ):
        session.add(
            ArtifactAnalysisGeneration(
                file_id=file.id,
                source_sha256=file.sha256,
                recipe=RECIPE,
                actor_user_id=actor_id,
            )
        )
    if thumbnail:
        request_thumbnail(session, file, promote=False, policy="background")
    session.flush()
    now = utcnow()
    if metadata:
        session.connection().execute(
            update(ArtifactAnalysisGeneration)
            .where(
                ArtifactAnalysisGeneration.file_id == file.id,
                ArtifactAnalysisGeneration.source_sha256 == file.sha256,
                ArtifactAnalysisGeneration.recipe == RECIPE,
                col(ArtifactAnalysisGeneration.state).in_(("failed", "pending")),
                or_(
                    col(ArtifactAnalysisGeneration.lease_expires_at).is_(None),
                    ArtifactAnalysisGeneration.lease_expires_at <= now,
                ),
            )
            .values(
                state="pending",
                attempts=0,
                next_attempt_at=now,
                error_code=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
    if thumbnail:
        session.connection().execute(
            update(ThumbnailGeneration)
            .where(
                ThumbnailGeneration.file_id == file.id,
                ThumbnailGeneration.source_sha256 == file.sha256,
                ThumbnailGeneration.recipe_fingerprint == recipe_fingerprint(),
                col(ThumbnailGeneration.state).in_(("failed", "pending")),
                or_(
                    col(ThumbnailGeneration.lease_expires_at).is_(None),
                    ThumbnailGeneration.lease_expires_at <= now,
                ),
            )
            .values(
                state="pending",
                processing_policy="background",
                attempts=0,
                failure_reason=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
    session.flush()
