"""Derivative failures never roll back committed Artifacts or schedule unusable work."""

import pytest
from sqlmodel import select

from app.db.models import GeometryFingerprint, SimilarityRun
from app.db.session import get_session_factory
from app.modules.media.fingerprints import FingerprintResult, extract
from app.modules.media.mesh_resources import prepare_loaded_mesh
from app.modules.similarity import configuration, ingestion
from tests.factories.geometry import tetrahedron


class TestIngestDerivative:
    def test_invalid_configuration_does_not_request_extraction(
        self, make_system_config
    ):
        make_system_config(similarity_settings_json="not-json")
        assert ingestion.extraction_options(get_session_factory()) == {}

    def test_missing_artifact_is_stale(self):
        assert (
            ingestion.after_commit(
                get_session_factory(), 999, None, FingerprintResult("failed")
            )
            == "stale"
        )

    def test_persists_failed_derivative_without_starting_run(
        self, db_session, make_file, make_model, make_user
    ):
        file = make_file(make_model())
        original = (file.sha256, file.path, file.model_id)
        actor = make_user(superuser=True)
        state = ingestion.after_commit(
            get_session_factory(),
            file.id,
            actor.id,
            FingerprintResult("failed", failure_code="invalid_geometry"),
        )
        assert state == "failed"
        db_session.refresh(file)
        assert (file.sha256, file.path, file.model_id) == original
        assert (
            db_session.exec(select(GeometryFingerprint)).one().failure_code
            == "invalid_geometry"
        )
        assert db_session.exec(select(SimilarityRun)).all() == []

    @pytest.mark.parametrize("actor_state", ["absent", "inactive", "disabled"])
    def test_retains_derivative_without_eligible_run(
        self, db_session, make_file, make_model, make_user, actor_state
    ):
        file = make_file(make_model())
        actor = make_user(superuser=True, active=actor_state != "inactive")
        result = extract(prepare_loaded_mesh(tetrahedron(), file_type="stl"))
        state = ingestion.after_commit(
            get_session_factory(),
            file.id,
            None if actor_state == "absent" else actor.id,
            result,
        )
        assert state == "ready"
        assert db_session.exec(select(SimilarityRun)).all() == []
        assert all(
            row.state == "ready" for row in db_session.exec(select(GeometryFingerprint))
        )

    def test_respects_ingest_fingerprint_switch(self, db_session, make_user):
        actor = make_user(superuser=True)
        configuration.update_settings(
            db_session, actor, {"enabled": True, "fingerprint_on_ingest": False}
        )
        assert ingestion.extraction_options(get_session_factory()) == {}
