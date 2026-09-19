"""Optional Similar Models adapter for the shared vector platform.

Only this consumer knows SimilarityRun leases, geometry Artifact identities and
EDIT-only candidate permissions. The store has no dependency on these owners.
"""

from typing import Iterable

from printstash_core.inference import EmbeddingSpace as SpaceContract
from printstash_core.inference.units import unit_component as unit_component
from printstash_core.inference.units import unit_key as unit_key
from printstash_core.inference.vectors import NeighborResult
from sqlalchemy import literal
from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import File, Model, PassageVector, SimilarityRun, User
from app.modules.search import vector_store
from app.modules.search.vector_store import active_generation as active_generation
from app.modules.search.vector_store import has_unit as has_unit
from app.modules.search.vector_store import initialize as initialize
from app.modules.similarity.fingerprints import live_source_predicates
from app.modules.similarity.retrieval import editable_models


def publish(
    session: Session,
    actor: User,
    *,
    generation_id: int,
    space: SpaceContract,
    file_id: int,
    component_index: int,
    input_hash: str,
    vector: Iterable[float],
    run_id: int,
    lease_token: str,
) -> bool:
    owner = (
        select(SimilarityRun.id)
        .where(
            SimilarityRun.id == run_id,
            SimilarityRun.lease_token == lease_token,
            col(SimilarityRun.lease_expires_at) > utcnow(),
            col(SimilarityRun.cancel_requested).is_(False),
            SimilarityRun.state == "running",
        )
        .exists()
    )
    source = (
        select(
            literal("model").label("subject_type"),
            File.model_id.label("subject_id"),
            File.model_id,
            File.id.label("file_id"),
            literal(None).label("passage_id"),
        )
        .select_from(File)
        .join(Model, Model.id == File.model_id)
        .where(
            File.id == file_id,
            File.sha256 == input_hash,
            *live_source_predicates(),
            editable_models(session, actor),
            owner,
        )
    )
    changed = vector_store.publish(
        session,
        generation_id=generation_id,
        space=space,
        unit_kind="mesh_component" if component_index else "mesh_artifact",
        unit_key=unit_key(file_id, component_index, input_hash, space.render_recipe),
        input_hash=input_hash,
        vector=vector,
        source=source,
        states=("active",),
    )
    session.commit()
    return changed


def query(
    session: Session,
    actor: User,
    *,
    generation_id: int,
    space: SpaceContract,
    vector: Iterable[float],
    limit: int = 20,
    max_scan: int = 100_000,
    exclude_model_id: int | None = None,
) -> NeighborResult:
    allowed = (
        select(PassageVector.id)
        .join(File, File.id == PassageVector.file_id)
        .join(Model, Model.id == PassageVector.model_id)
        .where(
            PassageVector.subject_type == "model",
            PassageVector.subject_id == Model.id,
            PassageVector.input_hash == File.sha256,
            File.model_id == Model.id,
            *live_source_predicates(),
            editable_models(session, actor),
        )
    )
    if exclude_model_id is not None:
        allowed = allowed.where(Model.id != exclude_model_id)
    return vector_store.query(
        session,
        generation_id=generation_id,
        space=space,
        vector=vector,
        allowed_ids=allowed,
        limit=limit,
        max_scan=max_scan,
    )
