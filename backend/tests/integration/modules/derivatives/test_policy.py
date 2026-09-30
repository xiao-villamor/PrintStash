"""Durable derivative controls govern discovery, attempts and read projections."""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    Job,
    JobKind,
    JobState,
)
from app.modules.derivatives import policy, producers, records, repair
from app.modules.derivatives.kinds import GROUPS
from app.modules.derivatives.source import DerivativeSource, subject_key
from app.modules.work import service
from app.modules.work.jobs import jobs
from app.modules.work.reconciler import run_pass
from app.modules.work.submission import submit
from app.schemas.jobs import RegenerateMode


@pytest.fixture(params=GROUPS, ids=lambda item: item.definition.value)
def producer_group(request):
    return request.param


@pytest.fixture
def artifact(producer_group, make_model, make_file):
    filename = {
        JobKind.DERIVATIVES_MESH: "part.stl",
        JobKind.DERIVATIVES_GCODE: "part.gcode",
        JobKind.DERIVATIVES_TOOLPATH: "part.bgcode",
    }[producer_group.definition]
    return make_file(make_model(), filename=filename)


class TestResolution:
    def test_honors_the_deployment_default(
        self, db_session, producer_group, monkeypatch
    ):
        name = policy.SETTINGS[producer_group.definition]
        monkeypatch.setattr(settings.frozen, name, False)
        _overlay[name] = True
        control = policy.resolve(db_session)[producer_group.definition]
        assert (control.enabled, control.default_enabled, control.overridden) == (
            False,
            False,
            False,
        )

    def test_a_persisted_override_wins(
        self, db_session, producer_group, make_system_config
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        control = policy.resolve(db_session)[producer_group.definition]
        assert (control.enabled, control.default_enabled, control.overridden) == (
            False,
            True,
            True,
        )

    def test_independent_sessions_read_the_saved_policy(
        self, db_session, producer_group
    ):
        policy.update(db_session, {policy.SETTINGS[producer_group.definition]: False})
        with Session(db_session.get_bind()) as restarted:
            assert policy.resolve(restarted)[producer_group.definition].enabled is False


class TestDiscovery:
    def test_disabled_sources_offer_neither_subjects_nor_retry_deadlines(
        self, db_session, producer_group, artifact, make_system_config, make_derivative
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        kind = next(iter(producer_group.kinds))
        make_derivative(
            artifact,
            kind,
            state=DerivativeState.FAILED,
            next_attempt_at=utcnow() + timedelta(minutes=1),
        )
        source = DerivativeSource(producer_group)
        assert source.pending(db_session, now=utcnow(), limit=10) == []
        assert source.next_due(db_session, now=utcnow()) is None

    def test_disabled_discovery_creates_no_job(
        self, db_session, producer_group, artifact, make_system_config
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        run_pass(producer_group.definition)
        assert (
            db_session.exec(
                select(Job).where(Job.kind == producer_group.definition)
            ).all()
            == []
        )

    def test_disabled_recipe_changes_create_no_work(
        self, db_session, producer_group, artifact, make_system_config, make_derivative
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        for kind in producer_group.kinds:
            make_derivative(artifact, kind, recipe_version=0)
        run_pass(producer_group.definition)
        assert (
            db_session.exec(
                select(Job).where(Job.kind == producer_group.definition)
            ).all()
            == []
        )

    def test_reenable_offers_missing_outputs(
        self, db_session, producer_group, artifact
    ):
        name = policy.SETTINGS[producer_group.definition]
        policy.update(db_session, {name: False})
        policy.update(db_session, {name: True})
        assert [
            item.subject_key
            for item in DerivativeSource(producer_group).pending(
                db_session, now=utcnow(), limit=10
            )
        ] == [subject_key(artifact.id)]

    @pytest.mark.parametrize(
        "state", [DerivativeState.CANCELLED, DerivativeState.FAILED]
    )
    def test_toggle_preserves_terminal_retry_decisions(
        self, db_session, producer_group, artifact, state, make_derivative
    ):
        for kind in producer_group.kinds:
            make_derivative(
                artifact, kind, state=state, exhausted=state is DerivativeState.FAILED
            )
        name = policy.SETTINGS[producer_group.definition]
        policy.update(db_session, {name: False})
        policy.update(db_session, {name: True})
        assert (
            DerivativeSource(producer_group).pending(db_session, now=utcnow(), limit=10)
            == []
        )

    def test_resume_excludes_trashed_artifacts(
        self, db_session, producer_group, make_model, make_file
    ):
        filename = (
            "part.stl"
            if producer_group.definition is JobKind.DERIVATIVES_MESH
            else "part.bgcode"
        )
        make_file(make_model(), filename=filename, trashed=True)
        name = policy.SETTINGS[producer_group.definition]
        policy.update(db_session, {name: False})
        policy.update(db_session, {name: True})
        assert (
            DerivativeSource(producer_group).pending(db_session, now=utcnow(), limit=10)
            == []
        )


class TestAdmission:
    def test_refuses_a_producer_before_mutating_rows(
        self, db_session, producer_group, artifact, make_system_config
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        producer = {
            JobKind.DERIVATIVES_MESH: producers.derive_mesh,
            JobKind.DERIVATIVES_GCODE: producers.derive_gcode,
            JobKind.DERIVATIVES_TOOLPATH: producers.derive_toolpath,
        }[producer_group.definition]
        with pytest.raises(OperationError, match="derivative_group_disabled"):
            producer(artifact.id)
        assert db_session.exec(select(ArtifactDerivative)).all() == []

    @pytest.mark.parametrize(
        "state", [JobState.QUEUED, JobState.INTERRUPTED, JobState.RUNNING]
    )
    def test_disabled_work_is_settled_without_withdrawing_derivatives(
        self, db_session, producer_group, artifact, make_job, make_system_config, state
    ):
        job = make_job(
            kind=producer_group.definition,
            subject=subject_key(artifact.id),
            state=state,
        )
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        run_pass(producer_group.definition)
        assert jobs.get(job.id).state is JobState.CANCELLED
        assert jobs.get(job.id).error == "derivative_group_disabled"
        assert db_session.exec(select(ArtifactDerivative)).all() == []

    def test_queued_attempt_cannot_execute_after_disablement(
        self, db_session, producer_group, artifact, make_job, work_engine
    ):
        job = make_job(kind=producer_group.definition, subject=subject_key(artifact.id))
        submit(job.id)
        policy.update(db_session, {policy.SETTINGS[producer_group.definition]: False})
        work_engine.drain()
        assert jobs.get(job.id).state is JobState.CANCELLED
        assert db_session.exec(select(ArtifactDerivative)).all() == []

    def test_orphaned_rows_preserve_attempts_with_backoff(
        self,
        db_session,
        producer_group,
        artifact,
        make_job,
        make_derivative,
        make_system_config,
    ):
        kind = next(iter(producer_group.kinds))
        row = make_derivative(artifact, kind, state=DerivativeState.RUNNING, attempts=2)
        make_job(
            kind=producer_group.definition,
            subject=subject_key(artifact.id),
            state=JobState.INTERRUPTED,
            attempts=1,
        )
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        run_pass(producer_group.definition)
        db_session.refresh(row)
        assert (row.state, row.attempts, row.failure_reason) == (
            DerivativeState.FAILED,
            2,
            "derivative_group_disabled",
        )
        assert row.next_attempt_at is not None

    def test_drains_a_disabled_backlog_in_bounded_passes(
        self, db_session, make_job, make_system_config, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "jobs_reconcile_batch", 2)
        make_system_config(derivatives_mesh_enabled=False)
        backlog = [
            make_job(kind=JobKind.DERIVATIVES_MESH, subject=f"file/{index}")
            for index in range(7)
        ]
        run_pass(JobKind.DERIVATIVES_MESH)
        assert all(jobs.get(job.id).state is JobState.CANCELLED for job in backlog)

    def test_policy_cancellations_do_not_trigger_submission_cooldown(
        self, db_session, artifact, producer_group, make_job
    ):
        for _ in range(settings.jobs_resubmit_burst + 1):
            make_job(
                kind=producer_group.definition,
                subject=subject_key(artifact.id),
                state=JobState.CANCELLED,
                status_json='{"error": "derivative_group_disabled"}',
            )
        run_pass(producer_group.definition)
        assert any(
            row.state is JobState.QUEUED
            for row in db_session.exec(
                select(Job).where(Job.kind == producer_group.definition)
            ).all()
        )


class TestReads:
    def test_idle_disabled_outputs_have_no_retry(
        self, db_session, producer_group, artifact, make_system_config, make_derivative
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        kind = next(iter(producer_group.kinds))
        make_derivative(artifact, kind, state=DerivativeState.FAILED)
        projected = {item.kind: item for item in records.read(db_session, artifact)}
        assert projected[kind].state.value == "disabled"
        assert projected[kind].retryable is False

    @pytest.mark.parametrize(
        "state",
        [DerivativeState.READY, DerivativeState.SKIPPED, DerivativeState.RUNNING],
    )
    def test_preserves_satisfied_states(
        self,
        db_session,
        producer_group,
        artifact,
        make_system_config,
        make_derivative,
        state,
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        kind = next(iter(producer_group.kinds))
        make_derivative(artifact, kind, state=state)
        projected = {item.kind: item for item in records.read(db_session, artifact)}
        assert projected[kind].state.value == state.value


class TestManualActions:
    def test_job_retry_refuses_before_mutating_intent(
        self,
        db_session,
        producer_group,
        artifact,
        make_system_config,
        make_job,
        make_user,
        make_derivative,
    ):
        owner = make_user()
        kind = next(iter(producer_group.kinds))
        row = make_derivative(
            artifact, kind, state=DerivativeState.FAILED, exhausted=True
        )
        job = make_job(
            kind=producer_group.definition,
            subject=subject_key(artifact.id),
            owner=owner,
            state=JobState.FAILED,
        )
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        with pytest.raises(OperationError, match="derivative_group_disabled"):
            service.retry(job.id, actor=owner)
        assert jobs.get(job.id).state is JobState.FAILED
        db_session.refresh(row)
        assert row.attempts == settings.derivative_max_attempts

    def test_regeneration_marks_only_enabled_producers(
        self, db_session, make_user, make_system_config
    ):
        from app.db.models import DerivativeGroupRegeneration

        make_system_config(derivatives_mesh_enabled=False)
        service.regenerate_derivatives(
            DerivativeKind.THUMBNAIL,
            mode=RegenerateMode.ALL,
            actor=make_user(superuser=True),
        )
        assert [
            row.definition
            for row in db_session.exec(select(DerivativeGroupRegeneration)).all()
        ] == [JobKind.DERIVATIVES_GCODE]

    def test_regeneration_with_no_enabled_producer_changes_nothing(
        self, db_session, make_user, make_system_config
    ):
        from app.db.models import DerivativeGroupRegeneration

        make_system_config(
            derivatives_mesh_enabled=False, derivatives_gcode_enabled=False
        )
        with pytest.raises(OperationError, match="derivative_group_disabled"):
            service.regenerate_derivatives(
                DerivativeKind.THUMBNAIL,
                mode=RegenerateMode.ALL,
                actor=make_user(superuser=True),
            )
        assert db_session.exec(select(DerivativeGroupRegeneration)).all() == []

    def test_explicit_repair_keeps_disabled_outputs(
        self, db_session, producer_group, artifact, make_system_config, make_derivative
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        kind = next(iter(producer_group.kinds))
        row = make_derivative(artifact, kind)
        with pytest.raises(OperationError, match="derivative_group_disabled"):
            repair.request(db_session, artifact, [kind])
        assert db_session.get(ArtifactDerivative, row.id) is row

    def test_automatic_repair_leaves_disabled_outputs_unrepaired(
        self, db_session, producer_group, artifact, make_system_config, make_derivative
    ):
        make_system_config(**{policy.SETTINGS[producer_group.definition]: False})
        kind = next(iter(producer_group.kinds))
        row = make_derivative(artifact, kind)
        assert repair.now(artifact.id, [kind]) == {}
        db_session.refresh(row)
        assert row.state is DerivativeState.READY


class TestRecovery:
    def test_engine_cancel_failure_remains_recoverable(
        self, db_session, make_model, make_file, make_job, work_engine, monkeypatch
    ):
        artifact = make_file(make_model(), filename="queued.stl")
        job = make_job(kind=JobKind.DERIVATIVES_MESH, subject=subject_key(artifact.id))
        submit(job.id)
        policy.update(db_session, {policy.SettingName.MESH: False})
        cancel = work_engine.cancel
        available = False

        def fail_once(execution):
            if not available:
                raise ConnectionError("engine temporarily unavailable")
            return cancel(execution)

        monkeypatch.setattr(work_engine, "cancel", fail_once)
        result = run_pass(JobKind.DERIVATIVES_MESH)
        assert result.deferred >= 1
        assert jobs.get(job.id).state is JobState.QUEUED
        available = True
        run_pass(JobKind.DERIVATIVES_MESH)
        assert jobs.get(job.id).state is JobState.CANCELLED
        assert db_session.exec(select(ArtifactDerivative)).all() == []

    def test_overview_omits_disabled_actionable_failures(
        self,
        db_session,
        make_system_config,
        make_model,
        make_file,
        make_derivative,
        make_job,
    ):
        make_system_config(derivatives_mesh_enabled=False)
        artifact = make_file(make_model(), filename="failed.stl")
        make_derivative(
            artifact, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED
        )
        history = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            state=JobState.FAILED,
            status_json='{"error":"render_failed","retryable":true}',
        )
        result = service.overview()
        assert result.failed_derivatives == 0
        assert result.failed_jobs == []
        assert jobs.get(history.id).error == "render_failed"


class TestRepairBacklog:
    def test_healthy_attempts_cannot_hide_a_disabled_lost_backlog(
        self, db_session, make_job, make_system_config, work_engine, monkeypatch
    ):
        from app.modules.work.contracts import EngineStatus
        from app.modules.work.submission import execution_id

        monkeypatch.setitem(_overlay, "jobs_reconcile_batch", 1)
        healthy = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject="file/100", state=JobState.RUNNING
        )
        submit(healthy.id)
        work_engine.executions[
            execution_id(healthy.id, 1)
        ].status = EngineStatus.RUNNING
        lost = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject="file/101",
            state=JobState.RUNNING,
            attempts=1,
        )
        make_system_config(derivatives_mesh_enabled=False)
        run_pass(JobKind.DERIVATIVES_MESH)
        run_pass(JobKind.DERIVATIVES_MESH)
        assert jobs.get(healthy.id).state is JobState.RUNNING
        assert jobs.get(lost.id).state is JobState.CANCELLED
