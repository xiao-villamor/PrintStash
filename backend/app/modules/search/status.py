"""No-egress capability disclosure and an authorized backlog indicator."""

import importlib.util

from printstash_core.inference import EmbeddingError
from sqlalchemy import and_, exists, or_
from sqlmodel import Session, select

from app.db.models import (
    File,
    SearchDependency,
    SearchPassage,
    SearchProjectionRequest,
    User,
)
from app.modules.inference.configuration import embedding_provider
from app.modules.inference.remote import RemoteEmbeddingProvider
from app.modules.search import configuration, generations, semantic, visual_sources
from app.modules.search.access import visible_passage_ids, visible_subjects
from app.schemas.search import SearchStatus


def read(session: Session, user: User) -> SearchStatus:
    settings = configuration.settings(session)
    result = SearchStatus(
        enabled=settings.enabled, semantic_ready=False, legs=["lexical"], generations=[]
    )
    visible = visible_subjects(session, user)
    owner_visible = exists(select(visible.c.id).where(
        visible.c.kind == SearchProjectionRequest.source_kind,
        visible.c.id == SearchProjectionRequest.source_id,
    ))
    dependency_visible = exists(select(SearchDependency.id).join(visible, and_(
        visible.c.kind == SearchDependency.subject_type,
        visible.c.id == SearchDependency.subject_id,
    )).where(SearchDependency.source_kind == SearchProjectionRequest.source_kind,
             SearchDependency.source_id == SearchProjectionRequest.source_id))
    result.backlog = session.exec(select(SearchProjectionRequest.id).where(
        or_(owner_visible, dependency_visible),
    ).limit(1)).first() is not None
    try:
        active = semantic.registry(session, settings)
    except (ValueError, TypeError):
        result.degraded.append("search_semantic_unavailable")
        return result
    hosts = set()
    for leg in active:
        try:
            if leg.space.provider == "onnx_cpu" and any(
                importlib.util.find_spec(name) is None
                for name in ("onnxruntime", "onnx", "tokenizers")
            ):
                raise EmbeddingError("embedding_runtime_unavailable")
            # The active generation already passed its canary. Constructing its
            # provider only checks saved identity/assets, never contacts a host
            # or loads a native inference session.
            provider = embedding_provider(session, leg.space)
        except (EmbeddingError, ValueError, TypeError):
            if "search_semantic_unavailable" not in result.degraded:
                result.degraded.append("search_semantic_unavailable")
            continue
        result.semantic_ready = True
        result.legs.append(leg.name)
        result.generations.append(leg.generation_id)
        if isinstance(provider, RemoteEmbeddingProvider):
            hosts.add(provider.endpoint.host)
        if (
            session.exec(
                generations.missing(session, leg.generation_id, leg.space)
                .where(
                    File.id.in_(visual_sources.eligible(session, user))
                    if leg.space.profile in visual_sources.PROFILES
                    else SearchPassage.id.in_(visible_passage_ids(session, user))
                )
                .limit(1)
            ).first()
            is not None
        ):
            result.backlog = True
    result.remote_hosts = sorted(hosts)
    return result
