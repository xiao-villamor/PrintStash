"""Promote verified component correspondences to a quantity-aware proposal."""

from __future__ import annotations

import json

from printstash_core.mesh.similarity.multiset import component_containment
from sqlmodel import Session, select

from app.db.models import File, GeometryFingerprint, SimilarityCandidateObservation


def summarize(
    session: Session, candidate_id: int, first: File, second: File, algorithm: str
) -> dict | None:
    rows = []
    for file in (first, second):
        rows.append(
            session.exec(
                select(GeometryFingerprint)
                .where(
                    GeometryFingerprint.file_id == file.id,
                    GeometryFingerprint.source_sha256 == file.sha256,
                    GeometryFingerprint.algorithm_version == algorithm,
                    GeometryFingerprint.state == "ready",
                )
                .order_by(GeometryFingerprint.component_index)
                .limit(258)
            ).all()
        )
    if any(not side or len(side) > 257 for side in rows):
        return None
    a, b = ({row.id: row for row in side} for side in rows)
    observations = session.exec(
        select(SimilarityCandidateObservation)
        .where(
            SimilarityCandidateObservation.candidate_id == candidate_id,
            SimilarityCandidateObservation.kind == "component_match",
            SimilarityCandidateObservation.input_hash_a == first.sha256,
            SimilarityCandidateObservation.input_hash_b == second.sha256,
        )
        .order_by(SimilarityCandidateObservation.id)
        .limit(5121)
    ).all()
    if len(observations) > 5120:
        return None
    edges = []
    accepted = []
    for observation in observations:
        left, right = (
            a.get(observation.fingerprint_a_id),
            b.get(observation.fingerprint_b_id),
        )
        if left is None or right is None:
            continue
        proof = json.loads(observation.evidence_json)
        # Mirror/scale-normalized similarity is useful review evidence, but it
        # cannot silently become a physically interchangeable Multipart Choice.
        if (
            proof.get("confidence", 0) < 0.98
            or proof.get("mirrored")
            or abs(proof.get("scale_factor", 0) - 1) > 1e-5
            or proof.get("evidence_class")
            not in ("identical_geometry", "remeshed", "repaired")
        ):
            continue
        # Non-rigid instance transforms need verification in placed coordinates;
        # retain the observations, but do not claim component interchangeability.
        if not _rigid_instances(left) or not _rigid_instances(right):
            continue
        edges.append((left.component_index, right.component_index))
        accepted.append(observation)
    if not edges:
        return None
    counts_a = {
        row.component_index: row.instance_count
        for row in rows[0]
        if row.component_index > 0
    }
    counts_b = {
        row.component_index: row.instance_count
        for row in rows[1]
        if row.component_index > 0
    }
    proposals = [(counts_a, counts_b, [(x, y) for x, y in edges if x and y])]
    if any(x == 0 and y > 0 for x, y in edges):
        proposals.append(
            ({0: 1}, counts_b, [(x, y) for x, y in edges if x == 0 and y > 0])
        )
    if any(y == 0 and x > 0 for x, y in edges):
        proposals.append(
            (counts_a, {0: 1}, [(x, y) for x, y in edges if y == 0 and x > 0])
        )
    results = [
        result
        for left, right, matches in proposals
        if left
        and right
        and (result := component_containment(left, right, matches)) is not None
    ]
    if not results:
        return None
    result = max(
        results, key=lambda item: (item.evidence_class == "plate_of", item.copies)
    )
    contained = first if result.contained_side == "a" else second
    return {
        "evidence_class": result.evidence_class,
        "confidence": 0.99,
        "exact_equivalence": False,
        "contained_side": result.contained_side,
        "copies": result.copies,
        "component_assignments": result.assignments,
        "composition": [{"model_id": contained.model_id, "quantity": result.copies}],
        "unmatched_components": abs(sum(counts_a.values()) - sum(counts_b.values()))
        if result.evidence_class == "component_of"
        else 0,
        "basis": "verified_component_multiset",
        "lineage_key": accepted[-1].lineage_key,
        "format_a": first.file_type.value,
        "format_b": second.file_type.value,
    }


def _rigid_instances(row: GeometryFingerprint) -> bool:
    import numpy as np

    for instance in json.loads(row.instances_json):
        matrix = np.asarray(instance["transform"], dtype=float)
        linear = matrix[:3, :3]
        if (
            not np.allclose(linear.T @ linear, np.eye(3), rtol=1e-6, atol=1e-6)
            or np.linalg.det(linear) < 0
        ):
            return False
    return True
