"""Versioned media work builders use the Artifact's actual source identity."""

from typing import Any

from sqlmodel import Session

from app.db.models import ArtifactAnalysisGeneration, File, ThumbnailGeneration
from app.modules.media.analysis_generations import RECIPE
from app.modules.media.thumbnail_generations import recipe_fingerprint
from tests.factories._support import save


def build_artifact_analysis(session: Session, file: File, **overrides: Any) -> ArtifactAnalysisGeneration:
    defaults = {"file_id": file.id, "source_sha256": file.sha256, "recipe": RECIPE}
    return save(session, ArtifactAnalysisGeneration(**(defaults | overrides)))


def build_thumbnail_generation(session: Session, file: File, **overrides: Any) -> ThumbnailGeneration:
    defaults = {"file_id": file.id, "source_sha256": file.sha256,
                "recipe_fingerprint": recipe_fingerprint(), "processing_policy": "background"}
    return save(session, ThumbnailGeneration(**(defaults | overrides)))
