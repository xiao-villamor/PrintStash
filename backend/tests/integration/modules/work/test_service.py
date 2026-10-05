"""Operations routes and domain modules call on background work.

A Job is visible to its owner and to administrators; to anyone else it does
not exist (404, never 403, so ids cannot be probed). Cancelling withdraws the
subject's intent first, so the reconciler cannot resurrect it, then stops
whatever the engine has in flight. Retrying returns a failed or cancelled
subject to pending and queues the same Job again, unless another Job already
owns the subject or the subject is gone.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select

import app.modules.work.service as service
from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    IngestRequest,
    IngestRequestKind,
    Job,
    JobKind,
    JobState,
    WorkPriority,
)
from app.modules.derivatives.source import subject_key
from app.modules.work.jobs import status_of
from app.modules.work.submission import execution_id
from app.runtime.engine.inline import EngineStatus


@pytest.fixture
def owner(make_user):
    return make_user()


@pytest.fixture
def admin(make_user):
    return make_user(superuser=True)


@pytest.fixture
def mesh(make_model, make_file):
    return make_file(make_model(), filename="part.stl")


@pytest.fixture
def nudged(monkeypatch) -> list[str]:
    names: list[str] = []
    monkeypatch.setattr(service, "nudge", lambda name, **_: names.append(name))
    return names


def _state(session: Session, job_id: str) -> JobState:
    session.expire_all()
    job = session.get(Job, job_id)
    assert job is not None
    return job.state


def _refused(call) -> OperationError:
    with pytest.raises(OperationError) as refused:
        call()
    return refused.value


class TestRequest:
    def test_records_a_queued_job_in_the_callers_transaction(
        self, db_session: Session, owner
    ) -> None:
        job_id = service.request(
            db_session,
            definition=JobKind.SOURCES_SCAN,
            subject_key="library/1",
            owner_user_id=owner.id,
            priority=WorkPriority.BACKFILL,
        )
        db_session.commit()

        job = db_session.get(Job, job_id)
        assert job is not None
        assert (job.state, job.owner_user_id, job.priority) == (
            JobState.QUEUED,
            owner.id,
            WorkPriority.BACKFILL,
        )

    def test_nothing_is_recorded_if_the_caller_rolls_back(
        self, db_session: Session
    ) -> None:
        # The Job and the intent it describes commit together or not at all.
        job_id = service.request(
            db_session,
            definition=JobKind.SOURCES_SCAN,
            subject_key="library/1",
            owner_user_id=None,
        )
        db_session.rollback()

        assert db_session.get(Job, job_id) is None


class TestVisibleTo:
    def test_the_owner_sees_their_job(self, make_job, owner) -> None:
        assert service.visible_to(status_of(make_job(owner=owner)), owner)

    def test_an_administrator_sees_every_job(self, make_job, owner, admin) -> None:
        assert service.visible_to(status_of(make_job(owner=owner)), admin)
        assert service.visible_to(status_of(make_job()), admin)

    def test_another_user_does_not(self, make_job, make_user, owner) -> None:
        assert not service.visible_to(status_of(make_job(owner=owner)), make_user())

    def test_a_system_job_is_for_administrators_only(self, make_job, owner) -> None:
        assert not service.visible_to(status_of(make_job()), owner)


class TestCancel:
    def test_withdraws_the_subjects_intent(
        self, db_session: Session, make_job, owner, mesh
    ) -> None:
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject=subject_key(mesh.id), owner=owner
        )

        status = service.cancel(job.id, actor=owner)

        assert status.state == JobState.CANCELLED.value
        states = {
            row.kind: row.state
            for row in db_session.exec(
                select(ArtifactDerivative).where(ArtifactDerivative.file_id == mesh.id)
            ).all()
        }
        assert states[DerivativeKind.THUMBNAIL] == DerivativeState.CANCELLED

    def test_stops_the_attempt_the_engine_is_running(
        self, work_engine, make_job, owner
    ) -> None:
        from app.modules.work.submission import submit

        job = make_job(kind=JobKind.SOURCES_SCAN, owner=owner)
        submit(job.id)

        service.cancel(job.id, actor=owner)

        execution = work_engine.executions[execution_id(job.id, 1, job.execution_epoch)]
        assert execution.status is EngineStatus.CANCELLED

    def test_a_job_never_submitted_needs_no_engine_cancel(
        self, db_session: Session, work_engine, make_job, owner, monkeypatch
    ) -> None:
        def unexpected(_execution_id):
            raise AssertionError("nothing was submitted")

        monkeypatch.setattr(work_engine, "cancel", unexpected)
        job = make_job(kind=JobKind.SOURCES_SCAN, owner=owner)

        service.cancel(job.id, actor=owner)

        assert _state(db_session, job.id) == JobState.CANCELLED

    def test_an_unreachable_engine_still_settles_the_job(
        self, db_session: Session, work_engine, make_job, owner, monkeypatch
    ) -> None:
        # The subject is already withdrawn; the reconciler interrupts whatever
        # the engine still runs once it is reachable again.
        from app.modules.work.submission import submit

        job = make_job(kind=JobKind.SOURCES_SCAN, owner=owner)
        submit(job.id)

        def unreachable(_execution_id):
            raise RuntimeError("engine down")

        monkeypatch.setattr(work_engine, "cancel", unreachable)

        assert service.cancel(job.id, actor=owner).state == JobState.CANCELLED.value

    def test_another_users_job_does_not_exist_for_them(
        self, make_job, make_user, owner
    ) -> None:
        job = make_job(owner=owner)

        error = _refused(lambda: service.cancel(job.id, actor=make_user()))

        assert (error.code, error.kind) == ("job_not_found", ErrorKind.NOT_FOUND)

    def test_a_missing_job_does_not_exist(self, admin) -> None:
        error = _refused(lambda: service.cancel("no-such-job", actor=admin))

        assert error.kind is ErrorKind.NOT_FOUND

    def test_a_settled_job_cannot_be_cancelled(self, make_job, owner) -> None:
        job = make_job(owner=owner, state=JobState.COMPLETED)

        error = _refused(lambda: service.cancel(job.id, actor=owner))

        assert (error.code, error.kind) == ("job_not_active", ErrorKind.CONFLICT)

    def test_failed_intent_withdrawal_rolls_back_cancellation(
        self, make_job, mesh, owner, db_session, work_catalog, monkeypatch
    ):
        from dataclasses import replace

        from app.modules.derivatives.jobs import definitions

        job = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject=subject_key(mesh.id), owner=owner
        )
        definition = next(d for d in definitions() if d.name is job.kind)

        def broken_withdrawal(session, subject):
            definition.cancel(session, subject)
            session.flush()
            raise RuntimeError("withdrawal_failed")

        monkeypatch.setitem(
            work_catalog.definitions,
            job.kind,
            replace(definition, cancel=broken_withdrawal),
        )

        with pytest.raises(RuntimeError, match="withdrawal_failed"):
            service.cancel(job.id, actor=owner)

        db_session.refresh(job)
        assert job.state is JobState.QUEUED
        assert (
            db_session.exec(
                select(ArtifactDerivative).where(ArtifactDerivative.file_id == mesh.id)
            ).all()
            == []
        )


class TestCancelQueued:
    def test_withdraws_every_queued_job_of_one_definition(
        self, db_session: Session, make_job, admin
    ) -> None:
        queued = [make_job(kind=JobKind.SOURCES_SCAN) for _ in range(2)]
        running = make_job(
            kind=JobKind.SOURCES_SCAN, state=JobState.RUNNING, attempts=1
        )
        other = make_job(kind=JobKind.BACKUPS_CREATE)

        assert service.cancel_queued(JobKind.SOURCES_SCAN, actor=admin) == 2

        assert {_state(db_session, job.id) for job in queued} == {JobState.CANCELLED}
        assert _state(db_session, running.id) == JobState.RUNNING
        assert _state(db_session, other.id) == JobState.QUEUED

    def test_is_for_administrators_only(self, make_job, owner) -> None:
        make_job(kind=JobKind.SOURCES_SCAN)

        error = _refused(
            lambda: service.cancel_queued(JobKind.SOURCES_SCAN, actor=owner)
        )

        assert (error.code, error.kind) == ("admin_required", ErrorKind.FORBIDDEN)

    def test_a_definition_this_process_lacks_is_refused(
        self, admin, work_engine
    ) -> None:
        from app.modules.work import catalog as catalog_module
        from app.modules.work.catalog import WorkCatalog

        catalog_module.bind(work_engine, WorkCatalog())

        with pytest.raises(LookupError):
            service.cancel_queued(JobKind.SEARCH_CAPTION, actor=admin)


class TestSupersedeRestored:
    def test_cancels_a_backup_the_restored_database_shows_running(
        self, db_session: Session, make_job
    ) -> None:
        # The archive captured its own backup Job mid-run.
        snapshot = make_job(
            kind=JobKind.BACKUPS_CREATE, state=JobState.RUNNING, attempts=1
        )

        assert service.supersede_restored() == 1

        db_session.expire_all()
        row = db_session.get(Job, snapshot.id)
        assert row is not None and row.state == JobState.CANCELLED
        assert status_of(row).error == "superseded_by_restore"

    def test_leaves_work_the_restore_still_owes(
        self, db_session: Session, make_job
    ) -> None:
        scan = make_job(kind=JobKind.SOURCES_SCAN, state=JobState.RUNNING, attempts=1)

        assert service.supersede_restored() == 0

        assert _state(db_session, scan.id) == JobState.RUNNING

    def test_leaves_a_finished_backup_alone(
        self, db_session: Session, make_job
    ) -> None:
        done = make_job(kind=JobKind.BACKUPS_CREATE, state=JobState.COMPLETED)

        assert service.supersede_restored() == 0

        assert _state(db_session, done.id) == JobState.COMPLETED


class TestRetry:
    def test_retries_completed_partial_work_with_a_new_execution_epoch(
        self, make_ingest_request, owner, db_session, nudged, work_engine
    ):
        from app.modules.work.contracts import JobOutcome
        from app.modules.work.submission import submit
        from tests.factories.ops import build_job_context

        selection = json.dumps(
            {
                "files": [
                    {"file_id": "first", "name": "first.gcode", "file_type": "gcode"}
                ]
            }
        )
        request = make_ingest_request(
            owner,
            kind=IngestRequestKind.URL_SELECTION,
            source_url="https://www.printables.com/model/1",
            selection_json=selection,
        )
        submit(request.job_id)
        context = build_job_context(request.job_id)
        context.finish(
            JobOutcome.COMPLETED,
            succeeded=1,
            failed=1,
            processed=2,
            total=2,
            progress=100,
            retryable=True,
            result={"imported": 1, "total": 2},
        )
        db_session.expire_all()
        row = db_session.get(Job, request.job_id)
        assert row is not None
        old_epoch, old_attempts = row.execution_epoch, row.attempts
        assert status_of(row).state is JobState.COMPLETED

        status = service.retry(row.id, actor=owner)

        db_session.refresh(row)
        assert status.job_id == request.job_id
        assert status.state is JobState.QUEUED
        assert row.execution_epoch != old_epoch
        assert (row.resubmits, row.finished_at, row.attempts) == (0, None, old_attempts)
        assert context.cancelled()
        assert nudged == [JobKind.INGESTION_URL_SELECTION]
        retained = db_session.get(IngestRequest, request.job_id)
        assert retained is not None and retained.selection_json == selection
        submit(row.id)
        db_session.refresh(row)
        assert row.attempts == old_attempts + 1
        assert row.submitted_epoch == row.execution_epoch
        assert (
            work_engine.executions[
                execution_id(row.id, row.attempts, row.execution_epoch)
            ].status
            is EngineStatus.QUEUED
        )

    @pytest.mark.parametrize(
        ("state", "retryable", "failed"),
        [
            (JobState.COMPLETED, True, 0),
            (JobState.COMPLETED, False, 1),
            (JobState.QUEUED, True, 1),
            (JobState.RUNNING, True, 1),
            (JobState.INTERRUPTED, True, 1),
        ],
        ids=[
            "completed-success",
            "completed-not-retryable",
            "queued",
            "running",
            "interrupted",
        ],
    )
    def test_refuses_completed_or_active_work_without_retry_eligibility(
        self, make_ingest_request, owner, db_session, nudged, state, retryable, failed
    ):
        request = make_ingest_request(owner, state=state)
        row = db_session.get(Job, request.job_id)
        assert row is not None
        row.status_json = json.dumps(
            {"retryable": retryable, "failed": failed, "succeeded": 1}
        )
        db_session.add(row)
        db_session.commit()
        epoch = row.execution_epoch

        refused = _refused(lambda: service.retry(row.id, actor=owner))

        assert (refused.code, refused.kind) == ("job_not_retryable", ErrorKind.CONFLICT)
        db_session.refresh(row)
        assert row.state is state and row.execution_epoch == epoch
        assert nudged == []

    def test_queues_the_same_job_again(
        self, db_session: Session, make_job, owner, mesh, make_derivative, nudged
    ) -> None:
        make_derivative(
            mesh, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED, exhausted=True
        )
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(mesh.id),
            owner=owner,
            state=JobState.FAILED,
            resubmits=3,
        )

        status = service.retry(job.id, actor=owner)

        assert status.state == JobState.QUEUED.value
        db_session.expire_all()
        row = db_session.get(Job, job.id)
        assert row is not None
        assert (row.resubmits, row.finished_at) == (0, None)
        assert nudged == [JobKind.DERIVATIVES_MESH]

    def test_returns_the_subject_to_pending(
        self, db_session: Session, make_job, owner, mesh, make_derivative, nudged
    ) -> None:
        make_derivative(
            mesh, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED, exhausted=True
        )
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(mesh.id),
            owner=owner,
            state=JobState.FAILED,
        )

        service.retry(job.id, actor=owner)

        assert (
            db_session.exec(
                select(ArtifactDerivative).where(ArtifactDerivative.file_id == mesh.id)
            ).all()
            == []
        )

    def test_keeps_the_progress_it_had_but_not_the_error(
        self, make_job, owner, mesh, nudged
    ) -> None:
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(mesh.id),
            owner=owner,
            state=JobState.CANCELLED,
            status_json=json.dumps(
                {"result": {"scanned": 3}, "total": 10, "error": "boom"}
            ),
        )

        status = service.retry(job.id, actor=owner)

        assert (status.result, status.total, status.error) == ({"scanned": 3}, 10, None)

    @pytest.mark.parametrize("state", [JobState.QUEUED, JobState.COMPLETED])
    def test_refuses_work_without_a_retryable_terminal_outcome(
        self, make_job, owner, state: JobState
    ) -> None:
        job = make_job(owner=owner, state=state)

        error = _refused(lambda: service.retry(job.id, actor=owner))

        assert (error.code, error.kind) == ("job_not_retryable", ErrorKind.CONFLICT)

    def test_a_subject_another_job_owns_cannot_be_retried(
        self, make_job, owner
    ) -> None:
        failed = make_job(
            kind=JobKind.SOURCES_SCAN,
            subject="library/1",
            owner=owner,
            state=JobState.FAILED,
        )
        make_job(kind=JobKind.SOURCES_SCAN, subject="library/1")

        error = _refused(lambda: service.retry(failed.id, actor=owner))

        assert (error.code, error.kind) == ("job_subject_busy", ErrorKind.CONFLICT)

    def test_a_subject_that_is_gone_cannot_be_retried(
        self, db_session: Session, make_job, make_model, make_file, owner
    ) -> None:
        trashed = make_file(make_model(), filename="part.stl", trashed=True)
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(trashed.id),
            owner=owner,
            state=JobState.FAILED,
        )

        error = _refused(lambda: service.retry(job.id, actor=owner))

        assert (error.code, error.kind) == ("job_subject_gone", ErrorKind.GONE)
        assert _state(db_session, job.id) == JobState.FAILED

    def test_another_users_job_does_not_exist_for_them(
        self, make_job, make_user, owner
    ) -> None:
        job = make_job(owner=owner, state=JobState.FAILED)

        error = _refused(lambda: service.retry(job.id, actor=make_user()))

        assert error.kind is ErrorKind.NOT_FOUND


class TestExecutionEpoch:
    def test_retry_before_begin_rejects_old_callback_without_counting_execution(
        self, make_job, mesh, owner, db_session, nudged
    ):
        from app.modules.work.runner import _begin

        job = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject=subject_key(mesh.id), owner=owner
        )
        old_epoch = job.execution_epoch
        service.cancel(job.id, actor=owner)
        service.retry(job.id, actor=owner)
        db_session.refresh(job)

        assert job.attempts == 0
        assert job.execution_epoch != old_epoch
        assert _begin(job.id, 1, old_epoch) is None
        db_session.refresh(job)
        assert job.state is JobState.QUEUED
        assert job.attempts == 0

    def test_retry_without_nudge_is_submitted_by_reconciliation(
        self, make_job, mesh, owner, db_session, nudged, work_engine, work_catalog
    ):
        from app.core.time import utcnow
        from app.modules.work.reconciler import PassResult, _repair
        from app.modules.work.submission import submit

        job = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject=subject_key(mesh.id), owner=owner
        )
        submit(job.id)
        service.cancel(job.id, actor=owner)
        service.retry(job.id, actor=owner)
        db_session.refresh(job)
        assert job.attempts == 1
        result = PassResult()

        _repair(work_catalog.definition(job.kind), now=utcnow(), result=result)

        db_session.refresh(job)
        assert (result.submitted, result.interrupted, job.attempts) == (1, 0, 2)
        assert job.submitted_epoch == job.execution_epoch
        assert execution_id(job.id, 2, job.execution_epoch) in work_engine.executions

    def test_late_submit_confirmation_cannot_rewrite_retried_job(
        self, make_job, mesh, owner, db_session, nudged, work_engine, monkeypatch
    ):
        from app.modules.work.contracts import JobSubmission
        from app.modules.work.submission import submit

        job = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject=subject_key(mesh.id), owner=owner
        )
        old_epoch = job.execution_epoch
        accept = work_engine.submit
        retried_at = []

        def accept_after_retry(submission):
            if isinstance(submission, JobSubmission):
                service.cancel(job.id, actor=owner)
                status = service.retry(job.id, actor=owner)
                retried_at.append(status.updated_at)
            return accept(submission)

        monkeypatch.setattr(work_engine, "submit", accept_after_retry)
        submit(job.id)

        db_session.refresh(job)
        assert job.execution_epoch != old_epoch
        assert job.attempts == 0
        assert job.submitted_epoch is None
        from app.core.time import ensure_utc

        assert ensure_utc(job.updated_at) == ensure_utc(retried_at[0])
        assert (
            work_engine.executions[execution_id(job.id, 1, old_epoch)].status
            is EngineStatus.CANCELLED
        )

    @pytest.mark.parametrize(
        "new_job", [False, True], ids=["retry", "rediscovered-subject"]
    )
    def test_pending_epoch_recovers_old_subject_deduplication(
        self, make_job, mesh, owner, db_session, nudged, work_engine, new_job
    ):
        from app.modules.work.submission import submit

        old = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject=subject_key(mesh.id), owner=owner
        )
        submit(old.id)
        old_epoch = old.execution_epoch
        old_execution = work_engine.executions[execution_id(old.id, 1, old_epoch)]
        service.cancel(old.id, actor=owner)
        # Crash/lost engine cancellation leaves the terminal Job's execution active.
        old_execution.status = EngineStatus.QUEUED
        if new_job:
            current = make_job(kind=old.kind, subject=old.subject_key, owner=owner)
        else:
            service.retry(old.id, actor=owner)
            db_session.refresh(old)
            current = old
        previous_attempts = current.attempts

        submit(current.id)

        db_session.refresh(current)
        assert current.attempts == previous_attempts + 1
        assert current.submitted_epoch == current.execution_epoch
        assert old_execution.status is EngineStatus.CANCELLED
        assert (
            work_engine.executions[
                execution_id(current.id, current.attempts, current.execution_epoch)
            ].status
            is EngineStatus.QUEUED
        )

    def test_dedupe_recovery_does_not_cancel_a_newly_retried_related_job(
        self, make_job, mesh, owner, db_session, nudged, work_engine, monkeypatch
    ):
        from dataclasses import replace

        from app.modules.work.contracts import JobOutcome
        from app.modules.work.jobs import jobs
        from app.modules.work.submission import submit

        old = make_job(
            kind=JobKind.DERIVATIVES_MESH, subject=subject_key(mesh.id), owner=owner
        )
        submit(old.id)
        original = work_engine.executions[execution_id(old.id, 1, old.execution_epoch)]
        service.cancel(old.id, actor=owner)
        original.status = EngineStatus.QUEUED
        current = make_job(kind=old.kind, subject=old.subject_key, owner=owner)
        active = work_engine.active
        retried = []

        def retry_during_active_catalogue():
            jobs.finish(current.id, JobOutcome.CANCELLED, error="superseded")
            service.retry(old.id, actor=owner)
            db_session.refresh(old)
            original.status = EngineStatus.CANCELLED
            execution = replace(
                original.submission,
                execution_epoch=old.execution_epoch,
                execution_id=execution_id(old.id, 2, old.execution_epoch),
                attempt=2,
            )
            work_engine.submit(execution)
            retried.append(execution.execution_id)
            return active()

        monkeypatch.setattr(work_engine, "active", retry_during_active_catalogue)

        submit(current.id)

        assert work_engine.executions[retried[0]].status is EngineStatus.QUEUED
