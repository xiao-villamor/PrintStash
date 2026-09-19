"""Text/image queries use the paired native tower and freshly authorized mesh units."""

from __future__ import annotations

from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.search.passages import SubjectType
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from app.db.models import PassageVector, User
from app.modules.inference.configuration import embedding_provider
from app.modules.inference.query import runner
from app.modules.search import (
    configuration,
    generations,
    model_query,
    structured,
    vector_store,
    visual_sources,
)
from app.modules.search.query_context import LegResult, authorization_context


def retrieve(session, user_id, auth_version, query, leg, *, types, filters=None):
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
        if SubjectType.MODEL not in types:
            return LegResult(leg)
        if leg.space.provider != "onnx_cpu":
            # No remote image wire format is guessed from a text-only endpoint.
            raise EmbeddingError("embedding_image_unavailable")
        allowed = structured.vectors(
            visual_sources.current_vectors(session, leg.generation_id, leg.space, user),
            session,
            user,
            filters,
        )
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
            value = (
                query
                if isinstance(query, EmbeddingInput)
                else EmbeddingInput("text", text=query)
            )
            provider = embedding_provider(session, leg.space)
        lease = generations.pin(session, leg.generation_id)
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
        allowed = structured.vectors(
            visual_sources.current_vectors(session, leg.generation_id, leg.space, user),
            session,
            user,
            filters,
        )
        if isinstance(query, model_query.ModelQuery):
            if not model_query.unchanged(
                session, user, query, source_snapshot, allowed
            ):
                raise EmbeddingError("search_model_index_pending")
            allowed = allowed.where(PassageVector.subject_id != query.model_id)
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
        current = (
            {
                row.id: row
                for row in session.exec(
                    select(PassageVector).where(
                        PassageVector.id.in_(ids),
                        PassageVector.id.in_(
                            structured.vectors(
                                visual_sources.current_vectors(
                                    session, leg.generation_id, leg.space, user
                                ),
                                session,
                                user,
                                filters,
                            )
                        ),
                    )
                ).all()
            }
            if ids
            else {}
        )
        matches = []
        seen = set()
        # Native rescoring already orders individual views. First appearance is
        # the deterministic maximum for a Model, never a sum biased by view count.
        for id in ids:
            row = current.get(id)
            if row is not None and row.subject_id not in seen:
                seen.add(row.subject_id)
                matches.append((row.subject_id, row.file_id, row.input_hash))
        return LegResult(
            leg,
            visual_matches=tuple(matches),
            truncated=result.truncated or len(result.items) == leg.candidate_limit,
            weak_matches=bool(result.items) and not ids,
        )
    except (EmbeddingError, DBAPIError, ValueError, TypeError) as exc:
        session.rollback()
        return LegResult(
            leg,
            degraded="search_visual_unavailable",
            available=False,
            error_code=exc.code
            if isinstance(exc, EmbeddingError)
            else "search_store_unavailable",
        )
    finally:
        if lease:
            generations.unpin(session, lease)
