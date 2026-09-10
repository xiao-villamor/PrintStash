"""Database constraints protect evidence lineage and durable review history."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.models import (
    GeometryFingerprint,
    SimilarityCandidate,
    SimilarityCandidateObservation,
    SimilarityReviewDecision,
)
from tests import factories as f


class TestFingerprintIdentity:
    def test_separates_changed_source_hash(self, db_session, make_model, make_file):
        file = make_file(make_model())
        first = f.build_geometry_fingerprint(db_session, file)
        second = f.build_geometry_fingerprint(db_session, file, source_sha256="d" * 64)

        assert first.id != second.id
        assert first.source_sha256 == file.sha256

    def test_rejects_duplicate_source_component(
        self, db_session, make_model, make_file
    ):
        file = make_file(make_model())
        f.build_geometry_fingerprint(db_session, file)

        with pytest.raises(IntegrityError):
            f.build_geometry_fingerprint(db_session, file)
        db_session.rollback()

        assert len(db_session.exec(select(GeometryFingerprint)).all()) == 1

    def test_retains_observation_after_fingerprint_purge(
        self, db_session, make_model, make_file
    ):
        first, second = make_model(), make_model()
        a = f.build_geometry_fingerprint(db_session, make_file(first))
        b = f.build_geometry_fingerprint(db_session, make_file(second))
        candidate = f.build_similarity_candidate(db_session, first, second)
        observation = f.build_similarity_observation(db_session, candidate, a, b)
        original_hash = a.source_sha256

        db_session.delete(a)
        db_session.commit()
        db_session.expire_all()

        observed = db_session.get(SimilarityCandidateObservation, observation.id)
        assert observed.fingerprint_a_id is None
        assert observed.input_hash_a == original_hash
        assert observed.fingerprint_b_id == b.id


class TestReviewIdentity:
    def test_rejects_unordered_pair(self, db_session, make_model):
        first, second = make_model(), make_model()

        with pytest.raises(IntegrityError):
            f.build_similarity_candidate(
                db_session, first, second, model_a_id=second.id, model_b_id=first.id
            )
        db_session.rollback()

    def test_rejects_repeated_algorithm_pair(self, db_session, make_model):
        first, second = make_model(), make_model()
        f.build_similarity_candidate(db_session, first, second)

        with pytest.raises(IntegrityError):
            f.build_similarity_candidate(db_session, second, first)
        db_session.rollback()

    def test_preserves_review_after_candidate_purge(
        self, db_session, make_model, make_user
    ):
        candidate = f.build_similarity_candidate(db_session, make_model(), make_model())
        decision = f.build_similarity_decision(db_session, candidate, make_user())
        snapshot = decision.snapshot_json

        db_session.delete(candidate)
        db_session.commit()
        db_session.expire_all()

        history = db_session.get(SimilarityReviewDecision, decision.id)
        assert history.candidate_id is None
        assert history.snapshot_json == snapshot
        assert history.resolution_kind == "evidence_only"

    def test_retains_human_state_separate_from_freshness(self, db_session, make_model):
        candidate = f.build_similarity_candidate(
            db_session,
            make_model(),
            make_model(),
            review_state="confirmed",
            resolution_kind="evidence_only",
        )

        candidate.freshness = "stale"
        candidate.stale_reason = "source_changed"
        db_session.add(candidate)
        db_session.commit()
        db_session.expire_all()

        result = db_session.get(SimilarityCandidate, candidate.id)
        assert result.review_state == "confirmed"
        assert result.resolution_kind == "evidence_only"
        assert result.freshness == "stale"

    def test_rejects_duplicate_request(self, db_session, make_model, make_user):
        candidate = f.build_similarity_candidate(db_session, make_model(), make_model())
        actor = make_user()
        f.build_similarity_decision(
            db_session, candidate, actor, request_id="one-request"
        )

        with pytest.raises(IntegrityError):
            f.build_similarity_decision(
                db_session, candidate, actor, request_id="one-request"
            )
        db_session.rollback()


class TestSharedVectorIdentity:
    def test_separates_artifacts_of_the_same_model(
        self, db_session, make_model, make_file
    ):
        model = make_model()
        space = f.build_embedding_space(db_session)
        generation = f.build_index_generation(db_session, space)

        first = f.build_passage_vector(db_session, generation, make_file(model))
        second = f.build_passage_vector(db_session, generation, make_file(model))

        assert first.unit_key != second.unit_key
        assert first.model_id == second.model_id
        assert first.vector_blob == second.vector_blob == b"\x00\x00\x80?" + b"\x00" * 8

    def test_allows_one_active_generation_per_profile(self, db_session):
        space = f.build_embedding_space(db_session)
        f.build_index_generation(db_session, space)

        with pytest.raises(IntegrityError):
            f.build_index_generation(db_session, space)
        db_session.rollback()
        retired = f.build_index_generation(db_session, space, active=False)

        assert retired.active_profile_key is None
