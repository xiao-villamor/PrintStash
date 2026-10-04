"""Derivative failures never roll back committed Artifacts or schedule unusable work."""

import pytest
from sqlmodel import select

from app.db.models import GeometryFingerprint, JobKind, SimilarityRun
from app.db.session import get_session_factory
from app.modules.media.fingerprints import (
    FingerprintResult,
    FingerprintResultState,
    extract,
)
from app.modules.media.mesh_facts import FingerprintFailureCode
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
                get_session_factory(),
                999,
                None,
                FingerprintResult(
                    FingerprintResultState.FAILED,
                    failure_code=FingerprintFailureCode.SOURCE_UNAVAILABLE,
                ),
                source_sha256="a" * 64,
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
            FingerprintResult(
                FingerprintResultState.FAILED,
                failure_code=FingerprintFailureCode.INVALID_GEOMETRY,
            ),
            source_sha256=file.sha256,
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
            source_sha256=file.sha256,
        )
        assert state == "ready"
        assert db_session.exec(select(SimilarityRun)).all() == []
        assert all(
            row.state == "ready" for row in db_session.exec(select(GeometryFingerprint))
        )

    def test_a_derivative_fingerprint_starts_a_system_run_for_its_model(
        self, db_session, make_file, make_model, make_user, monkeypatch
    ):
        # The mesh derivative has no requesting user. Regression: its
        # fingerprints were published without ever starting the analysis that
        # an upload used to start, so no candidates appeared after ingest.
        import app.modules.work as work

        admin = make_user(superuser=True)
        make_user()
        configuration.update_settings(db_session, admin, {"enabled": True})
        model = make_model()
        file = make_file(model)
        nudged: list[str] = []
        monkeypatch.setattr(work, "nudge", lambda name, **_: nudged.append(name))

        state = ingestion.after_commit(
            get_session_factory(),
            file.id,
            None,
            extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
            source_sha256=file.sha256,
        )

        assert state == "ready"
        run = db_session.exec(select(SimilarityRun)).one()
        assert (run.actor_id, run.trigger, run.scope) == (admin.id, "ingest", "models")
        assert run.scope_ids_json == f"[{model.id}]"
        assert nudged == [JobKind.SIMILARITY_ANALYZE]

    def test_no_active_administrator_means_no_system_run(
        self, db_session, make_file, make_model, make_user
    ):
        admin = make_user(superuser=True)
        configuration.update_settings(db_session, admin, {"enabled": True})
        admin.is_active = False
        db_session.add(admin)
        db_session.commit()
        file = make_file(make_model())

        ingestion.after_commit(
            get_session_factory(),
            file.id,
            None,
            extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
            source_sha256=file.sha256,
        )

        assert db_session.exec(select(SimilarityRun)).all() == []

    def test_rejects_fingerprints_computed_from_replaced_source_bytes(
        self, db_session, make_file, make_model
    ):
        file = make_file(make_model())
        source_sha256 = file.sha256
        result = extract(prepare_loaded_mesh(tetrahedron(), file_type="stl"))
        file.sha256 = "b" * 64
        db_session.add(file)
        db_session.commit()

        state = ingestion.after_commit(
            get_session_factory(), file.id, None, result, source_sha256=source_sha256
        )

        assert state == "stale"
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    def test_respects_ingest_fingerprint_switch(self, db_session, make_user):
        actor = make_user(superuser=True)
        configuration.update_settings(
            db_session, actor, {"enabled": True, "fingerprint_on_ingest": False}
        )
        assert ingestion.extraction_options(get_session_factory()) == {}

    @pytest.mark.parametrize("retired", ["cancelled", "retried"])
    @pytest.mark.parametrize("cached", [False, True], ids=["new-cache", "ready-cache"])
    def test_retired_execution_cannot_schedule_similarity_work(
        self, db_session, make_file, make_model, make_user, make_job, retired, cached
    ):
        from app.db.models import JobState
        from app.modules.similarity.fingerprints import publish_precomputed
        from app.modules.work.contracts import JobExecution

        actor = make_user(superuser=True)
        configuration.update_settings(db_session, actor, {"enabled": True})
        file = make_file(make_model())
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=f"file/{file.id}",
            state=JobState.RUNNING,
            attempts=1,
        )
        execution = JobExecution(job.id, 1, job.execution_epoch)
        result = extract(prepare_loaded_mesh(tetrahedron(), file_type="stl"))
        before = None
        if cached:
            assert publish_precomputed(db_session, file, result) == "ready"
            before = (
                db_session.exec(
                    select(GeometryFingerprint).where(
                        GeometryFingerprint.component_index == 0
                    )
                )
                .one()
                .model_dump()
            )
        if retired == "cancelled":
            job.state = JobState.CANCELLED
        else:
            job.execution_epoch = "new-execution"
        db_session.add(job)
        db_session.commit()

        state = ingestion.after_commit(
            get_session_factory(),
            file.id,
            actor.id,
            FingerprintResult(
                FingerprintResultState.FAILED,
                failure_code=FingerprintFailureCode.ANALYSIS_FAILED,
            )
            if cached
            else result,
            source_sha256=file.sha256,
            execution=execution,
        )

        assert state == "ready"
        db_session.expire_all()
        fingerprint = db_session.exec(
            select(GeometryFingerprint).where(GeometryFingerprint.component_index == 0)
        ).one()
        assert fingerprint.state == "ready"
        if before is not None:
            assert fingerprint.model_dump() == before
        assert db_session.exec(select(SimilarityRun)).all() == []
