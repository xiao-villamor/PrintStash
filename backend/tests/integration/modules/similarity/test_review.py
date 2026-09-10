"""Human decisions are atomic, permission checked, replayable and durable."""

import json

import pytest
from sqlmodel import select

from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import CollectionRole, SimilarityReviewDecision
from app.modules.similarity import candidates, review
from tests.factories import (
    build_geometry_fingerprint,
    build_similarity_candidate,
    build_similarity_observation,
)


@pytest.fixture
def evidence_pair(db_session, make_model, make_file):
    a, b = make_model(), make_model()
    fa, fb = make_file(a), make_file(b)
    left = build_geometry_fingerprint(db_session, fa, state="ready")
    right = build_geometry_fingerprint(db_session, fb, state="ready")
    candidate = build_similarity_candidate(db_session, a, b)
    build_similarity_observation(db_session, candidate, left, right)
    return candidate, fa, fb


class TestReview:
    def test_confirms_evidence_without_family(
        self, db_session, make_user, evidence_pair
    ):
        candidate, fa, fb = evidence_pair
        before = (fa.sha256, fb.sha256, fa.model_id, fb.model_id)
        decision = review.decide(
            db_session,
            make_user(superuser=True),
            candidate.id,
            review.DecisionRequest(
                request_id="confirm-1", version=1, action="confirm_evidence"
            ),
        )
        db_session.refresh(candidate)
        assert candidate.review_state == "confirmed"
        assert candidate.resolution_kind == "evidence_only"
        assert decision.resolution_kind == "evidence_only"
        assert decision.target_id is None
        assert (fa.sha256, fb.sha256, fa.model_id, fb.model_id) == before
        assert json.loads(decision.snapshot_json)["model_a_id"] == fa.model_id

    def test_replays_same_request_without_second_decision(
        self, db_session, make_user, evidence_pair
    ):
        candidate, _, _ = evidence_pair
        actor = make_user(superuser=True)
        request = review.DecisionRequest(
            request_id="repeat", version=1, action="confirm_evidence"
        )
        first = review.decide(db_session, actor, candidate.id, request)
        second = review.decide(db_session, actor, candidate.id, request)
        assert first.id == second.id
        assert len(db_session.exec(select(SimilarityReviewDecision)).all()) == 1
        with pytest.raises(OperationError, match="similarity_request_conflict"):
            review.decide(
                db_session,
                actor,
                candidate.id,
                request.model_copy(update={"action": "reject"}),
            )

    def test_refuses_family_without_resolver(
        self, db_session, make_user, evidence_pair
    ):
        candidate, _, _ = evidence_pair
        with pytest.raises(OperationError, match="family_resolution_unavailable"):
            review.decide(
                db_session,
                make_user(superuser=True),
                candidate.id,
                review.DecisionRequest(
                    request_id="family", version=1, action="confirm_family"
                ),
            )
        db_session.refresh(candidate)
        assert candidate.review_state == "open"
        assert db_session.exec(select(SimilarityReviewDecision)).all() == []

    def test_refuses_outdated_candidate_version(
        self, db_session, make_user, evidence_pair
    ):
        candidate, _, _ = evidence_pair
        actor = make_user(superuser=True)
        review.decide(
            db_session,
            actor,
            candidate.id,
            review.DecisionRequest(request_id="one", version=1, action="later"),
        )
        with pytest.raises(OperationError, match="similarity_version_conflict"):
            review.decide(
                db_session,
                actor,
                candidate.id,
                review.DecisionRequest(request_id="two", version=1, action="reject"),
            )
        assert len(db_session.exec(select(SimilarityReviewDecision)).all()) == 1

    @pytest.mark.parametrize("change", ["hash", "trash_file", "trash_model", "purge"])
    def test_refuses_confirmation_of_stale_evidence(
        self, db_session, make_user, evidence_pair, change
    ):
        candidate, file, _ = evidence_pair
        if change == "hash":
            file.sha256 = "f" * 64
        elif change == "trash_file":
            file.deleted_at = utcnow()
        elif change == "purge":
            file.purge_token = "claimed"
        else:
            file.model.deleted_at = utcnow()
            db_session.add(file.model)
        db_session.add(file)
        db_session.commit()
        with pytest.raises(
            OperationError, match="similarity_(evidence_stale|candidate_not_found)"
        ):
            review.decide(
                db_session,
                make_user(superuser=True),
                candidate.id,
                review.DecisionRequest(
                    request_id="stale", version=1, action="confirm_evidence"
                ),
            )
        assert db_session.exec(select(SimilarityReviewDecision)).all() == []

    def test_requires_edit_on_both_endpoints(
        self, db_session, make_user, make_collection, grant_role, evidence_pair
    ):
        candidate, fa, fb = evidence_pair
        a, b = make_collection(path="first"), make_collection(path="second")
        fa.model.collection_id, fb.model.collection_id = a.id, b.id
        db_session.add(fa.model)
        db_session.add(fb.model)
        db_session.commit()
        editor = make_user()
        grant_role(editor, a, CollectionRole.EDIT)
        grant_role(editor, b, CollectionRole.VIEW)
        assert candidates.list_visible(db_session, editor).items == []
        with pytest.raises(OperationError, match="similarity_candidate_not_found"):
            review.decide(
                db_session,
                editor,
                candidate.id,
                review.DecisionRequest(request_id="hidden", version=1, action="reject"),
            )
        assert db_session.exec(select(SimilarityReviewDecision)).all() == []

    def test_retains_decision_after_candidate_purge(
        self, db_session, make_user, evidence_pair
    ):
        candidate, _, _ = evidence_pair
        decision = review.decide(
            db_session,
            make_user(superuser=True),
            candidate.id,
            review.DecisionRequest(
                request_id="durable", version=1, action="confirm_evidence"
            ),
        )
        snapshot = decision.snapshot_json
        db_session.delete(candidate)
        db_session.commit()
        db_session.refresh(decision)
        assert decision.candidate_id is None
        assert decision.snapshot_json == snapshot
        assert decision.after_state == "confirmed"

    def test_preserves_existing_multipart_choices(
        self, db_session, make_user, evidence_pair, make_model, make_multipart_model
    ):
        from app.db.models import MultipartModelChoice, MultipartPart
        from app.modules.library import multipart_models
        from app.schemas.multipart_models import MultipartPartWrite

        candidate, fa, _ = evidence_pair
        actor = make_user(superuser=True)
        existing = make_multipart_model()
        original = make_model()
        multipart_models.save(
            db_session,
            actor,
            existing,
            [
                MultipartPartWrite(
                    name="Existing part", model_ids=[original.id], quantity=3
                )
            ],
        )
        previous = db_session.exec(select(MultipartModelChoice)).one()
        identity = (previous.id, previous.model_id, previous.multipart_part_id)
        candidate.evidence_class = "plate_of"
        candidate.summary_json = json.dumps(
            {"composition": [{"model_id": fa.model_id, "quantity": 6}]}
        )
        db_session.add(candidate)
        db_session.commit()
        decision = review.decide(
            db_session,
            actor,
            candidate.id,
            review.DecisionRequest(
                request_id="append",
                version=1,
                action="create_multipart",
                target_id=existing.id,
                parts=[
                    MultipartPartWrite(
                        name="New part", model_ids=[fa.model_id], quantity=6
                    )
                ],
            ),
        )
        assert decision.target_id == existing.id
        rows = db_session.exec(
            select(MultipartModelChoice).order_by(MultipartModelChoice.id)
        ).all()
        assert [(row.id, row.model_id, row.multipart_part_id) for row in rows[:1]] == [
            identity
        ]
        assert rows[1].model_id == fa.model_id
        parts = db_session.exec(
            select(MultipartPart).order_by(MultipartPart.sort_order)
        ).all()
        assert [(part.name, part.quantity) for part in parts] == [
            ("Existing part", 3),
            ("New part", 6),
        ]

    def test_rolls_back_duplicate_multipart_member(
        self, db_session, make_user, evidence_pair, make_multipart_model
    ):
        from app.db.models import MultipartModelChoice
        from app.modules.library import multipart_models
        from app.schemas.multipart_models import MultipartPartWrite

        candidate, fa, _ = evidence_pair
        actor = make_user(superuser=True)
        existing = make_multipart_model()
        multipart_models.save(
            db_session,
            actor,
            existing,
            [
                MultipartPartWrite(
                    name="Already present", model_ids=[fa.model_id], quantity=2
                )
            ],
        )
        candidate.evidence_class = "plate_of"
        candidate.summary_json = json.dumps(
            {"composition": [{"model_id": fa.model_id, "quantity": 6}]}
        )
        db_session.add(candidate)
        db_session.commit()
        with pytest.raises(OperationError, match="multipart_model_duplicate_member"):
            review.decide(
                db_session,
                actor,
                candidate.id,
                review.DecisionRequest(
                    request_id="duplicate",
                    version=1,
                    action="create_multipart",
                    target_id=existing.id,
                    parts=[
                        MultipartPartWrite(
                            name="New part", model_ids=[fa.model_id], quantity=6
                        )
                    ],
                ),
            )
        db_session.refresh(candidate)
        assert candidate.review_state == "open"
        assert candidate.version == 1
        assert len(db_session.exec(select(MultipartModelChoice)).all()) == 1
        assert db_session.exec(select(SimilarityReviewDecision)).all() == []


class TestResolutionValidation:
    @pytest.mark.parametrize(
        "field,value", [("name", "Unexpected"), ("target_id", 2), ("collection_id", 2)]
    )
    def test_evidence_review_refuses_grouping_payload(
        self, db_session, make_user, evidence_pair, field, value
    ):
        candidate, *_ = evidence_pair
        with pytest.raises(
            OperationError, match="similarity_resolution_payload_invalid"
        ):
            review.decide(
                db_session,
                make_user(superuser=True),
                candidate.id,
                review.DecisionRequest(
                    request_id="extra-payload",
                    version=1,
                    action="confirm_evidence",
                    **{field: value},
                ),
            )
        db_session.refresh(candidate)
        assert candidate.review_state == "open"
        assert db_session.exec(select(SimilarityReviewDecision)).all() == []

    @pytest.mark.parametrize(
        "scenario,code",
        [
            ("wrong_class", "similarity_multipart_requires_composition"),
            ("no_parts", "similarity_multipart_requires_composition"),
            ("unrelated", "similarity_multipart_unrelated_member"),
            ("no_evidence", "similarity_multipart_evidence_missing"),
            ("quantity", "similarity_multipart_composition_changed"),
            ("no_name", "similarity_multipart_name_required"),
            ("target_name", "similarity_resolution_payload_invalid"),
            ("missing_target", "multipart_model_not_found"),
        ],
    )
    def test_invalid_composition_rolls_back_review(
        self, db_session, make_user, evidence_pair, make_model, scenario, code
    ):
        from app.schemas.multipart_models import MultipartPartWrite

        candidate, file, _ = evidence_pair
        candidate.evidence_class = (
            "identical_geometry" if scenario == "wrong_class" else "plate_of"
        )
        candidate.summary_json = (
            "{}"
            if scenario == "no_evidence"
            else json.dumps(
                {"composition": [{"model_id": file.model_id, "quantity": 6}]}
            )
        )
        db_session.add(candidate)
        db_session.commit()
        part_model = make_model().id if scenario == "unrelated" else file.model_id
        request = review.DecisionRequest(
            request_id="invalid-composition",
            version=1,
            action="create_multipart",
            name=None if scenario in ("no_name", "missing_target") else "Assembly",
            target_id=999 if scenario in ("target_name", "missing_target") else None,
            parts=None
            if scenario == "no_parts"
            else [
                MultipartPartWrite(
                    name="Part",
                    model_ids=[part_model],
                    quantity=5 if scenario == "quantity" else 6,
                )
            ],
        )
        with pytest.raises(OperationError, match=code):
            review.decide(db_session, make_user(superuser=True), candidate.id, request)
        db_session.refresh(candidate)
        assert candidate.review_state == "open"
        assert candidate.version == 1
        assert db_session.exec(select(SimilarityReviewDecision)).all() == []
