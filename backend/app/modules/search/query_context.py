"""Shared semantic leg contracts and freshly authorized query inputs."""

import hashlib
import json
from dataclasses import dataclass

from printstash_core.inference import EmbeddingSpace as Space
from printstash_core.search.passages import SubjectType
from sqlmodel import Session, select

from app.db.models import PassageVector, SearchPassage, User
from app.modules.identity.rbac import accessible_collection_ids
from app.modules.search import structured
from app.modules.search.access import visible_passage_clause
from app.modules.search.text_inputs import TextRecipe


@dataclass(frozen=True)
class SemanticLeg:
    name: str
    generation_id: int
    space: Space
    floor: float
    weight: float
    timeout: float
    candidate_limit: int = 100
    scan_limit: int = 100_000


@dataclass(frozen=True)
class LegResult:
    leg: SemanticLeg
    passages: tuple[int, ...] = ()
    degraded: str | None = None
    truncated: bool = False
    available: bool = True
    weak_matches: bool = False
    error_code: str | None = None
    visual_matches: tuple[
        tuple[int, int, str], ...
    ] = ()  # Model, Artifact, source hash


def authorization_context(session: Session, user: User) -> str:
    value = [
        user.id,
        user.auth_version,
        user.is_active,
        user.is_superuser,
        sorted(accessible_collection_ids(session, user)),
    ]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def allowed_vectors(
    session: Session,
    user: User,
    leg: SemanticLeg,
    types: tuple[SubjectType, ...],
    filters=None,
):
    recipe = TextRecipe.for_space(leg.space)
    return structured.vectors(
        (
            select(PassageVector.id)
            .join(SearchPassage, SearchPassage.id == PassageVector.passage_id)
            .where(
                PassageVector.generation_id == leg.generation_id,
                PassageVector.unit_kind == "passage",
                PassageVector.input_hash == SearchPassage.content_hash,
                PassageVector.subject_type == SearchPassage.subject_type,
                PassageVector.subject_id == SearchPassage.subject_id,
                SearchPassage.recipe_version == recipe.passage_version,
                visible_passage_clause(session, user),
                SearchPassage.subject_type.in_([kind.value for kind in types]),
            )
        ),
        session,
        user,
        filters,
    )
