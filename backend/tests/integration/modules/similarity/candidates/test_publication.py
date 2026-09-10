"""Persisted evidence retains independent lineage and human decisions across reruns."""

from dataclasses import replace
from datetime import timedelta

import pytest
from printstash_core.mesh.similarity.verification import verify_meshes
from sqlmodel import select

from app.core.time import utcnow
from app.db.models import SimilarityCandidate, SimilarityCandidateObservation
from app.modules.similarity import candidates, review
from tests.factories.geometry import tetrahedron


@pytest.fixture(scope="module")
def exact_proof():
    mesh = tetrahedron()
    return verify_meshes(
        mesh.vertices, mesh.faces, mesh.vertices, mesh.faces, sample_points=256
    )


@pytest.fixture
def published_pair(
    db_session, make_model, make_file, make_geometry_fingerprint, exact_proof
):
    files = [make_file(make_model()) for _ in range(2)]
    fingerprints = [make_geometry_fingerprint(file, state="ready") for file in files]
    row = candidates.publish(db_session, *fingerprints, exact_proof)
    assert row is not None
    return row, files, fingerprints


class TestPublication:
    def test_retains_distinct_artifact_observations(
        self,
        db_session,
        published_pair,
        exact_proof,
        make_file,
        make_geometry_fingerprint,
    ):
        row, files, _ = published_pair
        original = candidates.project(db_session, row, detail=True)
        extra = [
            make_geometry_fingerprint(make_file(file.model), state="ready")
            for file in files
        ]
        weaker = replace(
            exact_proof,
            evidence_class="similar_shape",
            confidence=0.91,
            exact_equivalence=False,
        )
        repeated = candidates.publish(db_session, *extra, weaker)
        detail = candidates.project(db_session, repeated, detail=True)
        assert len(detail["observations"]) == 2
        assert len({item["lineage_key"] for item in detail["observations"]}) == 2
        assert detail["primary_lineage_key"] == original["primary_lineage_key"]
        assert detail["evidence_class"] == "identical_geometry"
        assert detail["version"] == original["version"] + 1

    def test_repeated_lineage_does_not_change_version(
        self, db_session, published_pair, exact_proof
    ):
        row, _, fingerprints = published_pair
        version = row.version
        candidates.publish(db_session, *fingerprints, exact_proof)
        db_session.refresh(row)
        assert row.version == version
        assert len(db_session.exec(select(SimilarityCandidateObservation)).all()) == 1

    def test_rejected_pair_stays_rejected_for_same_algorithm(
        self,
        db_session,
        published_pair,
        exact_proof,
        make_user,
        make_file,
        make_geometry_fingerprint,
    ):
        row, files, _ = published_pair
        decision = review.decide(
            db_session,
            make_user(superuser=True),
            row.id,
            review.DecisionRequest(
                request_id="reject-pair", version=row.version, action="reject"
            ),
        )
        extra = [
            make_geometry_fingerprint(make_file(file.model), state="ready")
            for file in files
        ]
        candidates.publish(db_session, *extra, exact_proof)
        db_session.refresh(row)
        assert row.review_state == "rejected"
        assert (
            candidates.list_visible(
                db_session, make_user(superuser=True), review_state="open"
            ).items
            == []
        )
        db_session.refresh(decision)
        assert decision.after_state == "rejected"

    def test_new_algorithm_links_preceding_decision(
        self,
        db_session,
        published_pair,
        exact_proof,
        make_user,
        make_geometry_fingerprint,
    ):
        old, files, _ = published_pair
        review.decide(
            db_session,
            make_user(superuser=True),
            old.id,
            review.DecisionRequest(
                request_id="old-rejection", version=old.version, action="reject"
            ),
        )
        changed = [
            make_geometry_fingerprint(
                file, state="ready", algorithm_version="geometry-next"
            )
            for file in files
        ]
        new = candidates.publish(db_session, *changed, exact_proof)
        assert new.id != old.id
        assert new.reconsidered_candidate_id == old.id
        assert new.review_state == "open"
        db_session.refresh(old)
        assert old.review_state == "rejected"

    @pytest.mark.parametrize("change", ["hash", "trash", "purge", "tombstone"])
    def test_source_lifecycle_preserves_confirmed_evidence(
        self,
        db_session,
        published_pair,
        make_user,
        make_external_library,
        tmp_path,
        change,
    ):
        row, files, _ = published_pair
        decision = review.decide(
            db_session,
            make_user(superuser=True),
            row.id,
            review.DecisionRequest(
                request_id="confirm", version=row.version, action="confirm_evidence"
            ),
        )
        snapshot = decision.snapshot_json
        source = files[0]
        if change == "hash":
            source.sha256 = "e" * 64
        elif change == "trash":
            source.deleted_at = utcnow()
        elif change == "purge":
            source.purge_token = "gc-claimed"
        else:
            from app.modules.library.trash import record_source_tombstone

            library = make_external_library(tmp_path)
            source.is_external = True
            source.external_library_id = library.id
            source.source_key = "removed.stl"
            record_source_tombstone(db_session, source, "deleted")
        db_session.add(source)
        db_session.commit()
        detail = candidates.project(db_session, row, detail=True)
        assert detail["freshness"] == "stale"
        assert detail["stale_reason"] == "source_changed_or_unavailable"
        assert detail["review_state"] == "confirmed"
        assert detail["resolution_kind"] == "evidence_only"
        assert "confirm_evidence" not in detail["allowed_actions"]
        db_session.refresh(decision)
        assert decision.snapshot_json == snapshot
        assert decision.target_id is None

    def test_partial_inputs_cannot_publish_exact_proof(
        self, db_session, published_pair, exact_proof
    ):
        _, _, fingerprints = published_pair
        fingerprints[0].state = "partial"
        db_session.add(fingerprints[0])
        db_session.commit()
        with pytest.raises(ValueError, match="partial_evidence_cannot_be_exact"):
            candidates.publish(db_session, *fingerprints, exact_proof)

    def test_different_algorithms_do_not_mix(
        self, db_session, published_pair, exact_proof
    ):
        _, _, fingerprints = published_pair
        fingerprints[0].algorithm_version = "different"
        db_session.add(fingerprints[0])
        db_session.commit()
        assert candidates.publish(db_session, *fingerprints, exact_proof) is None

    def test_stale_sources_cannot_add_observations(
        self, db_session, published_pair, exact_proof
    ):
        _, files, fingerprints = published_pair
        files[0].sha256 = "f" * 64
        db_session.add(files[0])
        db_session.commit()
        assert candidates.publish(db_session, *fingerprints, exact_proof) is None
        assert len(db_session.exec(select(SimilarityCandidateObservation)).all()) == 1

    def test_refuses_unordered_model_transform(
        self, db_session, published_pair, exact_proof
    ):
        _, _, fingerprints = published_pair
        with pytest.raises(
            ValueError, match="similarity_verification_requires_ordered_models"
        ):
            candidates.publish(db_session, *reversed(fingerprints), exact_proof)

    @pytest.mark.parametrize("fence", ["expired", "cancelled", "reclaimed"])
    def test_fences_worker_publication(
        self,
        db_session,
        published_pair,
        exact_proof,
        make_similarity_run,
        make_user,
        fence,
    ):
        row, _, fingerprints = published_pair
        run = make_similarity_run(
            make_user(superuser=True),
            lease_token="owner",
            lease_expires_at=utcnow() + timedelta(minutes=5),
        )
        if fence == "expired":
            run.lease_expires_at = utcnow() - timedelta(seconds=1)
        elif fence == "cancelled":
            run.cancel_requested = True
        else:
            run.lease_token = "new-owner"
        db_session.add(run)
        db_session.commit()
        assert (
            candidates.publish(
                db_session, *fingerprints, exact_proof, run=run, run_token="owner"
            )
            is None
        )
        assert len(db_session.exec(select(SimilarityCandidate)).all()) == 1
        assert len(db_session.exec(select(SimilarityCandidateObservation)).all()) == 1

    def test_unclassified_evidence_does_not_publish(
        self, db_session, published_pair, exact_proof
    ):
        _, _, fingerprints = published_pair
        assert (
            candidates.publish(
                db_session, *fingerprints, replace(exact_proof, evidence_class=None)
            )
            is None
        )

    def test_empty_detail_retains_stale_state(
        self, db_session, make_similarity_candidate, make_model
    ):
        row = make_similarity_candidate(make_model(), make_model())
        detail = candidates.project(db_session, row, detail=True)
        assert detail["observations"] == []
        assert detail["freshness"] == "stale"
        assert detail["observations_truncated"] is False
