"""The ``similarity.analyze`` Job: each unfinished run advanced in bounded slices.

Every run that is not finished is its own subject, so the engine runs one
execution per run and that execution is the run's fenced writer. A run the
user asked for is interactive; a scheduled one is backfill. While analysis is
disabled only cancellations are offered, so a cancelled run still settles.
Each Job advances its run until the slice runs out and completes; its
completion nudges the source, so a long analysis is a chain of short Jobs. A
slice that could move nothing parks the source instead of spinning.
Cancelling the Job withdraws the run; a Job that fails settles its run as
failed and frees its scope for a new run.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Job, JobKind, JobState, SimilarityRun, WorkPriority
from app.modules.similarity import jobs as similarity_jobs
from app.modules.similarity.configuration import update_settings
from app.modules.similarity.jobs import AnalysisSource, subject_key
from app.modules.similarity.processing import SimilarityProcessor
from app.modules.work.contracts import JobOutcome
from app.modules.work.sources import idle_window, mark_idle
from app.modules.work.submission import execution_id, submit

SOURCE = AnalysisSource()
(ANALYZE,) = similarity_jobs.definitions()


@pytest.fixture
def admin(make_user):
    return make_user(superuser=True)


@pytest.fixture
def enabled(db_session: Session, admin) -> None:
    update_settings(db_session, admin, {"enabled": True})
    db_session.commit()


def _advance(session: Session, work_engine, make_job, run: SimilarityRun) -> dict:
    job = make_job(kind=JobKind.SIMILARITY_ANALYZE, subject=subject_key(run.id))
    submit(job.id)
    work_engine.run_one()
    session.expire_all()
    row = session.get(Job, job.id)
    assert row is not None and row.state == JobState.COMPLETED
    return {"job": row, **json.loads(row.status_json)["result"]}


def _units(monkeypatch, count: int) -> list[tuple[int, str]]:
    """A stand-in run with `count` units of work; records who advanced it."""
    done: list[tuple[int, str]] = []

    def work_one(_self, run_id: int, writer: str) -> bool:
        if len(done) >= count:
            return False
        done.append((run_id, writer))
        return True

    monkeypatch.setattr(SimilarityProcessor, "work_one", work_one)
    return done


class TestAnalysisSource:
    def test_each_unfinished_run_is_its_own_subject(
        self, db_session: Session, admin, make_user, make_similarity_run, enabled
    ) -> None:
        first = make_similarity_run(admin)
        second = make_similarity_run(make_user())

        items = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert [item.subject_key for item in items] == [
            subject_key(first.id),
            subject_key(second.id),
        ]

    def test_a_requested_run_is_interactive(
        self, db_session: Session, admin, make_similarity_run, enabled
    ) -> None:
        make_similarity_run(admin, trigger="manual")

        (item,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert item.priority is WorkPriority.INTERACTIVE

    def test_a_scheduled_run_is_backfill(
        self, db_session: Session, admin, make_similarity_run, enabled
    ) -> None:
        make_similarity_run(admin, trigger="schedule")

        (item,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert item.priority is WorkPriority.BACKFILL

    def test_finished_runs_need_nothing(
        self, db_session: Session, admin, make_similarity_run, enabled
    ) -> None:
        make_similarity_run(admin, active=False)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_while_disabled_only_a_cancellation_is_offered(
        self, db_session: Session, admin, make_user, make_similarity_run
    ) -> None:
        make_similarity_run(admin)
        cancelled = make_similarity_run(
            make_user(), cancel_requested=True, state="cancelling"
        )

        (item,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert item.subject_key == subject_key(cancelled.id)

    def test_never_offers_more_than_asked(
        self, db_session: Session, admin, make_user, make_similarity_run, enabled
    ) -> None:
        make_similarity_run(admin)
        make_similarity_run(make_user())

        assert len(SOURCE.pending(db_session, now=utcnow(), limit=1)) == 1

    def test_a_parked_source_offers_nothing(
        self, db_session: Session, admin, make_similarity_run, enabled
    ) -> None:
        make_similarity_run(admin)
        mark_idle(JobKind.SIMILARITY_ANALYZE, seconds=30)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_offers_nothing_without_room(
        self, db_session: Session, admin, make_similarity_run, enabled
    ) -> None:
        make_similarity_run(admin)

        assert SOURCE.pending(db_session, now=utcnow(), limit=0) == []


class TestNextDue:
    def test_disabled_analysis_is_never_due(self, db_session: Session) -> None:
        assert SOURCE.next_due(db_session, now=utcnow()) is None

    def test_a_schedule_makes_it_due_after_its_interval(
        self, db_session: Session, admin
    ) -> None:
        update_settings(db_session, admin, {"enabled": True, "schedule_hours": 6})
        db_session.commit()
        now = utcnow()

        assert SOURCE.next_due(db_session, now=now) == now + timedelta(hours=6)

    def test_enabled_without_a_schedule_is_never_due(
        self, db_session: Session, admin
    ) -> None:
        update_settings(db_session, admin, {"enabled": True, "schedule_hours": 0})
        db_session.commit()

        assert SOURCE.next_due(db_session, now=utcnow()) is None

    def test_a_parked_source_is_due_when_its_window_ends(
        self, db_session: Session
    ) -> None:
        now = utcnow()
        mark_idle(JobKind.SIMILARITY_ANALYZE, seconds=30, now=now)

        due = SOURCE.next_due(db_session, now=now)

        assert due is not None
        assert abs((due - (now + timedelta(seconds=30))).total_seconds()) < 1


class TestAdvance:
    def test_advances_its_own_run_as_its_execution(
        self,
        db_session: Session,
        work_engine,
        make_job,
        admin,
        make_similarity_run,
        monkeypatch,
    ) -> None:
        run = make_similarity_run(admin)
        done = _units(monkeypatch, 3)

        result = _advance(db_session, work_engine, make_job, run)

        writer = execution_id(result["job"].id, 1, result["job"].execution_epoch)
        assert done == [(run.id, writer)] * 3
        assert result["units"] == 3

    def test_stops_when_its_slice_runs_out(
        self,
        db_session: Session,
        work_engine,
        make_job,
        admin,
        make_similarity_run,
        monkeypatch,
    ) -> None:
        # The next slice is another Job, nudged by this one's completion.
        _units(monkeypatch, 1000)
        clock = iter(range(0, 10_000, 10))
        # Only this module's clock: each unit appears to take ten seconds.
        monkeypatch.setattr(
            similarity_jobs, "time", SimpleNamespace(monotonic=lambda: next(clock))
        )

        result = _advance(db_session, work_engine, make_job, make_similarity_run(admin))

        assert 0 < result["units"] < 1000

    def test_a_slice_that_moved_nothing_parks_the_source(
        self,
        db_session: Session,
        work_engine,
        make_job,
        admin,
        make_similarity_run,
        monkeypatch,
    ) -> None:
        _units(monkeypatch, 0)

        result = _advance(db_session, work_engine, make_job, make_similarity_run(admin))

        assert result["units"] == 0
        assert idle_window(db_session, JobKind.SIMILARITY_ANALYZE) is not None

    def test_a_slice_that_moved_work_unparks_the_source(
        self,
        db_session: Session,
        work_engine,
        make_job,
        admin,
        make_similarity_run,
        monkeypatch,
    ) -> None:
        mark_idle(JobKind.SIMILARITY_ANALYZE, seconds=30)
        _units(monkeypatch, 1)

        _advance(db_session, work_engine, make_job, make_similarity_run(admin))

        db_session.expire_all()
        assert idle_window(db_session, JobKind.SIMILARITY_ANALYZE) is None

    def test_a_cancelled_job_stops_advancing(
        self,
        db_session: Session,
        work_engine,
        make_job,
        admin,
        make_similarity_run,
        monkeypatch,
    ) -> None:
        from app.modules.work.jobs import jobs

        run = make_similarity_run(admin)
        job = make_job(kind=JobKind.SIMILARITY_ANALYZE, subject=subject_key(run.id))
        done: list[int] = []

        def work_one(_self, _run_id: int, _writer: str) -> bool:
            done.append(1)
            jobs.finish(job.id, JobOutcome.CANCELLED, error="cancelled_by_user")
            return True

        monkeypatch.setattr(SimilarityProcessor, "work_one", work_one)
        submit(job.id)

        work_engine.run_one()

        assert done == [1]


def _reread(session: Session, run_id: int) -> SimilarityRun:
    session.expire_all()
    run = session.get(SimilarityRun, run_id)
    assert run is not None
    return run


class TestHooks:
    def test_cancelling_the_job_withdraws_the_run(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        run = make_similarity_run(admin)

        ANALYZE.cancel(db_session, subject_key(run.id))
        db_session.commit()

        withdrawn = _reread(db_session, run.id)
        assert (withdrawn.cancel_requested, withdrawn.state) == (True, "cancelling")

    def test_a_failed_job_fails_its_run(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        run = make_similarity_run(admin, writer="job:1")

        ANALYZE.on_failure(db_session, subject_key(run.id), "boom")
        db_session.commit()

        failed = _reread(db_session, run.id)
        assert (failed.state, failed.failure_code) == ("failed", "analysis_failed")

    def test_a_failed_run_frees_its_scope_for_a_new_one(
        self, db_session: Session, admin, make_similarity_run
    ) -> None:
        run = make_similarity_run(admin, writer="job:1")

        ANALYZE.on_failure(db_session, subject_key(run.id), "boom")
        db_session.commit()

        failed = _reread(db_session, run.id)
        assert (failed.active_scope_key, failed.writer) == (None, None)

    @pytest.mark.parametrize("hook", ["cancel", "on_failure"])
    def test_a_finished_run_is_not_touched(
        self, db_session: Session, admin, make_similarity_run, hook: str
    ) -> None:
        run = make_similarity_run(admin, active=False)
        subject = subject_key(run.id)

        if hook == "cancel":
            ANALYZE.cancel(db_session, subject)
        else:
            ANALYZE.on_failure(db_session, subject, "boom")
        db_session.commit()

        assert _reread(db_session, run.id).state == "completed"

    def test_a_retry_resumes_the_run(self, db_session: Session) -> None:
        assert ANALYZE.retry(db_session, subject_key(1)) is True
