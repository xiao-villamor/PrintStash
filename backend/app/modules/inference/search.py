"""Authorized semantic neighbors in one native Space, distinct from geometry evidence."""

from __future__ import annotations

from printstash_core.inference import EmbeddingError, EmbeddingInput
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import File, Model, PassageVector, User
from app.db.session import SessionFactory
from app.modules.inference import store
from app.modules.inference.local import configured_provider
from app.modules.similarity import candidates, runs
from app.modules.similarity.configuration import read_settings
from app.modules.similarity.fingerprints import live_source_predicates


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, min_length=1, max_length=4096)
    model_id: int | None = Field(default=None, gt=0, le=2**63 - 1, strict=True)
    space_id: int | None = Field(default=None, gt=0, le=2**63 - 1, strict=True)
    limit: int = Field(default=20, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def exactly_one_query(self):
        if (self.text is None) == (self.model_id is None) or (
            self.text is not None and not self.text.strip()
        ):
            raise ValueError("one_text_or_model_query_required")
        return self


def search(
    session: Session, sessions: SessionFactory, actor: User, request: SearchRequest
) -> dict:
    import numpy as np

    config = read_settings(session)
    if not config.enabled or not config.embeddings_enabled:
        raise OperationError("embedding_disabled", kind=ErrorKind.CONFLICT)
    if request.model_id is not None:
        runs.normalize_scope(session, actor, "models", [request.model_id])
    try:
        provider = configured_provider(sessions)
        generation = store.active_generation(session, provider.space)
        if generation is None:
            generation_id = store.initialize(sessions, provider)
            generation = store.active_generation(session, provider.space)
        else:
            generation_id = generation.id
        if generation is None or generation_id is None:
            raise EmbeddingError("embedding_generation_unavailable")
        if request.space_id is not None and request.space_id != generation.space_id:
            raise EmbeddingError("embedding_space_mismatch")
        if request.text is not None:
            vector = provider.embed(
                (EmbeddingInput("text", text=request.text),), provider.space
            )[0]
        else:
            entry = session.exec(
                select(PassageVector)
                .join(File, File.id == PassageVector.file_id)
                .join(Model, Model.id == File.model_id)
                .where(
                    PassageVector.generation_id == generation_id,
                    PassageVector.model_id == request.model_id,
                    PassageVector.unit_kind == "mesh_artifact",
                    PassageVector.input_hash == File.sha256,
                    *live_source_predicates(),
                )
                .order_by(PassageVector.id)
                .limit(1)
            ).first()
            if entry is None:
                return {
                    "items": [],
                    "evidence_kind": "semantic",
                    "space_id": generation.space_id,
                    "index_state": "missing_model_vectors",
                    "scanned": 0,
                    "truncated": False,
                }
            vector = tuple(
                float(value) for value in np.frombuffer(entry.vector_blob, dtype="<f4")
            )
        result = store.query(
            session,
            actor,
            generation_id=generation_id,
            space=provider.space,
            vector=vector,
            limit=request.limit,
            exclude_model_id=request.model_id,
        )
        references = candidates.model_references(
            session, {item.subject_id for item in result.items}
        )
        return {
            "items": [
                {
                    "model": references[item.subject_id],
                    "score": item.score,
                    "evidence_kind": "semantic",
                }
                for item in result.items
            ],
            "evidence_kind": "semantic",
            "space_id": generation.space_id,
            "index_state": "ready" if result.scanned else "empty",
            "scanned": result.scanned,
            "truncated": result.truncated,
        }
    except EmbeddingError as exc:
        raise OperationError(exc.code, kind=ErrorKind.CONFLICT) from exc


def capabilities(session: Session, sessions: SessionFactory) -> dict:
    """Availability follows a validated generation, never module discovery alone."""
    import importlib.util

    try:
        if importlib.util.find_spec("onnxruntime") is None:
            raise EmbeddingError("embedding_runtime_unavailable")
        provider = configured_provider(sessions)
        if (
            provider.space.modality == "text_image"
            and importlib.util.find_spec("tokenizers") is None
        ):
            raise EmbeddingError("embedding_runtime_unavailable")
        generation = store.active_generation(session, provider.space)
    except EmbeddingError as exc:
        return {
            "local_embeddings": False,
            "text_to_shape": False,
            "embedding_reason": exc.code,
        }
    if generation is None:
        return {
            "local_embeddings": False,
            "text_to_shape": False,
            "embedding_reason": "embedding_not_validated",
        }
    return {
        "local_embeddings": True,
        "text_to_shape": provider.space.modality == "text_image",
        "embedding_reason": None,
    }
