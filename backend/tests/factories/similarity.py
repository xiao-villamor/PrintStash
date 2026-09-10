"""Review and vector rows whose lineage always points to persisted inputs."""

import hashlib
import json
import struct
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from printstash_core.inference import EmbeddingSpace as SpaceContract
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    EmbeddingSpace,
    File,
    GeometryFingerprint,
    IndexGeneration,
    Model,
    PassageVector,
    SimilarityCandidate,
    SimilarityCandidateObservation,
    SimilarityReviewDecision,
    SimilarityRun,
    User,
)
from app.modules.inference.store import unit_key
from app.modules.media.fingerprints import ALGORITHM_VERSION
from app.modules.similarity.fingerprints import encode_json
from tests.factories._support import nth, save, unique_hash


def build_geometry_fingerprint(
    session: Session, file: File, *, leased: bool = False, **overrides: Any
) -> GeometryFingerprint:
    defaults = {
        "file_id": file.id,
        "source_sha256": file.sha256,
        "algorithm_version": ALGORITHM_VERSION,
    }
    if leased:
        defaults.update(
            lease_token=f"lease-{nth('fingerprint-lease')}",
            lease_expires_at=utcnow() + timedelta(minutes=5),
        )
    return save(session, GeometryFingerprint(**(defaults | overrides)))


def build_similarity_run(
    session: Session,
    actor: User,
    *,
    model_ids: tuple[int, ...] = (),
    active: bool = True,
    **overrides: Any,
) -> SimilarityRun:
    normalized = tuple(sorted(set(model_ids)))
    ids = encode_json(normalized)
    scope = "models" if model_ids else "library"
    defaults = {
        "actor_id": actor.id,
        "scope": scope,
        "scope_ids_json": ids,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "queued" if active else "completed",
        "active_scope_key": hashlib.sha256(
            encode_json([actor.id, scope, normalized, ALGORITHM_VERSION]).encode()
        ).hexdigest()
        if active
        else None,
    }
    return save(session, SimilarityRun(**(defaults | overrides)))


def build_similarity_candidate(
    session: Session, first: Model, second: Model, **overrides: Any
) -> SimilarityCandidate:
    a, b = sorted((first.id, second.id))
    defaults = {
        "model_a_id": a,
        "model_b_id": b,
        "algorithm_version": ALGORITHM_VERSION,
        "evidence_class": "identical_geometry",
        "confidence": 1.0,
        "exact_equivalence": True,
    }
    return save(session, SimilarityCandidate(**(defaults | overrides)))


def build_similarity_observation(
    session: Session,
    candidate: SimilarityCandidate,
    first: GeometryFingerprint,
    second: GeometryFingerprint,
    **overrides: Any,
) -> SimilarityCandidateObservation:
    first_file = session.get(File, first.file_id)
    assert first_file is not None
    if first_file.model_id != candidate.model_a_id:
        first, second = second, first
    defaults = {
        "candidate_id": candidate.id,
        "fingerprint_a_id": first.id,
        "fingerprint_b_id": second.id,
        "input_hash_a": first.source_sha256,
        "input_hash_b": second.source_sha256,
        "lineage_key": hashlib.sha256(
            f"{first.id}:{second.id}:whole".encode()
        ).hexdigest(),
    }
    return save(session, SimilarityCandidateObservation(**(defaults | overrides)))


def build_similarity_decision(
    session: Session, candidate: SimilarityCandidate, actor: User, **overrides: Any
) -> SimilarityReviewDecision:
    defaults = {
        "candidate_id": candidate.id,
        "actor_id": actor.id,
        "request_id": f"request-{nth('similarity-decision')}",
        "action": "confirm_evidence",
        "resolution_kind": "evidence_only",
        "before_state": "open",
        "after_state": "confirmed",
        "candidate_version": candidate.version,
        "snapshot_json": json.dumps(
            {
                "model_a_id": candidate.model_a_id,
                "model_b_id": candidate.model_b_id,
                "evidence": candidate.summary_json,
            }
        ),
        "request_hash": unique_hash("similarity-request"),
    }
    return save(session, SimilarityReviewDecision(**(defaults | overrides)))


def build_embedding_space(session: Session, **overrides: Any) -> EmbeddingSpace:
    contract = SpaceContract(
        model_key=overrides.get("model_key", f"fixture-clip-{nth('embedding-space')}"),
        model_revision=overrides.get("model_revision", "v1"),
        dimension=overrides.get("native_dimension", 3),
        modality=overrides.get("modality", "text_image"),
        profile=overrides.get("profile", "mesh_view"),
        provider=overrides.get("provider", "onnx_cpu"),
        render_recipe=overrides.get("recipe_json", "{}"),
    )
    defaults = {
        "config_hash": contract.config_hash,
        "modality": contract.modality,
        "profile": contract.profile,
        "provider": contract.provider,
        "model_key": contract.model_key,
        "model_revision": contract.model_revision,
        "native_dimension": contract.dimension,
        "recipe_json": contract.render_recipe,
        "config_json": encode_json(asdict(contract)),
    }
    return save(session, EmbeddingSpace(**(defaults | overrides)))


def build_index_generation(
    session: Session, space: EmbeddingSpace, *, active: bool = True, **overrides: Any
) -> IndexGeneration:
    defaults = {
        "space_id": space.id,
        "index_dimension": space.native_dimension,
        "active_profile_key": f"{space.modality}/{space.profile}" if active else None,
        "state": "active" if active else "retired",
    }
    return save(session, IndexGeneration(**(defaults | overrides)))


def build_passage_vector(
    session: Session,
    generation: IndexGeneration,
    file: File,
    *,
    component_index: int = 0,
    **overrides: Any,
) -> PassageVector:
    dimension = generation.index_dimension
    defaults = {
        "generation_id": generation.id,
        "unit_kind": "mesh_component" if component_index else "mesh_artifact",
        "unit_key": unit_key(
            file.id,
            component_index,
            file.sha256,
            session.get(EmbeddingSpace, generation.space_id).recipe_json,
        ),
        "file_id": file.id,
        "model_id": file.model_id,
        "input_hash": file.sha256,
        "native_dimension": dimension,
        "vector_blob": struct.pack(f"<{dimension}f", 1.0, *([0.0] * (dimension - 1))),
    }
    return save(session, PassageVector(**(defaults | overrides)))
