"""Authorized batched Model badges and SQL Saved View predicates."""

from __future__ import annotations

from sqlalchemy import func, union_all
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, or_, select

from app.db.models import Model, SimilarityCandidate, User
from app.modules.similarity.candidates import current_evidence, visible_query


def summaries(
    session: Session, actor: User, model_ids: list[int]
) -> dict[int, dict[str, int]]:
    if not model_ids:
        return {}
    visible = (
        visible_query(session, actor)
        .with_only_columns(
            SimilarityCandidate.model_a_id,
            SimilarityCandidate.model_b_id,
            SimilarityCandidate.review_state,
        )
        .where(
            or_(
                col(SimilarityCandidate.model_a_id).in_(model_ids),
                col(SimilarityCandidate.model_b_id).in_(model_ids),
            ),
            col(SimilarityCandidate.review_state).in_(("open", "confirmed")),
            current_evidence(),
        )
        .cte("visible_similarity")
    )
    endpoints = union_all(
        select(visible.c.model_a_id.label("model_id"), visible.c.review_state).where(
            visible.c.model_a_id.in_(model_ids)
        ),
        select(visible.c.model_b_id.label("model_id"), visible.c.review_state).where(
            visible.c.model_b_id.in_(model_ids)
        ),
    ).subquery()
    counts = session.exec(
        select(endpoints.c.model_id, endpoints.c.review_state, func.count()).group_by(
            endpoints.c.model_id, endpoints.c.review_state
        )
    ).all()
    result = {}
    for model_id, state, count in counts:
        result.setdefault(model_id, {"open_candidates": 0, "confirmed": 0})[
            "open_candidates" if state == "open" else "confirmed"
        ] = count
    return result


def has_open_candidates(session: Session, actor: User) -> ColumnElement[bool]:
    return (
        visible_query(session, actor)
        .with_only_columns(SimilarityCandidate.id)
        .where(
            or_(
                SimilarityCandidate.model_a_id == Model.id,
                SimilarityCandidate.model_b_id == Model.id,
            ),
            SimilarityCandidate.review_state == "open",
            current_evidence(),
        )
        .correlate(Model)
        .exists()
    )
