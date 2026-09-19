"""Durable passage fixtures use the same immutable recipe identity as search."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

from printstash_core.search.passages import (
    RECIPE_VERSION,
    PassageContent,
    SearchSubject,
    SubjectType,
    access_identity,
    render_passages,
)
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    IndexGeneration,
    SearchExpansion,
    SearchExpansionTerm,
    SearchGenerationLease,
    SearchIndexFailure,
    SearchLexicalPosting,
    SearchLexicalState,
    SearchLexicalTerm,
    SubjectCaption,
)
from app.db.models.search import (
    SearchDependency,
    SearchPassage,
    SearchProjectionRequest,
    SearchReconciliationState,
)
from app.db.projections import ContentSource
from tests.factories._support import save


def build_search_passage(
    session: Session, subject: SearchSubject, **overrides: Any
) -> SearchPassage:
    passage = render_passages(PassageContent(title="Stored passage"))[0]
    segment_key, dependencies = access_identity(())
    defaults = {
        "subject_type": subject.subject_type.value,
        "subject_id": subject.subject_id,
        "visibility_segment_key": segment_key,
        "access_dependencies_json": dependencies,
        "chunk_index": 0,
        "recipe_version": RECIPE_VERSION,
        "content_hash": passage.content_hash,
        "text": passage.text,
        "title": "Stored passage",
        "source_updated_at": utcnow(),
    }
    return save(session, SearchPassage(**(defaults | overrides)))


def build_search_dependency(
    session: Session, subject: SearchSubject, source: ContentSource, **overrides: Any
) -> SearchDependency:
    defaults = {
        "subject_type": subject.subject_type.value,
        "subject_id": subject.subject_id,
        "source_kind": source.kind,
        "source_id": source.id,
    }
    return save(session, SearchDependency(**(defaults | overrides)))


def build_search_reconciliation_state(
    session: Session, kind: SubjectType, **overrides: Any
) -> SearchReconciliationState:
    return save(
        session, SearchReconciliationState(subject_type=kind.value, **overrides)
    )


def build_search_projection_request(
    session: Session, source: ContentSource, **overrides: Any
) -> SearchProjectionRequest:
    """A committed source notification, ready for background projection."""
    return save(
        session,
        SearchProjectionRequest(
            source_kind=source.kind, source_id=source.id, **overrides
        ),
    )


def build_search_lexical_state(
    session: Session, **overrides: Any
) -> SearchLexicalState:
    return save(session, SearchLexicalState(**overrides))


def build_search_lexical_term(
    session: Session, term: str = "bracket", **overrides: Any
) -> SearchLexicalTerm:
    defaults = {"term": term, "document_frequency": 1}
    return save(session, SearchLexicalTerm(**(defaults | overrides)))


def build_search_lexical_posting(
    session: Session, passage: SearchPassage, term: str = "bracket", **overrides: Any
) -> SearchLexicalPosting:
    defaults = {"passage_id": passage.id, "term": term, "frequency": 1.0}
    return save(session, SearchLexicalPosting(**(defaults | overrides)))


def build_search_index_failure(
    session: Session,
    generation: IndexGeneration,
    passage: SearchPassage,
    **overrides: Any,
) -> SearchIndexFailure:
    defaults = {
        "generation_id": generation.id,
        "passage_id": passage.id,
        "input_hash": passage.content_hash,
        "attempts": 3,
        "state": "quarantined",
        "error_code": "inference_request_rejected",
    }
    return save(session, SearchIndexFailure(**(defaults | overrides)))


def build_search_generation_lease(
    session: Session, generation: IndexGeneration, **overrides: Any
) -> SearchGenerationLease:
    defaults = {
        "token": uuid4().hex,
        "generation_id": generation.id,
        "expires_at": utcnow() + timedelta(minutes=3),
    }
    return save(session, SearchGenerationLease(**(defaults | overrides)))


def build_subject_caption(
    session: Session, subject: SearchSubject, **overrides: Any
) -> SubjectCaption:
    """A caption belongs to one real Subject; dismissal is durable state."""
    defaults = {
        "subject_type": subject.subject_type.value,
        "subject_id": subject.subject_id,
        subject.subject_type.value + "_id": subject.subject_id,
        "version_token": uuid4().hex,
        "state": "generated",
        "phase": "ready",
        "text": "A printable object",
    }
    return save(session, SubjectCaption(**(defaults | overrides)))


def build_user_search_preferences(session: Session, user, **overrides: Any):
    from app.db.models import UserSearchPreferences

    return save(session, UserSearchPreferences(user_id=user.id, **overrides))


def build_search_expansion(
    session: Session, passage: SearchPassage, **overrides: Any
) -> SearchExpansion:

    defaults = {
        "passage_id": passage.id,
        "input_hash": passage.content_hash,
        "recipe": "4" * 64,
        "phase": "ready",
    }
    return save(session, SearchExpansion(**(defaults | overrides)))


def build_search_expansion_term(
    session: Session, expansion: SearchExpansion, term: str = "bike", **overrides: Any
) -> SearchExpansionTerm:

    defaults = {"passage_id": expansion.passage_id, "term": term, "weight": 1.0}
    return save(session, SearchExpansionTerm(**(defaults | overrides)))
