"""Pending optional analysis keeps durable authority after basic outputs commit."""

import pytest
from sqlmodel import select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.models import DerivativeKind, DerivativeState, GeometryFingerprint, JobKind
from app.modules.derivatives import records
from app.modules.media.fingerprints import (
    ALGORITHM_VERSION,
    FingerprintResult,
    FingerprintResultState,
)
from app.modules.media.mesh_facts import FingerprintFailureCode


@pytest.fixture(autouse=True)
def continuation_provider(monkeypatch):
    from app.modules.ingestion import extensions
    from app.modules.similarity import ingestion

    monkeypatch.setattr(extensions, "_derivatives", ingestion)


@pytest.fixture
def pending_mesh(db_session, make_file, make_model, make_derivative):
    from app.modules.derivatives import mesh_continuations as owner
    from app.modules.derivatives.mesh_continuation_values import FingerprintPlan

    file = make_file(make_model(), filename="part.stl")
    row = make_derivative(file, DerivativeKind.METADATA, state=DerivativeState.RUNNING)
    attempt = records.attempt(db_session, file, row)
    plan = FingerprintPlan(ALGORITHM_VERSION, 100)
    pending = owner.create(db_session, attempt, plan)
    records.mark_ready(db_session, attempt, now=utcnow())
    db_session.commit()
    return owner, file, plan, pending


@pytest.fixture
def failed_fingerprint():
    return FingerprintResult(
        FingerprintResultState.FAILED,
        failure_code=FingerprintFailureCode.INVALID_GEOMETRY,
    )


class TestMeshContinuation:
    def test_records_intent_atomically_with_metadata_ready(
        self,
        db_session,
        make_file,
        make_model,
        make_derivative,
    ):
        from app.db.models import MeshFingerprintContinuation
        from app.modules.derivatives import mesh_continuations as owner
        from app.modules.derivatives.mesh_continuation_values import FingerprintPlan

        file = make_file(make_model(), filename="part.stl")
        row = make_derivative(
            file, DerivativeKind.METADATA, state=DerivativeState.RUNNING
        )
        attempt = records.attempt(db_session, file, row)
        owner.create(db_session, attempt, FingerprintPlan(ALGORITHM_VERSION, 100))
        records.mark_ready(db_session, attempt, now=utcnow())
        db_session.rollback()

        db_session.refresh(row)
        assert row.state is DerivativeState.RUNNING
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    def test_retokened_recovery_rejects_old_completion(
        self,
        db_session,
        pending_mesh,
        failed_fingerprint,
    ):
        from app.db.models import MeshFingerprintContinuation

        owner, file, plan, old = pending_mesh
        resumed = owner.resume(db_session, file, plan, execution=None)
        db_session.commit()
        assert resumed is not None and resumed.context.token != old.context.token
        with pytest.raises(records.AttemptSuperseded):
            owner.complete(db_session, old, failed_fingerprint)
        db_session.rollback()
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        row = db_session.exec(select(MeshFingerprintContinuation)).one()
        assert row.token == resumed.context.token
        owner.complete(db_session, resumed, failed_fingerprint)
        db_session.commit()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert db_session.exec(select(GeometryFingerprint)).one().state == "failed"

    def test_completion_is_atomic(
        self,
        db_session,
        pending_mesh,
        failed_fingerprint,
    ):
        from app.db.models import MeshFingerprintContinuation

        owner, _file, _plan, pending = pending_mesh
        owner.complete(db_session, pending, failed_fingerprint)
        db_session.rollback()
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        assert (
            db_session.exec(select(MeshFingerprintContinuation)).one().token
            == pending.context.token
        )
        owner.complete(db_session, pending, failed_fingerprint)
        db_session.commit()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert (
            db_session.exec(select(GeometryFingerprint)).one().failure_code
            == "invalid_geometry"
        )

    def test_old_withdrawal_cannot_remove_resumed_intent(
        self, db_session, pending_mesh
    ):
        from app.db.models import MeshFingerprintContinuation

        owner, file, plan, old = pending_mesh
        resumed = owner.resume(db_session, file, plan, execution=None)
        assert resumed is not None
        owner.withdraw(db_session, old)
        db_session.commit()
        assert (
            db_session.exec(select(MeshFingerprintContinuation)).one().token
            == resumed.context.token
        )

    @pytest.mark.parametrize("changed", ["source", "plan", "disabled", "regeneration"])
    def test_retires_obsolete_input_without_withdrawing_ready_metadata(
        self,
        db_session,
        pending_mesh,
        make_derivative_group_regeneration,
        changed,
    ):
        from app.db.models import ArtifactDerivative, MeshFingerprintContinuation
        from app.modules.derivatives.mesh_continuation_values import FingerprintPlan

        owner, file, plan, _pending = pending_mesh
        if changed == "source":
            file.sha256 = "c" * 64
            db_session.add(file)
            db_session.commit()
        elif changed == "plan":
            plan = FingerprintPlan(ALGORITHM_VERSION, 200)
        elif changed == "disabled":
            plan = None
        else:
            make_derivative_group_regeneration(
                JobKind.DERIVATIVES_MESH, DerivativeKind.METADATA
            )
        assert owner.resume(db_session, file, plan, execution=None) is None
        db_session.commit()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert (
            db_session.exec(select(ArtifactDerivative)).one().state
            is DerivativeState.READY
        )

    def test_late_failure_preserves_existing_ready_cache(
        self,
        db_session,
        pending_mesh,
        failed_fingerprint,
        make_geometry_fingerprint,
    ):
        from app.db.models import MeshFingerprintContinuation

        owner, file, _plan, pending = pending_mesh
        prior = make_geometry_fingerprint(
            file, algorithm_version=ALGORITHM_VERSION, state="ready"
        )
        before = prior.model_dump()
        owner.complete(db_session, pending, failed_fingerprint)
        db_session.commit()
        db_session.refresh(prior)
        assert prior.model_dump() == before
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []

    @pytest.mark.parametrize("retired", ["epoch", "attempt", "cancelled"])
    def test_rejects_completion_from_retired_job_authority(
        self,
        db_session,
        make_file,
        make_model,
        make_derivative,
        make_job,
        failed_fingerprint,
        retired,
    ):
        from app.db.models import JobState, MeshFingerprintContinuation
        from app.modules.derivatives import mesh_continuations as owner
        from app.modules.derivatives.mesh_continuation_values import FingerprintPlan
        from app.modules.work.contracts import JobExecution

        file = make_file(make_model(), filename="part.stl")
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            state=JobState.RUNNING,
            subject=f"file/{file.id}",
            attempts=1,
        )
        execution = JobExecution(job.id, job.attempts, job.execution_epoch)
        row = make_derivative(
            file, DerivativeKind.METADATA, state=DerivativeState.RUNNING
        )
        attempt = records.attempt(db_session, file, row, execution=execution)
        pending = owner.create(
            db_session, attempt, FingerprintPlan(ALGORITHM_VERSION, 100)
        )
        records.mark_ready(db_session, attempt, now=utcnow())
        db_session.commit()
        if retired == "epoch":
            job.execution_epoch = "new-execution-epoch"
        elif retired == "attempt":
            job.attempts += 1
        else:
            job.state = JobState.CANCELLED
            job.finished_at = utcnow()
        db_session.add(job)
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            owner.complete(db_session, pending, failed_fingerprint)
        db_session.rollback()
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        assert (
            db_session.exec(select(MeshFingerprintContinuation)).one().token
            == pending.context.token
        )

    @pytest.mark.parametrize("changed", ["source", "regeneration"])
    def test_stale_context_cannot_publish_cache(
        self,
        db_session,
        pending_mesh,
        failed_fingerprint,
        make_derivative_group_regeneration,
        changed,
    ):
        from app.db.models import MeshFingerprintContinuation

        owner, file, _plan, pending = pending_mesh
        if changed == "source":
            file.sha256 = "d" * 64
            db_session.add(file)
            db_session.commit()
        else:
            make_derivative_group_regeneration(
                JobKind.DERIVATIVES_MESH, DerivativeKind.METADATA
            )
        with pytest.raises(records.AttemptSuperseded):
            owner.complete(db_session, pending, failed_fingerprint)
        db_session.rollback()
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        assert (
            db_session.exec(select(MeshFingerprintContinuation)).one().token
            == pending.context.token
        )

    def test_defers_native_failure_until_existing_backoff_is_due(
        self,
        db_session,
        pending_mesh,
    ):
        from datetime import timedelta

        from app.db.models import MeshFingerprintContinuation

        owner, file, plan, pending = pending_mesh
        before = utcnow()
        owner.defer(db_session, pending, FingerprintFailureCode.TIMEOUT)
        db_session.commit()
        row = db_session.exec(select(MeshFingerprintContinuation)).one()
        after = utcnow()
        delay = timedelta(seconds=settings.derivative_backoff_seconds)
        assert before + delay <= ensure_utc(row.available_at) <= after + delay
        assert row.attempts == 1
        assert owner.resume(db_session, file, plan, execution=None) is None
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    def test_lost_process_at_attempt_limit_retires_input(
        self,
        db_session,
        make_file,
        make_model,
        make_derivative,
        make_mesh_continuation,
    ):
        from app.db.models import MeshFingerprintContinuation
        from app.modules.derivatives import mesh_continuations as owner
        from app.modules.derivatives.mesh_continuation_values import FingerprintPlan

        file = make_file(make_model(), filename="part.stl")
        ready = make_derivative(file, DerivativeKind.METADATA)
        make_mesh_continuation(
            file, attempts=settings.derivative_max_attempts, triangle_cap=100
        )
        pending = owner.resume(
            db_session, file, FingerprintPlan(ALGORITHM_VERSION, 100), execution=None
        )
        # Lost completion at the limit retires before another native launch.
        assert pending is None
        db_session.commit()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert (
            db_session.exec(select(GeometryFingerprint)).one().failure_code
            == "worker_failed"
        )
        db_session.refresh(ready)
        assert ready.state is DerivativeState.READY

    def test_last_native_failure_uses_actual_closed_failure_cause(
        self,
        db_session,
        pending_mesh,
        monkeypatch,
    ):
        from app.core.config import _overlay
        from app.db.models import MeshFingerprintContinuation

        owner, _file, _plan, pending = pending_mesh
        monkeypatch.setitem(_overlay, "derivative_max_attempts", 1)
        owner.defer(db_session, pending, FingerprintFailureCode.RESOURCE_LIMIT)
        db_session.commit()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert (
            db_session.exec(select(GeometryFingerprint)).one().failure_code
            == "resource_limit"
        )

    def test_successful_publication_is_atomic(
        self,
        db_session,
        pending_mesh,
        make_user,
    ):
        from app.db.models import MeshFingerprintContinuation, SimilarityRun
        from app.modules.media.fingerprints import extract
        from app.modules.media.mesh_resources import prepare_loaded_mesh
        from app.modules.similarity import configuration
        from tests.factories.geometry import tetrahedron

        owner, file, _plan, pending = pending_mesh
        admin = make_user(superuser=True)
        configuration.update_settings(db_session, admin, {"enabled": True})
        result = extract(prepare_loaded_mesh(tetrahedron(), file_type="stl"))
        assert result.state is FingerprintResultState.READY
        owner.complete(db_session, pending, result)
        db_session.rollback()
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        assert db_session.exec(select(SimilarityRun)).all() == []
        assert (
            db_session.exec(select(MeshFingerprintContinuation)).one().token
            == pending.context.token
        )
        owner.complete(db_session, pending, result)
        db_session.commit()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert all(
            row.state == "ready" for row in db_session.exec(select(GeometryFingerprint))
        )
        run = db_session.exec(select(SimilarityRun)).one()
        assert (run.actor_id, run.trigger, run.scope_ids_json) == (
            admin.id,
            "ingest",
            f"[{file.model_id}]",
        )

    @pytest.mark.parametrize("operation", ["create", "resume"])
    def test_rejects_source_outside_mesh_group(
        self,
        db_session,
        make_file,
        make_model,
        make_derivative,
        operation,
    ):
        from app.db.models import MeshFingerprintContinuation
        from app.modules.derivatives import mesh_continuations as owner
        from app.modules.derivatives.mesh_continuation_values import FingerprintPlan

        file = make_file(make_model(), filename="part.gcode")
        row = make_derivative(
            file, DerivativeKind.METADATA, state=DerivativeState.RUNNING
        )
        plan = FingerprintPlan(ALGORITHM_VERSION, 100)
        with pytest.raises(ValueError, match="continuation_requires_mesh_source"):
            if operation == "create":
                owner.create(db_session, records.attempt(db_session, file, row), plan)
            else:
                owner.resume(db_session, file, plan, execution=None)
        db_session.rollback()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert db_session.exec(select(GeometryFingerprint)).all() == []


class TestMeshContinuationRecovery:
    def test_rejects_nonmetadata_intent(
        self, db_session, make_file, make_model, make_derivative
    ):
        from app.db.models import MeshFingerprintContinuation
        from app.modules.derivatives import mesh_continuations as owner
        from app.modules.derivatives.mesh_continuation_values import FingerprintPlan

        file = make_file(make_model(), filename="part.stl")
        row = make_derivative(
            file, DerivativeKind.THUMBNAIL, state=DerivativeState.RUNNING
        )
        attempt = records.attempt(db_session, file, row)
        with pytest.raises(
            ValueError, match="fingerprint_continuation_requires_metadata"
        ):
            owner.create(db_session, attempt, FingerprintPlan(ALGORITHM_VERSION, 100))
        db_session.rollback()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        db_session.refresh(row)
        assert row.state is DerivativeState.RUNNING

    def test_absent_intent_preserves_ready_metadata(self, db_session, pending_mesh):
        from app.db.models import ArtifactDerivative, MeshFingerprintContinuation

        owner, file, plan, pending = pending_mesh
        owner.withdraw(db_session, pending)
        db_session.commit()
        before = db_session.exec(select(ArtifactDerivative)).one().model_dump()
        assert owner.resume(db_session, file, plan, execution=None) is None
        db_session.commit()
        assert db_session.exec(select(MeshFingerprintContinuation)).all() == []
        assert db_session.exec(select(ArtifactDerivative)).one().model_dump() == before
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    def test_replaces_existing_intent_for_current_metadata_attempt(
        self, db_session, make_file, make_model, make_derivative, make_mesh_continuation
    ):
        from app.db.models import MeshFingerprintContinuation
        from app.modules.derivatives import mesh_continuations as owner
        from app.modules.derivatives.mesh_continuation_values import FingerprintPlan

        file = make_file(make_model(), filename="part.stl")
        old = make_mesh_continuation(file, attempts=3, triangle_cap=200)
        old_token = old.token
        row = make_derivative(
            file, DerivativeKind.METADATA, state=DerivativeState.RUNNING
        )
        attempt = records.attempt(db_session, file, row)
        plan = FingerprintPlan(ALGORITHM_VERSION, 100)
        pending = owner.create(db_session, attempt, plan)
        records.mark_ready(db_session, attempt, now=utcnow())
        db_session.commit()
        stored = db_session.exec(select(MeshFingerprintContinuation)).one()
        assert stored.token == pending.context.token != old_token
        assert pending.attempts == stored.attempts == 1
        assert pending.plan == plan
        assert stored.triangle_cap == 100
        db_session.refresh(row)
        assert row.state is DerivativeState.READY
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    def test_changed_result_algorithm_preserves_intent(self, db_session, pending_mesh):
        from app.db.models import MeshFingerprintContinuation

        owner, _file, _plan, pending = pending_mesh
        result = FingerprintResult(
            FingerprintResultState.FAILED,
            failure_code=FingerprintFailureCode.INVALID_GEOMETRY,
            algorithm_version="different-algorithm",
        )
        before = db_session.exec(select(MeshFingerprintContinuation)).one().model_dump()
        with pytest.raises(
            ValueError, match="continuation_fingerprint_algorithm_changed"
        ):
            owner.complete(db_session, pending, result)
        db_session.rollback()
        assert (
            db_session.exec(select(MeshFingerprintContinuation)).one().model_dump()
            == before
        )
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    @pytest.mark.parametrize(
        "changed",
        ["cap", "algorithm", "attempts"],
        ids=["cap", "algorithm", "attempts"],
    )
    def test_mismatched_captured_authority_cannot_publish(
        self, db_session, pending_mesh, failed_fingerprint, changed
    ):
        from dataclasses import replace

        from app.db.models import MeshFingerprintContinuation

        owner, _file, _plan, pending = pending_mesh
        if changed == "cap":
            stale = replace(pending, plan=replace(pending.plan, triangle_cap=200))
        elif changed == "algorithm":
            stale = replace(
                pending,
                plan=replace(pending.plan, algorithm_version="different-algorithm"),
            )
        else:
            stale = replace(pending, attempts=pending.attempts + 1)
        with pytest.raises(records.AttemptSuperseded):
            owner.require_pending(db_session, stale)
        db_session.rollback()
        stored = db_session.exec(select(MeshFingerprintContinuation)).one()
        assert stored.token == pending.context.token
        assert stored.attempts == pending.attempts
        assert stored.triangle_cap == pending.plan.triangle_cap
        assert stored.algorithm_version == pending.plan.algorithm_version
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        assert owner.require_pending(db_session, pending).id == pending.context.file_id

    def test_active_cache_lease_defers_durable_continuation(
        self, db_session, pending_mesh, failed_fingerprint
    ):
        from app.db.models import (
            ArtifactDerivative,
            MeshFingerprintContinuation,
            SimilarityRun,
        )
        from app.modules.ingestion.extensions import MeshFingerprintDeferred
        from app.modules.similarity import fingerprints

        owner, file, plan, pending = pending_mesh
        claim = fingerprints.claim(db_session, file)
        assert claim is not None
        cache = db_session.exec(select(GeometryFingerprint)).one()
        before = cache.model_dump()
        state = owner.complete(db_session, pending, failed_fingerprint)
        assert isinstance(state, MeshFingerprintDeferred)
        db_session.commit()
        db_session.refresh(cache)
        assert cache.model_dump() == before
        stored = db_session.exec(select(MeshFingerprintContinuation)).one()
        assert stored.token == pending.context.token
        assert stored.attempts == pending.attempts
        assert ensure_utc(stored.available_at) == ensure_utc(cache.lease_expires_at)
        assert ensure_utc(state.available_at) == ensure_utc(cache.lease_expires_at)
        assert owner.resume(db_session, file, plan, execution=None) is None
        assert db_session.exec(select(SimilarityRun)).all() == []
        assert (
            db_session.exec(select(ArtifactDerivative)).one().state
            is DerivativeState.READY
        )

    @pytest.mark.parametrize(
        "authority",
        ["unfenced", "current", "epoch", "attempt", "cancelled", "other-file"],
        ids=["unfenced", "current", "epoch", "attempt", "cancelled", "other-file"],
    )
    def test_withdrawal_requires_current_stored_job(
        self,
        db_session,
        make_file,
        make_model,
        make_derivative,
        make_mesh_continuation,
        make_job,
        authority,
    ):
        from app.db.models import JobState, MeshFingerprintContinuation
        from app.modules.derivatives import mesh_continuations as owner

        file = make_file(make_model(), filename="part.stl")
        ready = make_derivative(file, DerivativeKind.METADATA)
        job = (
            None
            if authority == "unfenced"
            else make_job(
                kind=JobKind.DERIVATIVES_MESH,
                state=JobState.RUNNING,
                subject=f"file/{file.id}",
                attempts=1,
            )
        )
        pending = make_mesh_continuation(file, job=job, triangle_cap=100)
        token = pending.token
        if job is not None:
            if authority == "epoch":
                job.execution_epoch = "replacement-execution"
            elif authority == "attempt":
                job.attempts += 1
            elif authority == "cancelled":
                job.state = JobState.CANCELLED
                job.finished_at = utcnow()
            elif authority == "other-file":
                other = make_file(make_model(), filename="other.stl")
                job.subject_key = f"file/{other.id}"
            db_session.add(job)
            db_session.commit()
        owner.withdraw_current_job(db_session, file.id)
        db_session.commit()
        remaining = db_session.exec(select(MeshFingerprintContinuation)).all()
        if authority in {"unfenced", "current"}:
            assert remaining == []
        else:
            assert len(remaining) == 1
            assert remaining[0].token == token
        db_session.refresh(ready)
        assert ready.state is DerivativeState.READY
        assert db_session.exec(select(GeometryFingerprint)).all() == []
