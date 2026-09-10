"""Composition requires physically interchangeable components and complete counts."""

import json

import pytest

from app.modules.media.fingerprints import ALGORITHM_VERSION
from app.modules.similarity.composition import summarize


@pytest.fixture
def assembly(
    db_session,
    make_model,
    make_file,
    make_geometry_fingerprint,
    make_similarity_candidate,
    make_similarity_observation,
):
    first, second = make_file(make_model()), make_file(make_model())
    make_geometry_fingerprint(first, state="ready")
    make_geometry_fingerprint(second, state="ready")
    a = make_geometry_fingerprint(first, state="ready", component_index=1)
    b = make_geometry_fingerprint(second, state="ready", component_index=1)
    make_geometry_fingerprint(second, state="ready", component_index=2)
    candidate = make_similarity_candidate(
        first.model, second.model, confidence=0, exact_equivalence=False
    )
    evidence = {
        "confidence": 1.0,
        "mirrored": False,
        "scale_factor": 1,
        "evidence_class": "identical_geometry",
    }
    observation = make_similarity_observation(
        candidate, a, b, kind="component_match", evidence_json=json.dumps(evidence)
    )
    return candidate, first, second, a, b, observation


class TestComponentEvidence:
    def test_identifies_part_inside_mixed_assembly(self, db_session, assembly):
        candidate, first, second, *_ = assembly
        result = summarize(db_session, candidate.id, first, second, ALGORITHM_VERSION)
        assert result["evidence_class"] == "component_of"
        assert result["contained_side"] == "a"
        assert result["composition"] == [{"model_id": first.model_id, "quantity": 1}]
        assert result["unmatched_components"] == 1
        assert result["exact_equivalence"] is False

    @pytest.mark.parametrize(
        "field,value",
        [
            ("confidence", 0.979),
            ("mirrored", True),
            ("scale_factor", 2),
            ("evidence_class", "similar_shape"),
        ],
    )
    def test_rejects_noninterchangeable_evidence(
        self, db_session, assembly, field, value
    ):
        candidate, first, second, *_, observation = assembly
        evidence = json.loads(observation.evidence_json)
        evidence[field] = value
        observation.evidence_json = json.dumps(evidence)
        db_session.add(observation)
        db_session.commit()
        assert (
            summarize(db_session, candidate.id, first, second, ALGORITHM_VERSION)
            is None
        )

    @pytest.mark.parametrize("factor", [-1, 2])
    def test_refuses_nonrigid_instance_placement(self, db_session, assembly, factor):
        candidate, first, second, a, *_ = assembly
        a.instances_json = json.dumps(
            [
                {
                    "resource_id": "part",
                    "transform": [
                        [factor, 0, 0, 0],
                        [0, 1, 0, 0],
                        [0, 0, 1, 0],
                        [0, 0, 0, 1],
                    ],
                }
            ]
        )
        db_session.add(a)
        db_session.commit()
        assert (
            summarize(db_session, candidate.id, first, second, ALGORITHM_VERSION)
            is None
        )

    def test_ignores_observations_from_previous_source(self, db_session, assembly):
        candidate, first, second, *_, observation = assembly
        observation.input_hash_a = "f" * 64
        db_session.add(observation)
        db_session.commit()
        assert (
            summarize(db_session, candidate.id, first, second, ALGORITHM_VERSION)
            is None
        )

    def test_refuses_missing_component_inventory(self, db_session, assembly):
        candidate, first, second, *_ = assembly
        assert (
            summarize(db_session, candidate.id, first, second, "unknown-algorithm")
            is None
        )

    def test_ignores_unavailable_matched_component(self, db_session, assembly):
        candidate, first, second, a, *_ = assembly
        a.state = "failed"
        db_session.add(a)
        db_session.commit()
        assert (
            summarize(db_session, candidate.id, first, second, ALGORITHM_VERSION)
            is None
        )
