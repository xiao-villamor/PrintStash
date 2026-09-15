"""Authorized, pinned semantic query legs; provider work never holds a DB lock."""

from __future__ import annotations

import json

from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.inference import EmbeddingSpace as Space
from printstash_core.search.passages import SubjectType
from sqlalchemy import or_
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from app.db.models import (
    EmbeddingSpace,
    IndexGeneration,
    PassageVector,
    User,
)
from app.modules.inference.configuration import embedding_provider
from app.modules.inference.query import runner
from app.modules.search import (
    configuration,
    generations,
    model_query,
    vector_store,
    visual_sources,
)
from app.modules.search.query_context import (
    LegResult,
    SemanticLeg,
    allowed_vectors,
    authorization_context,
)
from app.modules.search.text_inputs import TextRecipe
from app.schemas.inference import SearchSettings

# Measured against the pinned B/32 towers and frozen visual corpus. CLIP cosine
# scores are not on the text encoder's scale. Admin per-Space overrides win.
_CLIP_B32 = "7f56e23951620776f8a65d4de5441b6ff1eecd1f48c8ddf1eca8a82f1dea2089"
_OPENSHAPE_B32 = "4f845433d48ed5f38609b1538118ea8761405d528f750eb07784fbb795ffe448"


def score_floor(space: Space, settings: SearchSettings) -> float:
    default = (
        0.1
        if space.config_hash == _OPENSHAPE_B32
        else 0.2
        if space.profile in ("thumbnail", "multiview")
        and space.alignment_identity == _CLIP_B32
        else settings.semantic_floor
    )
    return settings.semantic_floors.get(space.config_hash, default)


def registry(session: Session, settings: SearchSettings) -> tuple[SemanticLeg, ...]:
    if not settings.enabled:
        return ()
    rows = session.exec(
        select(IndexGeneration, EmbeddingSpace)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            IndexGeneration.state == "active",
            EmbeddingSpace.profile.in_(("semantic_text", *visual_sources.PROFILES)),
        )
        .order_by(IndexGeneration.id)
        .limit(4)
    ).all()
    return tuple(
        SemanticLeg(
            space.profile,
            generation.id,
            Space(**json.loads(space.config_json)),
            score_floor(Space(**json.loads(space.config_json)), settings),
            settings.semantic_weight,
            settings.query_timeout_seconds,
        )
        for generation, space in rows
    )


def retrieve(
    session: Session,
    user_id: int,
    auth_version: int,
    query: str | EmbeddingInput | model_query.ModelQuery,
    leg: SemanticLeg,
    *,
    types: tuple[SubjectType, ...],
    filters=None,
) -> LegResult:
    """Read-only search owns this session's transactions, including its lease.

    Authorization gates both egress and scoring. The final materializer repeats
    the same authorization fence so a contributor never leaks through evidence.
    """
    if leg.space.profile in visual_sources.PROFILES:
        from app.modules.search.visual_query import retrieve as retrieve_visual

        return retrieve_visual(
            session, user_id, auth_version, query, leg, types=types, filters=filters
        )
    lease = None
    try:
        user = session.get(User, user_id, populate_existing=True)
        if (
            user is None
            or not user.is_active
            or user.auth_version != auth_version
            or not configuration.settings(session).enabled
        ):
            return LegResult(leg, available=False)
        allowed = allowed_vectors(session, user, leg, types, filters)
        if (
            not isinstance(query, model_query.ModelQuery)
            and session.exec(allowed.limit(1)).first() is None
        ):
            return LegResult(leg)
        authorization = authorization_context(session, user)
        source_snapshot = None
        if isinstance(query, model_query.ModelQuery):
            vector, source_snapshot = model_query.existing(
                session, user, query, leg, allowed
            )
        else:
            if not isinstance(query, str):
                raise EmbeddingError("embedding_image_unavailable")
            provider = embedding_provider(session, leg.space)
            recipe = TextRecipe.for_space(leg.space)
            budget = recipe.max_input_characters - len(leg.space.query_prefix)
            if len(query) > budget:
                raise EmbeddingError("embedding_input_limit_exceeded")
            value = EmbeddingInput("text", text=leg.space.query_prefix + query)
        lease = generations.pin(session, leg.generation_id)
        # End even a read transaction before external work; edits/cutover can
        # proceed while this request waits in the bounded inference executor.
        session.rollback()
        if source_snapshot is None:
            vector = runner().embed(
                provider,
                leg.space,
                value,
                authorization=authorization,
                seconds=leg.timeout,
            )
        session.expire_all()
        user = session.get(User, user_id, populate_existing=True)
        if (
            user is None
            or not user.is_active
            or user.auth_version != auth_version
            or not configuration.settings(session).enabled
        ):
            return LegResult(leg, available=False)
        for attempt in range(2):
            allowed = allowed_vectors(session, user, leg, types, filters)
            if isinstance(query, model_query.ModelQuery):
                if not model_query.unchanged(
                    session, user, query, source_snapshot, allowed
                ):
                    raise EmbeddingError("search_model_index_pending")
                allowed = allowed.where(
                    or_(
                        PassageVector.subject_type != "model",
                        PassageVector.subject_id != query.model_id,
                    )
                )
            result = vector_store.query(
                session,
                generation_id=leg.generation_id,
                space=leg.space,
                vector=vector,
                allowed_ids=allowed,
                limit=leg.candidate_limit,
                max_scan=leg.scan_limit,
                states=("active", "retired"),
            )
            ids = [item.unit_id for item in result.items if item.score >= leg.floor]
            passages = (
                dict(
                    session.exec(
                        select(PassageVector.id, PassageVector.passage_id).where(
                            PassageVector.id.in_(ids),
                            PassageVector.id.in_(
                                allowed_vectors(
                                    session, user, leg, types, filters
                                ).where(PassageVector.id.in_(ids))
                            ),
                        )
                    ).all()
                )
                if ids
                else {}
            )
            if len(passages) == len(ids) or attempt == 1:
                return LegResult(
                    leg,
                    tuple(passages[id] for id in ids if id in passages),
                    truncated=result.truncated
                    or len(result.items) == leg.candidate_limit,
                    weak_matches=bool(result.items) and not ids,
                )
            session.rollback()
            session.expire_all()
            user = session.get(User, user_id, populate_existing=True)
            if user is None or not user.is_active or user.auth_version != auth_version:
                return LegResult(leg, available=False)
        raise AssertionError("bounded retrieval loop")
    except (EmbeddingError, DBAPIError, ValueError, TypeError) as exc:
        session.rollback()
        return LegResult(
            leg,
            degraded="search_semantic_unavailable",
            available=False,
            error_code=exc.code
            if isinstance(exc, EmbeddingError)
            else "search_store_unavailable",
        )
    finally:
        if lease:
            generations.unpin(session, lease)
