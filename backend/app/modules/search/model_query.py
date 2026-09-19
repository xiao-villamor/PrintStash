"""Model-as-query reads current authorized vectors and requests bounded missing work."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass

from printstash_core.inference import EmbeddingError
from printstash_core.inference.vectors import normalize
from sqlalchemy import update
from sqlmodel import select

from app.db.models import IndexGeneration, Model, PassageVector
from app.modules.library.model_views.access import accessible_live_model_ids_stmt


@dataclass(frozen=True)
class ModelQuery:
    model_id: int


def require_visible(session, user, model_id):
    if (
        not user.is_active
        or session.exec(
            accessible_live_model_ids_stmt(session, user).where(Model.id == model_id)
        ).first()
        is None
    ):
        raise EmbeddingError("search_model_unavailable")


def existing(session, user, query: ModelQuery, leg, allowed):
    require_visible(session, user, query.model_id)
    rows = session.exec(
        select(PassageVector)
        .where(
            PassageVector.id.in_(allowed),
            PassageVector.subject_type == "model",
            PassageVector.subject_id == query.model_id,
        )
        .order_by(PassageVector.id)
        .limit(129)
    ).all()
    if len(rows) > 128:
        raise EmbeddingError("search_model_query_budget")
    if not rows:
        # There is already one durable worker for this generation. Make its
        # missing-unit scan eligible; never create a queue/job per query.
        session.exec(
            update(IndexGeneration)
            .where(
                IndexGeneration.id == leg.generation_id,
                IndexGeneration.state == "active",
            )
            .values(phase="backfill", passage_after_id=0)
        )
        session.commit()
        raise EmbeddingError("search_model_index_pending")
    vectors = tuple(
        struct.unpack(f"<{leg.space.dimension}f", row.vector_blob) for row in rows
    )
    mean = (sum(column) / len(vectors) for column in zip(*vectors, strict=True))
    vector = struct.unpack(
        f"<{leg.space.dimension}f", normalize(mean, leg.space.dimension)
    )
    return vector, tuple((row.id, row.input_hash) for row in rows)


def fingerprint(session, user, model_id, legs, types):
    from app.modules.search import query_context, visual_sources

    require_visible(session, user, model_id)
    state = []
    for leg in legs:
        allowed = (
            visual_sources.current_vectors(session, leg.generation_id, leg.space, user)
            if leg.space.profile in visual_sources.PROFILES
            else query_context.allowed_vectors(session, user, leg, types)
        )
        rows = session.exec(
            select(PassageVector.id, PassageVector.input_hash)
            .where(
                PassageVector.id.in_(allowed),
                PassageVector.subject_type == "model",
                PassageVector.subject_id == model_id,
            )
            .order_by(PassageVector.id)
            .limit(129)
        ).all()
        if len(rows) > 128:
            raise EmbeddingError("search_model_query_budget")
        state.append((leg.generation_id, [tuple(row) for row in rows]))
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()


def unchanged(session, user, query: ModelQuery, snapshot, allowed) -> bool:
    require_visible(session, user, query.model_id)
    rows = session.exec(
        select(PassageVector.id, PassageVector.input_hash).where(
            PassageVector.id.in_(allowed),
            PassageVector.id.in_([id for id, _ in snapshot]),
            PassageVector.subject_id == query.model_id,
            PassageVector.subject_type == "model",
        )
    ).all()
    return set(rows) == set(snapshot)
