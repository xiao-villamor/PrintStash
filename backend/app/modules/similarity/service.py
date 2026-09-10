"""Small public application boundary for similarity routes and Model projections."""

from __future__ import annotations

import importlib.util
import json

from sqlalchemy import func
from sqlmodel import Session, col, or_, select

from app.core.errors import OperationError
from app.db.models import (
    File,
    GeometryFingerprint,
    Model,
    SimilarityCandidate,
    SimilarityRun,
    User,
)
from app.modules.media.fingerprints import ALGORITHM_VERSION
from app.modules.similarity import candidates, runs
from app.modules.similarity.configuration import read_settings
from app.modules.similarity.fingerprints import live_source_predicates
from app.modules.similarity.retrieval import editable_models


def status(session: Session, actor: User) -> dict:
    config = read_settings(session)
    from app.db.session import get_session_factory
    from app.modules.inference.search import capabilities

    embedding_capabilities = capabilities(session, get_session_factory())
    backlog = session.exec(
        select(func.count())
        .select_from(GeometryFingerprint)
        .join(File, File.id == GeometryFingerprint.file_id)
        .join(Model, Model.id == File.model_id)
        .where(
            *live_source_predicates(),
            editable_models(session, actor),
            GeometryFingerprint.source_sha256 == File.sha256,
            GeometryFingerprint.state == "pending",
        )
    ).one()
    return {
        "enabled": config.enabled,
        "embeddings_enabled": config.embeddings_enabled,
        "algorithm_version": ALGORITHM_VERSION,
        "capabilities": {
            "family_resolution": False,
            "multipart_resolution": True,
            "step": importlib.util.find_spec("OCP") is not None,
            **embedding_capabilities,
        },
        "pending_fingerprints": backlog,
        "selection": {
            "minimum_confidence": config.minimum_confidence,
            "class_overrides": config.class_overrides,
        },
        "settings": config.model_dump() if actor.is_superuser else None,
    }


def project_run(run: SimilarityRun) -> dict:
    return run.model_dump(
        exclude={
            "lease_token",
            "lease_expires_at",
            "active_scope_key",
            "scope_ids_json",
            "checkpoint_json",
            "counters_json",
            "settings_json",
        }
    ) | {
        "scope_ids": json.loads(run.scope_ids_json),
        "counters": json.loads(run.counters_json),
        "checkpoint": {
            key: value
            for key, value in json.loads(run.checkpoint_json).items()
            if key != "pending_pairs"
        },
    }


def list_runs(
    session: Session, actor: User, *, before_id: int | None = None, limit: int = 50
) -> dict:
    query = select(SimilarityRun)
    if not actor.is_superuser:
        query = query.where(SimilarityRun.actor_id == actor.id)
    if before_id:
        query = query.where(SimilarityRun.id < before_id)
    # Bound work even when an editor has lost access to historical scopes.
    rows = session.exec(
        query.order_by(col(SimilarityRun.id).desc()).limit(limit + 1)
    ).all()
    visible = []
    for row in rows[:limit]:
        try:
            runs.require(session, actor, row.id)
        except OperationError:
            continue
        visible.append(project_run(row))
    return {
        "items": visible,
        "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
    }


def model_summary(session: Session, actor: User, model_id: int) -> dict:
    runs.normalize_scope(session, actor, "models", [model_id])
    counts = session.execute(
        candidates.visible_query(session, actor)
        .with_only_columns(SimilarityCandidate.review_state, func.count())
        .where(
            or_(
                SimilarityCandidate.model_a_id == model_id,
                SimilarityCandidate.model_b_id == model_id,
            ),
            candidates.current_evidence(),
        )
        .group_by(SimilarityCandidate.review_state)
    ).all()
    values = dict(counts)
    return {
        "open_candidates": values.get("open", 0),
        "confirmed": values.get("confirmed", 0),
    }


def query_model(session: Session, actor: User, model_id: int) -> dict:
    runs.normalize_scope(session, actor, "models", [model_id])
    page = candidates.list_visible(
        session, actor, model_id=model_id, freshness="current"
    )
    try:
        run = runs.start(session, actor, scope="models", ids=[model_id])
    except OperationError as exc:
        if exc.code != "similarity_run_active":
            raise
        run = session.exec(
            select(SimilarityRun).where(
                SimilarityRun.actor_id == actor.id,
                SimilarityRun.scope == "models",
                SimilarityRun.scope_ids_json
                == json.dumps([model_id], separators=(",", ":")),
                col(SimilarityRun.active_scope_key).is_not(None),
            )
        ).one()
    return {
        "items": page.items,
        "next_cursor": page.next_cursor,
        "run": project_run(run),
    }


def preview_selection(session: Session, actor: User, selection) -> dict:
    """Count currently reviewable persisted evidence without scheduling work."""
    statement = (
        candidates.visible_query(session, actor)
        .with_only_columns(SimilarityCandidate.evidence_class, func.count())
        .where(
            SimilarityCandidate.review_state == "open",
            candidates.current_evidence(),
            candidates.selection_predicate(
                selection.minimum_confidence, selection.class_overrides
            ),
        )
        .group_by(SimilarityCandidate.evidence_class)
    )
    counts = dict(session.execute(statement).all())
    return {"total": sum(counts.values()), "by_class": counts}
