"""Batched current-source readiness; no parser or materialization on read paths."""

from sqlalchemy import and_, union
from sqlmodel import Session, col, select

from app.db.models import (
    ArtifactAnalysisGeneration,
    BackgroundJob,
    File,
    InboxItem,
    ThumbnailGeneration,
)
from app.db.scopes import live
from app.modules.media.analysis_generations import RECIPE
from app.modules.media.thumbnail_generations import recipe_fingerprint
from app.schemas.enrichment import ArtifactEnrichmentRead


def states_for_files(session: Session, file_ids: list[int]) -> dict[int, ArtifactEnrichmentRead]:
    if not file_ids:
        return {}
    rows = session.exec(select(File.id, ArtifactAnalysisGeneration, ThumbnailGeneration)
        .outerjoin(ArtifactAnalysisGeneration, and_(
            ArtifactAnalysisGeneration.file_id == File.id,
            ArtifactAnalysisGeneration.source_sha256 == File.sha256,
            ArtifactAnalysisGeneration.recipe == RECIPE))
        .outerjoin(ThumbnailGeneration, and_(
            ThumbnailGeneration.file_id == File.id,
            ThumbnailGeneration.source_sha256 == File.sha256,
            ThumbnailGeneration.recipe_fingerprint == recipe_fingerprint()))
        .where(col(File.id).in_(file_ids), live(File))).all()
    result = {}
    for file_id, analysis, preview in rows:
        metadata_state = analysis.state if analysis else "unknown"
        if analysis and analysis.state == "pending" and analysis.error_code:
            metadata_state = "blocked"
        preview_state = preview.state if preview else "unknown"
        if preview and preview.state != "ready":
            if preview.processing_policy in {"on_demand", "disabled"}:
                preview_state = preview.processing_policy
            elif preview.failure_reason == "no_embedded_thumbnail":
                preview_state = "not_applicable"
            elif preview.state == "pending" and preview.failure_reason:
                preview_state = "blocked"
        result[file_id] = ArtifactEnrichmentRead(
            metadata=metadata_state, thumbnail=preview_state,
            metadata_error=analysis.error_code if analysis else None,
            thumbnail_error=preview.failure_reason if preview else None,
        )
    return result


def pending_models(session: Session, model_ids: list[int]) -> set[int]:
    if not model_ids:
        return set()
    metadata = select(File.model_id).join(ArtifactAnalysisGeneration, and_(
        ArtifactAnalysisGeneration.file_id == File.id,
        ArtifactAnalysisGeneration.source_sha256 == File.sha256))
    metadata = metadata.where(col(File.model_id).in_(model_ids), live(File),
        ArtifactAnalysisGeneration.recipe == RECIPE,
        col(ArtifactAnalysisGeneration.state).in_(("pending", "running")))
    previews = select(File.model_id).join(ThumbnailGeneration, and_(
        ThumbnailGeneration.file_id == File.id, ThumbnailGeneration.source_sha256 == File.sha256))
    previews = previews.where(col(File.model_id).in_(model_ids), live(File),
        ThumbnailGeneration.recipe_fingerprint == recipe_fingerprint(),
        ThumbnailGeneration.processing_policy == "background",
        col(ThumbnailGeneration.state).in_(("pending", "running")))
    pending = set(session.execute(union(metadata, previews)).scalars())
    # Capture work belongs to its source job, and may outlive Artifact analysis.
    from app.modules.ingestion.commands import capture_enrichment_job_id
    captured = session.exec(select(InboxItem.resulting_model_id, InboxItem.background_job_id).where(
        col(InboxItem.resulting_model_id).in_(model_ids), col(InboxItem.background_job_id).is_not(None),
        InboxItem.state == "completed",
    )).all()
    owners = {capture_enrichment_job_id(job_id): model_id for model_id, job_id in captured}
    if owners:
        active = session.exec(select(BackgroundJob.id).where(
            col(BackgroundJob.id).in_(owners), col(BackgroundJob.state).in_(("pending", "running")),
        )).all()
        pending.update(owners[job_id] for job_id in active)
    return pending
