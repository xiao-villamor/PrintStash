"""Bounded authorized lexical and semantic retrieval with per-leg evidence."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from urllib.parse import urlencode

from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.search.fusion import RankedLeg, fuse
from printstash_core.search.lexical import query_terms
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import File, SearchPassage, User
from app.modules.library.model_views.listing import read_items_by_ids
from app.modules.library.model_views.pagination import ordered_ids
from app.modules.search import (
    configuration,
    cursors,
    model_query,
    semantic,
    structured,
    visual_sources,
)
from app.modules.search.access import passage_in_scope, visible_passage_ids
from app.modules.search.dependencies import SUBJECT_MODELS
from app.modules.search.lexical_index import capability
from app.modules.search.lexical_query import ordered_passages
from app.schemas.models import ModelFilters, ModelSort
from app.schemas.search import SearchEvidence, SearchResponse, SearchResult

MAX_CANDIDATES = 2048


def evidence(passage: SearchPassage, query: str) -> SearchEvidence:
    tokens = query_terms(query)
    fields = (
        ("title", passage.title),
        ("tags", passage.tags_text),
        ("text", passage.text),
    )
    field, content = next(
        (
            (name, text)
            for name, text in fields
            if any(token in text.lower() for token in tokens)
        ),
        fields[-1],
    )
    pattern = (
        re.compile("|".join(re.escape(token) for token in tokens), re.IGNORECASE)
        if tokens
        else None
    )
    match = pattern.search(content) if pattern else None
    start = max(0, match.start() - 80) if match else 0
    excerpt = content[start : start + 240]
    ranges = (
        [(match.start(), match.end()) for match in pattern.finditer(excerpt)][:32]
        if pattern
        else []
    )
    return SearchEvidence(field=field, text=excerpt, ranges=ranges)


def search(
    session: Session,
    user: User,
    query: str,
    *,
    mode: str = "hybrid",
    limit: int = 30,
    cursor: str | None = None,
    types: tuple[SubjectType, ...] = tuple(SubjectType),
    legs: tuple[str, ...] = (
        "lexical",
        "semantic_text",
        "thumbnail",
        "multiview",
        "point_cloud",
    ),
    instant: bool = False,
    image: EmbeddingInput | None = None,
    source_model_id: int | None = None,
    filters: ModelFilters | None = None,
    sort: ModelSort = ModelSort.RELEVANCE,
) -> SearchResponse:
    if not 1 <= limit <= 100:
        raise ValueError("search_page_limit")
    query_terms(query)
    structured.validate(user, filters)
    if sort != ModelSort.RELEVANCE and filters is None:
        filters = ModelFilters()
    if image is not None and image.modality != "image":
        raise ValueError("search_image_required")
    private_query_key = query
    if image is not None:
        private_query_key = (
            "image:"
            + hashlib.sha256(
                struct.pack("!HH", image.width, image.height) + image.rgb
            ).hexdigest()
        )
    user_id, auth_version = user.id, user.auth_version
    user = session.get(User, user_id, populate_existing=True)
    if user is None or not user.is_active or user.auth_version != auth_version:
        raise OperationError("search_user_required")
    if source_model_id is not None:
        try:
            model_query.require_visible(session, user, source_model_id)
        except EmbeddingError:
            raise OperationError(
                "search_model_unavailable", kind=ErrorKind.NOT_FOUND
            ) from None
    dense_query = (
        model_query.ModelQuery(source_model_id)
        if source_model_id is not None
        else image
        if image is not None
        else query
    )
    settings = configuration.settings(session)
    degraded = []
    active = ()
    if mode == "hybrid" and not instant:
        try:
            active = tuple(
                leg
                for leg in semantic.registry(session, settings)
                if leg.name in legs
                and (image is None or leg.space.profile in visual_sources.PROFILES)
            )
        except (ValueError, TypeError):
            degraded.append("search_semantic_unavailable")
    if image is not None and not active:
        degraded.append("search_visual_unavailable")
    source_fingerprint = None
    if source_model_id is not None:
        source_fingerprint = model_query.fingerprint(
            session, user, source_model_id, active, types
        )
        private_query_key = f"model:{source_model_id}:{source_fingerprint}"
    generations = tuple(leg.generation_id for leg in active)
    query_context = (
        tuple(sorted(kind.value for kind in types)),
        tuple(sorted(legs)),
        instant,
        settings.rrf_k,
        settings.lexical_weight,
        settings.lexical_backend,
        settings.sparse_expansion_enabled,
        settings.sparse_model_id,
        settings.local_models_enabled,
        json.dumps(filters.model_dump(mode="json"), sort_keys=True)
        if filters is not None
        else None,
        sort.value,
        tuple((leg.name, leg.floor, leg.weight) for leg in active),
    )
    context = cursors.context_key(
        int(user.id),
        private_query_key,
        mode,
        generations,
        scope=(semantic.authorization_context(session, user), query_context),
    )
    offset = cursors.decode(cursor, context, generations=generations) if cursor else 0
    dense = (
        [
            semantic.retrieve(
                session,
                user_id,
                auth_version,
                dense_query,
                leg,
                types=types,
                filters=filters,
            )
            for leg in active
        ]
        if query.strip() or image is not None or source_model_id is not None
        else []
    )
    if any(result.error_code == "search_generation_changed" for result in dense):
        # A cutover can win between registry lookup and the lease UPDATE. No
        # inference ran for that leg. Admit once against the new active set;
        # an existing page cursor must restart instead of mixing generations.
        if cursor:
            raise OperationError("search_cursor_expired", kind=ErrorKind.CONFLICT)
        session.rollback()
        session.expire_all()
        active = tuple(
            leg
            for leg in semantic.registry(session, configuration.settings(session))
            if leg.name in legs
            and (image is None or leg.space.profile in visual_sources.PROFILES)
        )
        generations = tuple(leg.generation_id for leg in active)
        query_context = (
            *query_context[:-1],
            tuple((leg.name, leg.floor, leg.weight) for leg in active),
        )
        dense = [
            semantic.retrieve(
                session,
                user_id,
                auth_version,
                dense_query,
                leg,
                types=types,
                filters=filters,
            )
            for leg in active
        ]
    # End the inference/read transaction before the canonical visibility pass.
    if dense:
        session.rollback()
        session.expire_all()
    user = session.get(User, user_id, populate_existing=True)
    if user is None or not user.is_active or user.auth_version != auth_version:
        raise OperationError("search_user_required")
    if source_model_id is not None:
        try:
            current_source = model_query.fingerprint(
                session, user, source_model_id, active, types
            )
        except EmbeddingError:
            raise OperationError(
                "search_model_unavailable", kind=ErrorKind.NOT_FOUND
            ) from None
        if current_source != source_fingerprint:
            raise OperationError("search_model_index_pending", kind=ErrorKind.CONFLICT)
    if not configuration.settings(session).enabled:
        dense = []
    backend = (
        "ranked_like"
        if settings.lexical_backend == "ranked_like"
        else capability(session)
    )
    allowed = visible_passage_ids(session, user).where(
        SearchPassage.subject_type.in_([kind.value for kind in types])
    )
    allowed = structured.passages(allowed, session, user, filters)
    try:
        with session.begin_nested():
            ranks = (
                session.exec(
                    ordered_passages(
                        session,
                        query,
                        allowed,
                        limit=MAX_CANDIDATES,
                        force_like=backend == "ranked_like",
                    )
                ).all()
                if image is None and source_model_id is None
                else []
            )
    except DBAPIError:
        ranks = session.exec(
            ordered_passages(
                session, query, allowed, limit=MAX_CANDIDATES, force_like=True
            )
        ).all()
        backend = "ranked_like"
        degraded.append("search_fts_unavailable")
    # Lexical stays present when AI is disabled or a semantic leg fails.
    ranked_ids = {"lexical": tuple(id for id, _score in ranks)}
    weights = {"lexical": settings.lexical_weight}
    for result in dense:
        if result.degraded:
            degraded.append(result.degraded)
        if result.available:
            ranked_ids[result.leg.name] = result.passages
            weights[result.leg.name] = result.leg.weight
    ids = list(dict.fromkeys(id for values in ranked_ids.values() for id in values))
    passages = (
        {
            row.id: row
            for row in session.exec(
                select(SearchPassage).where(
                    SearchPassage.id.in_(ids), passage_in_scope(allowed)
                )
            ).all()
        }
        if ids
        else {}
    )
    evidence_by_subject = {}
    rank_lists = []
    for name, passage_ids in ranked_ids.items():
        subjects = []
        for id in passage_ids:
            passage = passages.get(id)
            if passage is None:
                continue
            subject = SearchSubject(
                SubjectType(passage.subject_type), passage.subject_id
            )
            subjects.append(subject)
            evidence_by_subject.setdefault((subject, name), passage)
        rank_lists.append(RankedLeg(name, tuple(subjects), weights[name]))
    for result in dense:
        if not result.visual_matches:
            continue
        current = {
            (file.model_id, file.id, file.sha256)
            for file in session.exec(
                select(File).where(
                    File.id.in_([match[1] for match in result.visual_matches]),
                    File.id.in_(visual_sources.eligible(session, user)),
                    *(
                        [
                            File.model_id.in_(
                                structured.model_ids(session, user, filters)
                            )
                        ]
                        if filters is not None
                        else []
                    ),
                )
            ).all()
        }
        subjects = tuple(
            SearchSubject(SubjectType.MODEL, model_id)
            for model_id, file_id, digest in result.visual_matches
            if (model_id, file_id, digest) in current
        )
        rank_lists = [leg for leg in rank_lists if leg.name != result.leg.name]
        rank_lists.append(RankedLeg(result.leg.name, subjects, result.leg.weight))
    filter_only = (
        filters is not None
        and not query.strip()
        and image is None
        and source_model_id is None
    )
    filtered_truncated = False
    if filter_only and SubjectType.MODEL in types:
        baseline = ordered_ids(session, user, filters, sort, limit=MAX_CANDIDATES + 1)
        filtered_truncated = len(baseline) > MAX_CANDIDATES
        rank_lists = [
            RankedLeg(
                "filters",
                tuple(
                    SearchSubject(SubjectType.MODEL, id)
                    for id in baseline[:MAX_CANDIDATES]
                ),
                1,
            )
        ]
    fused = fuse(tuple(rank_lists), k=settings.rrf_k)
    filtered_truncated = filtered_truncated or len(fused) > MAX_CANDIDATES
    fused = fused[:MAX_CANDIDATES]
    if sort != ModelSort.RELEVANCE and fused:
        order = ordered_ids(
            session,
            user,
            filters or ModelFilters(),
            sort,
            ids=[match.subject.subject_id for match in fused],
            limit=MAX_CANDIDATES,
        )
        positions = {id: index for index, id in enumerate(order)}
        fused = sorted(
            (match for match in fused if match.subject.subject_id in positions),
            key=lambda match: positions[match.subject.subject_id],
        )
    selected = fused[offset : offset + limit]
    model_ids = [
        match.subject.subject_id
        for match in selected
        if match.subject.subject_type is SubjectType.MODEL
    ]
    model_views = {row.id: row for row in read_items_by_ids(session, user, model_ids)}
    items = []
    for match in selected:
        subject = match.subject
        kind = subject.subject_type
        row = session.get(SUBJECT_MODELS[kind], subject.subject_id)
        if row is None or (
            kind is SubjectType.MODEL and subject.subject_id not in model_views
        ):
            continue
        href = {
            SubjectType.MODEL: f"/models/{subject.subject_id}",
            SubjectType.COLLECTION: "/?" + urlencode({"c": getattr(row, "path", "")}),
            SubjectType.DOCUMENT: f"/documents/{subject.subject_id}",
            SubjectType.MULTIPART_MODEL: f"/multipart-models/{subject.subject_id}",
        }[kind]
        items.append(
            SearchResult(
                subject_type=kind,
                subject_id=subject.subject_id,
                name=row.name,
                href=href,
                evidence=[
                    SearchEvidence(leg=name, field="filters", text="")
                    if name == "filters"
                    else SearchEvidence(leg=name, field="visual", text="")
                    if name in visual_sources.PROFILES
                    else evidence(
                        evidence_by_subject[(subject, name)], query
                    ).model_copy(update={"leg": name})
                    for name in match.legs
                ],
                model=model_views.get(subject.subject_id)
                if kind is SubjectType.MODEL
                else None,
            )
        )
    # New cursors describe the final authorization state. An old cursor cannot
    # survive a grant, filter, user, query or generation change unnoticed.
    context = cursors.context_key(
        int(user.id),
        private_query_key,
        mode,
        generations,
        scope=(semantic.authorization_context(session, user), query_context),
    )
    ready = any(result.available for result in dense)
    return SearchResponse(
        items=items,
        next_cursor=cursors.encode(context, offset + limit, generations=generations)
        if len(fused) > offset + limit
        else None,
        lexical_backend=backend,
        degraded=list(dict.fromkeys(degraded)),
        legs=list(ranked_ids),
        semantic_ready=ready,
        generations=list(generations),
        truncated=filtered_truncated
        or len(ranks) == MAX_CANDIDATES
        or any(result.truncated for result in dense),
        outcome="results"
        if items
        else "no_strong_matches"
        if any(result.weak_matches for result in dense)
        else "no_results",
        leg_errors={
            result.leg.name: result.error_code for result in dense if result.error_code
        },
    )
