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


class TestIngestionBoundaries:
    def test_enabled_extraction_uses_configured_cap(self, db_session, make_user):
        actor = make_user(superuser=True)
        configuration.update_settings(
            db_session, actor, {"enabled": True, "triangle_cap": 100}
        )
        assert ingestion.extraction_options(get_session_factory()) == {
            "include_fingerprint": True,
            "triangle_cap": 100,
        }

    @pytest.mark.parametrize("blocked", ["active-run", "inaccessible-model"])
    def test_post_commit_keeps_cache_when_analysis_cannot_start(
        self, db_session, make_user, make_model, make_file, blocked
    ):
        from app.modules.similarity import runs

        admin = make_user(superuser=True)
        configuration.update_settings(db_session, admin, {"enabled": True})
        file = make_file(make_model())
        actor = admin if blocked == "active-run" else make_user()
        existing = (
            runs.start(db_session, admin, scope="models", ids=[file.model_id])
            if blocked == "active-run"
            else None
        )
        state = ingestion.after_commit(
            get_session_factory(),
            file.id,
            actor.id,
            extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
            source_sha256=file.sha256,
        )
        assert state == "ready"
        assert {row.state for row in db_session.exec(select(GeometryFingerprint))} == {
            "ready"
        }
        assert [run.id for run in db_session.exec(select(SimilarityRun))] == (
            [existing.id] if existing else []
        )
        db_session.refresh(file)
        assert file.sha256 is not None

    def test_post_commit_propagates_invalid_configuration(
        self, db_session, make_user, make_model, make_file, make_system_config
    ):
        from app.core.errors import OperationError

        actor = make_user(superuser=True)
        make_system_config(similarity_settings_json="not-json")
        file = make_file(make_model())
        original = file.sha256
        with pytest.raises(OperationError, match="similarity_configuration_invalid"):
            ingestion.after_commit(
                get_session_factory(),
                file.id,
                actor.id,
                extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
                source_sha256=original,
            )
        db_session.expire_all()
        assert db_session.exec(select(SimilarityRun)).all() == []
        assert {row.state for row in db_session.exec(select(GeometryFingerprint))} == {
            "ready"
        }
        assert file.sha256 == original

    @pytest.mark.parametrize("blocked", ["no-administrator", "disabled", "active-run"])
    def test_continuation_keeps_cache_without_new_run(
        self, db_session, make_user, make_model, make_file, blocked
    ):
        from app.modules.ingestion.extensions import MeshFingerprintPublished
        from app.modules.similarity import runs

        admin = make_user(superuser=True, active=blocked != "no-administrator")
        configuration.update_settings(
            db_session, admin, {"enabled": blocked != "disabled"}
        )
        file = make_file(make_model())
        existing = (
            runs.start(db_session, admin, scope="models", ids=[file.model_id])
            if blocked == "active-run"
            else None
        )
        state = ingestion.publish_mesh_fingerprint_continuation(
            db_session,
            file,
            extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
        )
        assert state == MeshFingerprintPublished(FingerprintResultState.READY)
        db_session.commit()
        db_session.expire_all()
        assert {row.state for row in db_session.exec(select(GeometryFingerprint))} == {
            "ready"
        }
        assert [run.id for run in db_session.exec(select(SimilarityRun))] == (
            [existing.id] if existing else []
        )

    def test_failed_continuation_never_creates_analysis(
        self, db_session, make_user, make_model, make_file
    ):
        from app.modules.ingestion.extensions import MeshFingerprintPublished

        admin = make_user(superuser=True)
        configuration.update_settings(db_session, admin, {"enabled": True})
        state = ingestion.publish_mesh_fingerprint_continuation(
            db_session,
            make_file(make_model()),
            FingerprintResult(
                FingerprintResultState.FAILED,
                failure_code=FingerprintFailureCode.INVALID_GEOMETRY,
            ),
        )
        assert state == MeshFingerprintPublished(FingerprintResultState.FAILED)
        db_session.commit()
        assert db_session.exec(select(SimilarityRun)).all() == []
        assert (
            db_session.exec(select(GeometryFingerprint)).one().failure_code
            == "invalid_geometry"
        )

    def test_leased_continuation_defers_analysis(
        self, db_session, make_user, make_model, make_file
    ):
        from app.core.time import ensure_utc
        from app.modules.ingestion.extensions import MeshFingerprintDeferred
        from app.modules.similarity.fingerprints import claim

        admin = make_user(superuser=True)
        configuration.update_settings(db_session, admin, {"enabled": True})
        file = make_file(make_model())
        fingerprint_id, token = claim(db_session, file)
        before = db_session.get(GeometryFingerprint, fingerprint_id)
        expiry = ensure_utc(before.lease_expires_at)
        state = ingestion.publish_mesh_fingerprint_continuation(
            db_session,
            file,
            extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
        )
        assert state == MeshFingerprintDeferred(expiry)
        db_session.commit()
        db_session.refresh(before)
        assert (before.state, before.lease_token) == ("pending", token)
        assert db_session.exec(select(SimilarityRun)).all() == []

    def test_trashed_source_continuation_is_rejected(
        self, db_session, make_user, make_model, make_file
    ):
        from app.modules.ingestion.extensions import MeshFingerprintRejected

        admin = make_user(superuser=True)
        configuration.update_settings(db_session, admin, {"enabled": True})
        file = make_file(make_model(), trashed=True)
        original = file.sha256
        assert (
            ingestion.publish_mesh_fingerprint_continuation(
                db_session,
                file,
                extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
            )
            == MeshFingerprintRejected()
        )
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        assert db_session.exec(select(SimilarityRun)).all() == []
        assert file.sha256 == original

    def test_continuation_run_shares_caller_write_rollback(
        self, db_session, make_user, make_model, make_file
    ):
        from app.modules.ingestion.extensions import MeshFingerprintPublished

        admin = make_user(superuser=True)
        configuration.update_settings(db_session, admin, {"enabled": True})
        model = make_model()
        file = make_file(model)
        original = model.name
        model.name = "caller write before continuation"
        db_session.add(model)
        db_session.flush()
        state = ingestion.publish_mesh_fingerprint_continuation(
            db_session,
            file,
            extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
        )
        assert state == MeshFingerprintPublished(FingerprintResultState.READY)
        run = db_session.exec(select(SimilarityRun)).one()
        assert (run.actor_id, run.trigger, run.scope_ids_json) == (
            admin.id,
            "ingest",
            f"[{model.id}]",
        )
        assert {row.state for row in db_session.exec(select(GeometryFingerprint))} == {
            "ready"
        }
        db_session.rollback()
        db_session.expire_all()
        assert model.name == original
        assert db_session.exec(select(SimilarityRun)).all() == []
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    def test_current_execution_can_create_analysis_intent(
        self, db_session, make_user, make_model, make_file, make_job
    ):
        from app.db.models import JobState
        from app.modules.work.contracts import JobExecution

        admin = make_user(superuser=True)
        configuration.update_settings(db_session, admin, {"enabled": True})
        file = make_file(make_model())
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=f"file/{file.id}",
            state=JobState.RUNNING,
            attempts=1,
        )
        execution = JobExecution(job.id, 1, job.execution_epoch)
        assert (
            ingestion.after_commit(
                get_session_factory(),
                file.id,
                admin.id,
                extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
                source_sha256=file.sha256,
                execution=execution,
            )
            == "ready"
        )
        run = db_session.exec(select(SimilarityRun)).one()
        assert (run.actor_id, run.trigger, run.scope_ids_json) == (
            admin.id,
            "ingest",
            f"[{file.model_id}]",
        )
        db_session.refresh(job)
        assert (job.state, job.attempts, job.execution_epoch) == (
            JobState.RUNNING,
            execution.attempt,
            execution.execution_epoch,
        )

    def test_continuation_propagates_invalid_configuration(
        self, db_session, make_user, make_model, make_file, make_system_config
    ):
        from app.core.errors import OperationError

        make_user(superuser=True)
        make_system_config(similarity_settings_json="not-json")
        model = make_model()
        file = make_file(model)
        original = model.name
        model.name = "active caller write"
        db_session.add(model)
        db_session.flush()
        with pytest.raises(OperationError, match="similarity_configuration_invalid"):
            ingestion.publish_mesh_fingerprint_continuation(
                db_session,
                file,
                extract(prepare_loaded_mesh(tetrahedron(), file_type="stl")),
            )
        db_session.rollback()
        db_session.expire_all()
        assert model.name == original
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        assert db_session.exec(select(SimilarityRun)).all() == []
