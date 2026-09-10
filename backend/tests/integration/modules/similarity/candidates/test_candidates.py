"""Visible candidate projections preserve lineage and select persisted evidence without geometry work."""

import json

import pytest

from app.db.models import FileRevisionStatus, FileType
from app.modules.similarity import candidates
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
