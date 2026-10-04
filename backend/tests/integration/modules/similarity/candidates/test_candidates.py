"""Visible candidate projections preserve lineage and select persisted evidence without geometry work."""

import json

import pytest
from sqlmodel import select

from app.core.errors import OperationError
from app.db.models import (
    FileRevisionStatus,
    FileType,
    GeometryFingerprint,
    SimilarityReviewDecision,
)
from app.modules.media.fingerprints import ALGORITHM_VERSION
from app.modules.similarity import candidates, review
from tests.factories import (
    build_geometry_fingerprint,
    build_similarity_candidate,
    build_similarity_observation,
)


@pytest.fixture
def pair(db_session, make_model, make_file):
    a, b = make_model(name="Bracket"), make_model(name="Bracket copy")
    fa, fb = make_file(a, file_type=FileType.STL), make_file(b, file_type=FileType.OBJ)
    left = build_geometry_fingerprint(
        db_session, fa, state="ready", surface_area=120, volume=75
    )
    right = build_geometry_fingerprint(db_session, fb, state="ready")
    row = build_similarity_candidate(db_session, a, b)
    observation = build_similarity_observation(db_session, row, left, right)
    return row, observation, fa, fb


class TestCandidateProjection:
    def test_includes_authorized_model_labels(self, db_session, pair, make_user):
        row, _, fa, fb = pair
        page = candidates.list_visible(db_session, make_user(superuser=True))
        assert [
            (item["model_a"]["name"], item["model_b"]["name"]) for item in page.items
        ] == [("Bracket", "Bracket copy")]
        assert page.items[0]["model_a"]["id"] == fa.model_id
        assert page.items[0]["model_b"]["id"] == fb.model_id

    def test_projects_measured_lineage_sources(self, db_session, pair):
        row, observation, fa, _ = pair
        row.primary_lineage_key = observation.lineage_key
        db_session.add(row)
        db_session.commit()
        detail = candidates.project(db_session, row, detail=True)
        assert detail["observations"][0]["source_a"]["file_id"] == fa.id
        assert detail["observations"][0]["source_a"]["surface_area"] == 120
        assert detail["primary_lineage_key"] == observation.lineage_key
        assert "storage_key" not in json.dumps(detail, default=str)

    @pytest.mark.parametrize(
        "file_type,expected",
        [(FileType.STL, 1), (FileType.OBJ, 1), (FileType.THREE_MF, 0)],
    )
    def test_filters_artifact_format(
        self, db_session, pair, make_user, file_type, expected
    ):
        page = candidates.list_visible(
            db_session, make_user(superuser=True), file_type=file_type
        )
        assert len(page.items) == expected

    @pytest.mark.parametrize("source,expected", [("vault", 1), ("external", 0)])
    def test_filters_storage_source(
        self, db_session, pair, make_user, source, expected
    ):
        assert (
            len(
                candidates.list_visible(
                    db_session, make_user(superuser=True), source=source
                ).items
            )
            == expected
        )

    def test_filters_known_good_revision(
        self, db_session, pair, make_user, make_file, make_model
    ):
        row, _, fa, _ = pair
        actor = make_user(superuser=True)
        assert candidates.list_visible(db_session, actor, known_good=True).items == []
        from app.db.models import Model

        make_file(
            db_session.get(Model, fa.model_id),
            file_type=FileType.GCODE,
            status=FileRevisionStatus.KNOWN_GOOD,
        )
        assert [
            item["id"]
            for item in candidates.list_visible(
                db_session, actor, known_good=True
            ).items
        ] == [row.id]
        assert candidates.list_visible(db_session, actor, known_good=False).items == []

    def test_filters_descendant_collection(
        self, db_session, pair, make_user, make_collection
    ):
        from app.db.models import Model

        row, _, fa, _ = pair
        parent = make_collection(name="Parts", path="parts")
        child = make_collection(
            name="Brackets", path="parts/brackets", parent_id=parent.id
        )
        model = db_session.get(Model, fa.model_id)
        model.collection_id = child.id
        db_session.add(model)
        db_session.commit()
        actor = make_user(superuser=True)
        assert [
            item["id"]
            for item in candidates.list_visible(
                db_session, actor, collection_id=parent.id
            ).items
        ] == [row.id]
        assert (
            candidates.list_visible(db_session, actor, collection_id=9999).items == []
        )

    def test_threshold_selects_existing_evidence(self, db_session, pair, make_user):
        row, *_ = pair
        row.confidence = 0.8
        db_session.add(row)
        db_session.commit()
        actor = make_user(superuser=True)
        assert (
            candidates.list_visible(db_session, actor, minimum_confidence=0.9).items
            == []
        )
        assert (
            candidates.list_visible(db_session, actor, minimum_confidence=0.7).items[0][
                "id"
            ]
            == row.id
        )


class TestInterpretationVersion:
    @pytest.mark.parametrize(
        "algorithm_version,expected_current",
        [
            ("geometry-v4-sh5f4577c4", False),
            ("geometry-v6-sh5f4577c4", False),
            (ALGORITHM_VERSION, True),
        ],
        ids=["previous", "previous-reader", "current"],
    )
    def test_version_controls_current_evidence(
        self, db_session, pair, make_user, algorithm_version, expected_current
    ):
        candidate, observation, _, _ = pair
        candidate.algorithm_version = algorithm_version
        db_session.add(candidate)
        for fingerprint_id in (
            observation.fingerprint_a_id,
            observation.fingerprint_b_id,
        ):
            fingerprint = db_session.get(GeometryFingerprint, fingerprint_id)
            fingerprint.algorithm_version = algorithm_version
            db_session.add(fingerprint)
        db_session.commit()
        actor = make_user(superuser=True)

        assert candidates.is_current(db_session, candidate) is expected_current
        detail = candidates.project(db_session, candidate, detail=True)
        assert detail["freshness"] == ("current" if expected_current else "stale")
        assert ("confirm_evidence" in detail["allowed_actions"]) is expected_current
        current = candidates.list_visible(db_session, actor, freshness="current")
        stale = candidates.list_visible(db_session, actor, freshness="stale")
        assert [item["id"] for item in current.items] == (
            [candidate.id] if expected_current else []
        )
        assert [item["id"] for item in stale.items] == (
            [] if expected_current else [candidate.id]
        )
        assert len(detail["observations"]) == 1
        assert detail["observations"][0]["source_a"]["surface_area"] == 120

    def test_version_change_preserves_review_history(self, db_session, pair, make_user):
        candidate, observation, _, _ = pair
        actor = make_user(superuser=True)
        decision = review.decide(
            db_session,
            actor,
            candidate.id,
            review.DecisionRequest(
                request_id="prior-confirmation",
                version=candidate.version,
                action="confirm_evidence",
            ),
        )
        db_session.refresh(candidate)
        snapshot = decision.snapshot_json
        old_version = "geometry-v4-sh5f4577c4"
        candidate.algorithm_version = old_version
        db_session.add(candidate)
        for fingerprint_id in (
            observation.fingerprint_a_id,
            observation.fingerprint_b_id,
        ):
            fingerprint = db_session.get(GeometryFingerprint, fingerprint_id)
            fingerprint.algorithm_version = old_version
            db_session.add(fingerprint)
        db_session.commit()

        detail = candidates.project(db_session, candidate, detail=True)
        assert detail["freshness"] == "stale"
        assert detail["review_state"] == "confirmed"
        assert "confirm_evidence" not in detail["allowed_actions"]
        db_session.refresh(decision)
        assert decision.after_state == "confirmed"
        assert decision.snapshot_json == snapshot
        assert len(db_session.exec(select(SimilarityReviewDecision)).all()) == 1
        assert len(db_session.exec(select(GeometryFingerprint)).all()) == 2
        assert len(detail["observations"]) == 1
        with pytest.raises(OperationError, match="similarity_evidence_stale"):
            review.decide(
                db_session,
                actor,
                candidate.id,
                review.DecisionRequest(
                    request_id="obsolete-confirmation",
                    version=candidate.version,
                    action="confirm_evidence",
                ),
            )
        assert len(db_session.exec(select(SimilarityReviewDecision)).all()) == 1
