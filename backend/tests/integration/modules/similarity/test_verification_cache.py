"""Cached proofs remain tied to the actual inputs, geometry and sampling recipe."""

import json
from dataclasses import replace

import pytest
from printstash_core.mesh.similarity.verification import verify_meshes
from sqlmodel import select

from app.db.models import File, SimilarityCandidateObservation
from app.modules.similarity import candidates, review, verification_cache
from tests.factories.geometry import tetrahedron


@pytest.fixture(scope="module")
def exact_proof():
    mesh = tetrahedron()
    return verify_meshes(
        mesh.vertices, mesh.faces, mesh.vertices, mesh.faces, sample_points=256
    )


@pytest.fixture
def verified_pair(
    db_session, make_model, make_file, make_geometry_fingerprint, exact_proof
):
    first = make_geometry_fingerprint(make_file(make_model()), state="ready")
    second = make_geometry_fingerprint(make_file(make_model()), state="ready")
    assert candidates.publish(db_session, first, second, exact_proof) is not None
    return first, second


class TestVerificationCache:
    @pytest.mark.parametrize("previous_version", [None, "surface-verification-v2"])
    def test_retains_refreshed_recipe_for_reuse(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        make_geometry_fingerprint,
        exact_proof,
        previous_version,
    ):
        pair = [
            make_geometry_fingerprint(make_file(make_model()), state="ready")
            for _ in range(2)
        ]
        prior = (
            replace(exact_proof, version=previous_version)
            if previous_version is not None
            else exact_proof
        )
        candidate = candidates.publish(db_session, *pair, prior)
        review.decide(
            db_session,
            make_user(superuser=True),
            candidate.id,
            review.DecisionRequest(
                request_id="preserve-rejection",
                version=candidate.version,
                action="reject",
            ),
        )
        original = db_session.exec(select(SimilarityCandidateObservation)).one()
        snapshot = original.evidence_json
        mesh = tetrahedron()
        refreshed = verify_meshes(
            mesh.vertices, mesh.faces, mesh.vertices, mesh.faces, sample_points=512
        )

        candidates.publish(db_session, *pair, refreshed)

        assert verification_cache.reusable_pair(db_session, *pair, sample_points=512)
        assert len(db_session.exec(select(SimilarityCandidateObservation)).all()) == 2
        db_session.refresh(original)
        db_session.refresh(candidate)
        assert original.evidence_json == snapshot
        assert candidate.review_state == "rejected"

    @pytest.mark.parametrize("side", [0, 1])
    def test_rechecks_deleted_artifact(self, db_session, verified_pair, side):
        fingerprint = verified_pair[side]
        db_session.refresh(fingerprint)
        artifact = db_session.get(File, fingerprint.file_id)
        db_session.expunge(fingerprint)
        db_session.delete(artifact)
        db_session.commit()

        assert not verification_cache.reusable_pair(
            db_session, *verified_pair, sample_points=256
        )

    def test_rechecks_reassigned_artifact(self, db_session, verified_pair, make_model):
        first, _ = verified_pair
        artifact = db_session.get(File, first.file_id)
        artifact.model_id = make_model().id
        db_session.add(artifact)
        db_session.commit()

        assert not verification_cache.reusable_pair(
            db_session, *verified_pair, sample_points=256
        )

    def test_reuses_complete_exact_proof(self, db_session, verified_pair):
        assert verification_cache.reusable_pair(
            db_session, *verified_pair, sample_points=256
        )

    def test_rechecks_increased_sample_count(self, db_session, verified_pair):
        assert not verification_cache.reusable_pair(
            db_session, *verified_pair, sample_points=512
        )

    @pytest.mark.parametrize(
        "changed",
        ["first_state", "second_state", "algorithm", "input_hash", "component"],
    )
    def test_rechecks_changed_geometry(self, db_session, verified_pair, changed):
        first, second = verified_pair
        if changed == "first_state":
            first.state = "partial"
        elif changed == "second_state":
            second.state = "partial"
        elif changed == "algorithm":
            second.algorithm_version = "geometry-next"
        elif changed == "component":
            first.component_index = 1
        else:
            first.source_sha256 = "b" * 64

        assert not verification_cache.reusable_pair(
            db_session, first, second, sample_points=256
        )

    @pytest.mark.parametrize(
        "encoded",
        [
            "invalid",
            "[]",
            '{"exact_equivalence":true}',
            '{"exact_equivalence":true,"version":"old","sample_points":256}',
        ],
    )
    def test_rechecks_invalid_cached_recipe(self, db_session, verified_pair, encoded):
        observation = db_session.exec(select(SimilarityCandidateObservation)).one()
        observation.evidence_json = encoded
        db_session.add(observation)
        db_session.commit()

        assert not verification_cache.reusable_pair(
            db_session, *verified_pair, sample_points=256
        )

    def test_rechecks_approximate_evidence(
        self, db_session, make_model, make_file, make_geometry_fingerprint, exact_proof
    ):
        first = make_geometry_fingerprint(make_file(make_model()), state="ready")
        second = make_geometry_fingerprint(make_file(make_model()), state="ready")
        candidates.publish(
            db_session,
            first,
            second,
            replace(
                exact_proof,
                exact_equivalence=False,
                evidence_class="similar_shape",
                confidence=0.95,
            ),
        )

        assert not verification_cache.reusable_pair(
            db_session, first, second, sample_points=256
        )

    @pytest.mark.parametrize("count", [True, "256", 5001])
    def test_rechecks_invalid_sample_recipe(self, db_session, verified_pair, count):
        observation = db_session.exec(select(SimilarityCandidateObservation)).one()
        recipe = json.loads(observation.evidence_json)
        observation.evidence_json = json.dumps(recipe | {"sample_points": count})
        db_session.add(observation)
        db_session.commit()

        assert not verification_cache.reusable_pair(
            db_session, *verified_pair, sample_points=256
        )
