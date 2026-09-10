"""Immutable embedding spaces, one active native generation and source-fenced units."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from typing import Iterable

from printstash_core.inference import EmbeddingError
from printstash_core.inference import EmbeddingSpace as SpaceContract
from printstash_core.inference.vectors import (
    NeighborResult,
    VectorEntry,
    cosine_neighbors,
    normalize,
)
from sqlalchemy import insert, literal
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import (
    EmbeddingSpace,
    File,
    IndexGeneration,
    Model,
    PassageVector,
    SimilarityRun,
    User,
)
from app.db.session import SessionFactory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.similarity.fingerprints import encode_json, live_source_predicates
from app.modules.similarity.retrieval import editable_models


def active_generation(session: Session, space: SpaceContract) -> IndexGeneration | None:
    return session.exec(
        select(IndexGeneration)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            EmbeddingSpace.config_hash == space.config_hash,
            IndexGeneration.state == "active",
            IndexGeneration.index_dimension == space.dimension,
            IndexGeneration.index_backend == "numpy",
            IndexGeneration.quantization == "float32",
        )
    ).first()


def initialize(sessions: SessionFactory, provider: LocalEmbeddingProvider) -> int:
    """Validate native canaries before committing the initial durable generation."""
    provider.validate()
    contract = provider.space
    with sessions.scoped_session() as session:
        existing = active_generation(session, contract)
        if existing is not None:
            assert existing.id is not None
            return existing.id
        profile_key = f"{contract.modality}/{contract.profile}"
        try:
            space = session.exec(
                select(EmbeddingSpace).where(
                    EmbeddingSpace.config_hash == contract.config_hash
                )
            ).first()
            if space is None:
                space = EmbeddingSpace(
                    config_hash=contract.config_hash,
                    modality=contract.modality,
                    profile=contract.profile,
                    provider=contract.provider,
                    model_key=contract.model_key,
                    model_revision=contract.model_revision,
                    native_dimension=contract.dimension,
                    normalization=contract.normalization,
                    prefixes_json=encode_json(
                        {
                            "query": contract.query_prefix,
                            "document": contract.document_prefix,
                        }
                    ),
                    recipe_json=contract.render_recipe,
                    config_json=encode_json(asdict(contract)),
                )
                session.add(space)
                session.flush()
            assert space.id is not None
            generation = IndexGeneration(
                space_id=space.id,
                active_profile_key=profile_key,
                index_dimension=contract.dimension,
            )
            session.add(generation)
            session.commit()
            session.refresh(generation)
            assert generation.id is not None
            return generation.id
        except IntegrityError as exc:
            session.rollback()
            existing = active_generation(session, contract)
            if existing is not None:
                assert existing.id is not None
                return existing.id
            # Switching an active model needs an explicit generation migration;
            # never mix embeddings or silently replace an operator's index.
            raise EmbeddingError("embedding_active_generation_conflict") from exc


def unit_key(file_id: int, component_index: int, input_hash: str, recipe: str) -> str:
    if (
        not 1 <= file_id < 2**63
        or not 0 <= component_index <= 2048
        or re.fullmatch(r"[0-9a-f]{64}", input_hash) is None
    ):
        raise EmbeddingError("embedding_unit_invalid")
    # Full Space identity lives on the generation; the recipe suffix aids human
    # inspection and component identity remains parseable without a second index.
    return f"mesh:{file_id}:{component_index}:{input_hash}:{hashlib.sha256(recipe.encode()).hexdigest()[:16]}"


def unit_component(key: str) -> int | None:
    match = re.fullmatch(
        r"mesh:[1-9][0-9]{0,18}:([0-9]{1,4}):[0-9a-f]{64}:[0-9a-f]{16}", key
    )
    if match is None or int(match[1]) > 2048:
        return None
    return int(match[1])


def has_unit(session: Session, generation_id: int, key: str) -> bool:
    return (
        session.exec(
            select(PassageVector.id).where(
                PassageVector.generation_id == generation_id,
                PassageVector.unit_key == key,
            )
        ).first()
        is not None
    )


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
    key = unit_key(file_id, component_index, input_hash, space.render_recipe)
    blob = normalize(vector, space.dimension)
    generation = active_generation(session, space)
    if generation is None or generation.id != generation_id:
        raise EmbeddingError("embedding_space_mismatch")
    if has_unit(session, generation_id, key):
        return False
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
    values = (
        select(
            literal(generation_id),
            literal("mesh_component" if component_index else "mesh_artifact"),
            literal(key),
            File.model_id,
            File.id,
            literal(input_hash),
            literal(space.dimension),
            literal(blob),
            literal(utcnow()),
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
    try:
        result = session.connection().execute(
            insert(PassageVector).from_select(
                [
                    "generation_id",
                    "unit_kind",
                    "unit_key",
                    "model_id",
                    "file_id",
                    "input_hash",
                    "native_dimension",
                    "vector_blob",
                    "created_at",
                ],
                values,
            )
        )
        session.commit()
        return result.rowcount == 1
    except IntegrityError:
        session.rollback()
        if has_unit(session, generation_id, key):
            return False
        raise


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
    generation = active_generation(session, space)
    if generation is None or generation.id != generation_id:
        raise EmbeddingError("embedding_space_mismatch")
    statement = (
        select(PassageVector.id, PassageVector.model_id, PassageVector.vector_blob)
        .join(File, File.id == PassageVector.file_id)
        .join(Model, Model.id == PassageVector.model_id)
        .where(
            PassageVector.generation_id == generation_id,
            PassageVector.native_dimension == space.dimension,
            PassageVector.input_hash == File.sha256,
            File.model_id == Model.id,
            *live_source_predicates(),
            editable_models(session, actor),
        )
    )
    if exclude_model_id is not None:
        statement = statement.where(Model.id != exclude_model_id)
    rows = session.exec(
        statement.order_by(PassageVector.id)
        .limit(max_scan + 1)
        .execution_options(yield_per=256)
    )
    return cosine_neighbors(
        normalize(vector, space.dimension),
        (VectorEntry(unit_id, subject_id, blob) for unit_id, subject_id, blob in rows),
        dimension=space.dimension,
        limit=limit,
        max_scan=max_scan,
    )
