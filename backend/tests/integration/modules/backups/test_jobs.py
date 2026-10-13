"""Backups run as Jobs: manual ones on request, automatic ones on their schedule.

A manual backup no longer holds an HTTP request open while the vault is
archived; the route queues ``backups.create`` and the archive's metadata lands in
the Job's result. The daily automatic backup is ``backups.automatic``, whose
schedule comes from the backup configuration and whose domain claim keeps a
resubmitted occurrence from archiving twice in one day. If this goes red, a
backup can silently not happen, happen twice, or report success it did not have.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.db.models import BackupRun, Job, JobKind, JobState, SystemConfig
from app.modules.backups import jobs as backup_jobs
from app.modules.backups.backup_destination import BackupTrigger
from app.modules.work import service as work_service
from app.modules.work.jobs import jobs
from app.modules.work.submission import nudge
from app.runtime.engine.inline import InlineJobEngine
from tests.factories import build_system_config
from tests.integration._backup_harness import BackupEnv, seed_model_with_blob


def _config(env: BackupEnv, **fields: object) -> None:
    with env.new_session() as session:
        build_system_config(session, **fields)


def _manual_backup(env: BackupEnv, engine: InlineJobEngine) -> str:
    with env.new_session() as session:
        job_id = work_service.request(
            session,
            definition=JobKind.BACKUPS_CREATE,
            subject_key="job/manual-backup",
            owner_user_id=None,
        )
        session.commit()
    nudge(JobKind.BACKUPS_CREATE)
    engine.drain()
    return job_id


def _runs(env: BackupEnv) -> list[BackupRun]:
    with env.new_session() as session:
        return list(session.exec(select(BackupRun)).all())


class TestCreate:
    def test_reports_partial_publication_in_the_job_status(
        self,
        backup_env: BackupEnv,
        work_engine: InlineJobEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.modules.backups.backup.contracts import BackupMeta

        def partial_backup(**_kwargs: object) -> BackupMeta:
            return BackupMeta(
                id="partial-backup",
                created_at="2026-01-01T00:00:00Z",
                size_bytes=100,
                storage_backend="local",
                file_count=1,
                app_version="0.14.0",
                path="partial.tar.gz",
                outcome="partial",
            )

        monkeypatch.setattr(
            backup_jobs.backup_creation, "create_backup", partial_backup
        )

        job_id = _manual_backup(backup_env, work_engine)

        status = jobs.get(job_id)
        assert status is not None and status.completion == "partial"

    def test_reports_archive_phases(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid progress\n")
        seen = []
        jobs.subscribe(lambda status: seen.append(status))

        job_id = _manual_backup(backup_env, work_engine)

        updates = [status for status in seen if status.job_id == job_id]
        stages = [status.stage for status in updates]
        assert list(dict.fromkeys(stage for stage in stages if stage is not None)) == [
            "snapshotting",
            "archiving",
            "verifying",
            "publishing",
            "finalizing",
            "completed",
        ]

    def test_reports_archived_file_count(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid count\n")
        seen = []
        jobs.subscribe(lambda status: seen.append(status))

        job_id = _manual_backup(backup_env, work_engine)

        updates = [status for status in seen if status.job_id == job_id]
        assert [(s.processed, s.total) for s in updates if s.stage == "archiving"] == [
            (0, 1),
            (1, 1),
        ]

    def test_reports_the_destination_being_published(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seen = []
        jobs.subscribe(lambda status: seen.append(status))

        job_id = _manual_backup(backup_env, work_engine)

        assert [
            status.current_item
            for status in seen
            if status.job_id == job_id
            and status.stage == "publishing"
            and status.current_item
        ] == ["Local"]

    def test_archives_a_manual_backup(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid manual\n")

        _manual_backup(backup_env, work_engine)

        assert [run.trigger for run in _runs(backup_env)] == [
            BackupTrigger.MANUAL.value
        ]

    def test_records_the_backup_in_the_job_result(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid result\n")

        job_id = _manual_backup(backup_env, work_engine)

        status = jobs.get(job_id)
        assert status is not None and status.state == "completed"
        assert status.result["backup_id"]

    def test_fails_the_job_without_a_destination(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        _config(backup_env, manual_local_backup_enabled=False)

        job_id = _manual_backup(backup_env, work_engine)

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == (
            "failed",
            "backup_destination_required",
        )


class TestAutomatic:
    def _run(self, engine: InlineJobEngine) -> None:
        nudge(JobKind.BACKUPS_AUTOMATIC)
        engine.drain()

    def test_archives_with_the_automatic_trigger_when_due(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid auto\n")
        _config(
            backup_env,
            automatic_backups_enabled=True,
            automatic_backup_time_utc="00:00",
        )

        self._run(work_engine)

        assert [run.trigger for run in _runs(backup_env)] == [
            BackupTrigger.AUTOMATIC.value
        ]

    def test_reports_the_same_progress_as_manual_backups(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        _config(
            backup_env,
            automatic_backups_enabled=True,
            automatic_backup_time_utc="00:00",
        )
        seen = []
        jobs.subscribe(lambda status: seen.append(status))

        self._run(work_engine)

        stages = [
            status.stage for status in seen if status.kind == JobKind.BACKUPS_AUTOMATIC
        ]
        assert "snapshotting" in stages
        assert "verifying" in stages
        assert "publishing" in stages
        assert "finalizing" in stages

    def test_archives_once_however_often_the_schedule_is_reconciled(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid once\n")
        _config(
            backup_env,
            automatic_backups_enabled=True,
            automatic_backup_time_utc="00:00",
        )

        self._run(work_engine)
        self._run(work_engine)

        assert len(_runs(backup_env)) == 1

    def test_skips_a_day_already_claimed(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        from app.core.time import utcnow

        _config(
            backup_env,
            automatic_backups_enabled=True,
            automatic_backup_time_utc="00:00",
            automatic_backup_last_attempt_at=utcnow(),
        )

        self._run(work_engine)

        assert _runs(backup_env) == []

    def test_queues_nothing_while_automatic_backups_are_off(
        self, backup_env: BackupEnv, work_engine: InlineJobEngine
    ) -> None:
        _config(backup_env, automatic_backups_enabled=False)

        self._run(work_engine)

        with backup_env.new_session() as session:
            assert (
                session.exec(
                    select(Job).where(Job.kind == JobKind.BACKUPS_AUTOMATIC)
                ).all()
                == []
            )

    def test_claims_the_day_even_when_archiving_fails(
        self,
        backup_env: BackupEnv,
        work_engine: InlineJobEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _config(
            backup_env,
            automatic_backups_enabled=True,
            automatic_backup_time_utc="00:00",
        )

        def fail_backup(**_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(backup_jobs.backup_creation, "create_backup", fail_backup)

        self._run(work_engine)

        with backup_env.new_session() as session:
            config = session.get(SystemConfig, 1)
            job = session.exec(
                select(Job).where(Job.kind == JobKind.BACKUPS_AUTOMATIC)
            ).one()
        assert config is not None and config.automatic_backup_last_attempt_at
        assert job.state == JobState.FAILED


CREATE = next(d for d in backup_jobs.definitions() if d.name == JobKind.BACKUPS_CREATE)
RETRY = next(
    d for d in backup_jobs.definitions() if d.name == JobKind.BACKUPS_RETRY_DESTINATION
)


def _outcome(env: BackupEnv, model, row_id: str) -> str:
    with env.new_session() as session:
        row = session.get(model, row_id)
        assert row is not None
        return row.outcome


class TestCreateHooks:
    @pytest.mark.parametrize("hook", ["cancel", "on_failure"])
    def test_an_ended_backup_job_settles_the_run_it_left(
        self, backup_env: BackupEnv, make_job, hook: str
    ) -> None:
        from app.db.models import BackupDestinationResult
        from tests.factories import build_backup_destination_result, build_backup_run

        job = make_job(kind=JobKind.BACKUPS_CREATE, subject="backup/manual")
        with backup_env.new_session() as session:
            run = build_backup_run(session, job_id=job.id, outcome="running")
            result = build_backup_destination_result(session, run, outcome="publishing")
            run_id, result_id = run.id, result.id

        with backup_env.new_session() as session:
            if hook == "cancel":
                CREATE.cancel(session, "backup/manual")
            else:
                CREATE.on_failure(session, "backup/manual", "boom")
            session.commit()

        assert _outcome(backup_env, BackupRun, run_id) == "failed"
        assert _outcome(backup_env, BackupDestinationResult, result_id) == "failed"

    def test_a_restore_settles_the_run_its_snapshot_shows_running(
        self, backup_env: BackupEnv, make_job
    ) -> None:
        from tests.factories import build_backup_run

        job = make_job(
            kind=JobKind.BACKUPS_CREATE,
            subject="backup/manual",
            state=JobState.RUNNING,
            attempts=1,
        )
        with backup_env.new_session() as session:
            run_id = build_backup_run(session, job_id=job.id, outcome="running").id

        assert work_service.supersede_restored() == 1

        assert _outcome(backup_env, BackupRun, run_id) == "failed"


class TestRetryDestination:
    def _failed(self, env: BackupEnv):
        from tests.factories import build_backup_destination_result, build_backup_run

        with env.new_session() as session:
            run = build_backup_run(session, outcome="partial")
            build_backup_destination_result(session, run, outcome="completed")
            failed = build_backup_destination_result(
                session, run, kind="connection", name="Replica", outcome="failed"
            )
            return run.id, failed.id

    def _request(self, env: BackupEnv, result_id: str) -> str:
        from app.modules.backups.retry_commands import request_retry

        with env.new_session() as session:
            attempt_id = request_retry(session, result_id, owner_user_id=None)
            session.commit()
        return attempt_id

    def test_a_request_queues_one_job_per_attempt(self, backup_env: BackupEnv) -> None:
        from app.db.models import BackupRetryAttempt

        _run, result_id = self._failed(backup_env)

        attempt_id = self._request(backup_env, result_id)

        status = jobs.get(attempt_id)
        assert status is not None
        assert (status.kind, status.state) == (
            JobKind.BACKUPS_RETRY_DESTINATION,
            "queued",
        )
        assert _outcome(backup_env, BackupRetryAttempt, attempt_id) == "queued"

    def test_a_destination_being_retried_is_refused(
        self, backup_env: BackupEnv
    ) -> None:
        from app.modules.backups.backup_replica_retry import RetryRefused

        _run, result_id = self._failed(backup_env)
        self._request(backup_env, result_id)

        with pytest.raises(RetryRefused, match="backup_retry_in_progress"):
            self._request(backup_env, result_id)

    def test_a_destination_that_did_not_fail_is_refused(
        self, backup_env: BackupEnv
    ) -> None:
        from app.modules.backups.backup_replica_retry import RetryRefused
        from tests.factories import build_backup_destination_result, build_backup_run

        with backup_env.new_session() as session:
            run = build_backup_run(session, outcome="completed")
            done = build_backup_destination_result(session, run, outcome="completed")
            done_id = done.id

        with pytest.raises(RetryRefused, match="backup_retry_not_failed"):
            self._request(backup_env, done_id)

    def test_a_destination_of_a_backup_still_running_is_refused(
        self, backup_env: BackupEnv
    ) -> None:
        # Its backup's own Job is still publishing the other destinations; a
        # retry ending (even by a cancel) would settle that live run under it.
        from app.modules.backups.backup_replica_retry import RetryRefused
        from tests.factories import build_backup_destination_result, build_backup_run

        with backup_env.new_session() as session:
            run = build_backup_run(session, outcome="running")
            build_backup_destination_result(session, run, outcome="publishing")
            failed = build_backup_destination_result(
                session, run, kind="connection", name="Replica", outcome="failed"
            )
            failed_id = failed.id

        with pytest.raises(RetryRefused, match="backup_retry_backup_running"):
            self._request(backup_env, failed_id)

    def test_an_unknown_destination_cannot_be_retried(
        self, backup_env: BackupEnv
    ) -> None:
        with pytest.raises(LookupError):
            self._request(backup_env, "missing")

    @pytest.mark.parametrize(
        ("hook", "reason"),
        [
            ("cancel", "backup_retry_cancelled"),
            ("on_failure", "backup_publication_interrupted"),
        ],
    )
    def test_an_ended_retry_settles_its_attempt(
        self, backup_env: BackupEnv, hook: str, reason: str
    ) -> None:
        from app.db.models import BackupDestinationResult, BackupRetryAttempt
        from app.modules.backups.retry_commands import subject_key

        run_id, result_id = self._failed(backup_env)
        attempt_id = self._request(backup_env, result_id)
        with backup_env.new_session() as session:
            publishing = session.get(BackupDestinationResult, result_id)
            publishing.outcome = "publishing"
            session.add(publishing)
            session.commit()

        with backup_env.new_session() as session:
            if hook == "cancel":
                RETRY.cancel(session, subject_key(result_id))
            else:
                RETRY.on_failure(session, subject_key(result_id), "boom")
            session.commit()

        with backup_env.new_session() as session:
            attempt = session.get(BackupRetryAttempt, attempt_id)
            result = session.get(BackupDestinationResult, result_id)
            run = session.get(BackupRun, run_id)
        assert (attempt.outcome, attempt.error_code) == ("failed", reason)
        assert (result.outcome, result.error_code) == ("failed", reason)
        assert run.outcome == "partial"

    def test_a_retry_that_published_before_it_was_lost_completes(
        self, backup_env: BackupEnv
    ) -> None:
        # Its terminal write was lost; the destination already says so.
        from app.db.models import BackupDestinationResult, BackupRetryAttempt
        from app.modules.backups.retry_commands import run_retry

        run_id, result_id = self._failed(backup_env)
        attempt_id = self._request(backup_env, result_id)
        with backup_env.new_session() as session:
            published = session.get(BackupDestinationResult, result_id)
            published.outcome = "completed"
            session.add(published)
            session.commit()

        destination = run_retry(attempt_id)

        assert destination["outcome"] == "completed"
        assert _outcome(backup_env, BackupRetryAttempt, attempt_id) == "completed"
        assert _outcome(backup_env, BackupRun, run_id) == "completed"

    def test_a_settled_attempt_is_not_run_again(self, backup_env: BackupEnv) -> None:
        from app.modules.backups.retry_commands import run_retry
        from tests.factories import build_backup_retry_attempt

        _run, result_id = self._failed(backup_env)
        with backup_env.new_session() as session:
            from app.db.models import BackupDestinationResult

            result = session.get(BackupDestinationResult, result_id)
            done = build_backup_retry_attempt(session, result, outcome="failed")
            done_id = done.id

        with pytest.raises(LookupError, match="not_open"):
            run_retry(done_id)
