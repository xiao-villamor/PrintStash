"""Reuse complete exact pair proofs only for the same inputs and verifier recipe.

The caller owns authorization and rehashes materialized source bytes first. This
lookup never turns retrieval hashes or approximate evidence into an exact proof.
"""

from __future__ import annotations

import json

from printstash_core.mesh.similarity.verification import VERIFICATION_VERSION
from sqlmodel import Session, select

from app.db.models import (
    File,
    GeometryFingerprint,
    SimilarityCandidate,
    SimilarityCandidateObservation,
)


def reusable_pair(
    session: Session,
    first: GeometryFingerprint,
    second: GeometryFingerprint,
    *,
    sample_points: int,
) -> bool:
    if first.state != "ready" or second.state != "ready":
        return False
    if first.algorithm_version != second.algorithm_version:
        return False
    fa, fb = session.get(File, first.file_id), session.get(File, second.file_id)
    if fa is None or fb is None:
        return False
    observation = SimilarityCandidateObservation
    encoded = session.exec(
        select(observation.evidence_json)
        .join(SimilarityCandidate, SimilarityCandidate.id == observation.candidate_id)
        .where(
            observation.fingerprint_a_id == first.id,
            observation.fingerprint_b_id == second.id,
            observation.input_hash_a == first.source_sha256,
            observation.input_hash_b == second.source_sha256,
            observation.kind
            == (
                "component_match"
                if first.component_index or second.component_index
                else "whole"
            ),
            SimilarityCandidate.algorithm_version == first.algorithm_version,
            SimilarityCandidate.model_a_id == fa.model_id,
            SimilarityCandidate.model_b_id == fb.model_id,
        )
        .limit(1)
    ).first()
    if encoded is None:
        return False
    try:
        evidence = json.loads(encoded)
    except ValueError:
        return False
    return (
        isinstance(evidence, dict)
        and evidence.get("exact_equivalence") is True
        and evidence.get("version") == VERIFICATION_VERSION
        and type(evidence.get("sample_points")) is int
        and sample_points <= evidence["sample_points"] <= 5000
    )
