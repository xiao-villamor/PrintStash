"""The Job store: one claim per subject, an honest status, and bounded history.

A Job is the user-visible record of background work on one subject. Three
things about it are load-bearing, and this file defends each against the real
database:

**One active Job per subject.** The reconciler relies on it to never run two
imports of one upload or two renders of one Artifact at once. The claim is the
``uq_jobs_active_subject`` index, and a lost race reads as ``ActiveJobExists``
naming the winner, never as an integrity error.

**The status is honest.** The total is unknown until discovery finishes, a
partial success is distinct from a complete failure, and a terminal Job never
changes again: a late progress write from a lost execution must not reopen it.

**Listing is scoped and bounded.** A user sees only their own Jobs, scoped in
the query before any status JSON is deserialized. Finished history is a bounded
tail, and retention never removes a Job that still owns staged bytes.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.metrics import jobs_terminal, stuck_jobs
from app.core.time import ensure_utc, utcnow
from app.db.models import Job, JobKind, JobState, StagingLease, User, WorkPriority
from app.modules.work.contracts import JobOutcome
from app.modules.work.jobs import ActiveJobExists, JobStore
from tests.factories import build_user


@pytest.fixture
def owner(db_session: Session) -> User:
    return build_user(db_session, "job-owner")


@pytest.fixture
def other_owner(db_session: Session) -> User:
    return build_user(db_session, "job-other-owner")


@pytest.fixture
def store() -> JobStore:
    return JobStore()


def _row(db_session: Session, job_id: str) -> Job:
    db_session.expire_all()
    row = db_session.get(Job, job_id)
    assert row is not None
    return row


def _ids(db_session: Session) -> set[str]:
    db_session.expire_all()
    return set(db_session.exec(select(Job.id)).all())


def _lease(db_session: Session, job: Job, tmp_path) -> None:
    path = tmp_path / f"{job.id}.stl"
    path.write_bytes(b"staged")
    db_session.add(
        StagingLease(
            id=f"lease-{job.id}",
            path=str(path),
            owner_user_id=job.owner_user_id,
            job_id=job.id,
            size_bytes=6,
            sha256="d" * 64,
            expires_at=utcnow() + timedelta(hours=1),
        )
    )
    db_session.commit()


class TestCreate:
    def test_creates_a_queued_job_on_its_subject(
        self, store: JobStore, owner: User, db_session: Session
    ) -> None:
        job_id = store.create(
            definition=JobKind.INGESTION_UPLOAD,
            subject_key="ingest/1",
            owner_user_id=owner.id,
            priority=WorkPriority.BACKFILL,
            status={"label": "cube.stl"},
        )

        row = _row(db_session, job_id)
        assert (row.kind, row.subject_key, row.owner_user_id) == (
            JobKind.INGESTION_UPLOAD,
            "ingest/1",
            owner.id,
        )
        assert (row.state, row.priority) == (JobState.QUEUED, WorkPriority.BACKFILL)
        assert row.app_version == settings.app_version
        assert json.loads(row.status_json) == {"label": "cube.stl"}

    def test_uses_a_caller_supplied_id(self, store: JobStore, owner: User) -> None:
        job_id = store.create(
            definition=JobKind.INGESTION_UPLOAD,
            subject_key="ingest/given",
            owner_user_id=owner.id,
            job_id="given-id",
        )

        assert job_id == "given-id"

    def test_notifies_listeners_of_the_new_job(
        self, store: JobStore, owner: User
    ) -> None:
        seen: list[str] = []
        store.subscribe(lambda status: seen.append(status.state))

        store.create(
            definition=JobKind.INGESTION_UPLOAD,
            subject_key="s/1",
            owner_user_id=owner.id,
        )

        assert seen == ["queued"]

    def test_a_failing_listener_does_not_fail_the_write(
        self, store: JobStore, owner: User, db_session: Session
    ) -> None:
        def broken(_status):
            raise RuntimeError("listener down")

        store.subscribe(broken)

        job_id = store.create(
            definition=JobKind.INGESTION_UPLOAD,
            subject_key="s/2",
            owner_user_id=owner.id,
        )

        assert _row(db_session, job_id).state == JobState.QUEUED

    def test_refuses_a_second_active_job_on_one_subject(
        self, store: JobStore, owner: User
    ) -> None:
        first = store.create(
            definition=JobKind.INGESTION_UPLOAD,
            subject_key="s/dup",
            owner_user_id=owner.id,
        )

        with pytest.raises(ActiveJobExists) as error:
            store.create(
                definition=JobKind.INGESTION_UPLOAD,
                subject_key="s/dup",
                owner_user_id=owner.id,
            )

        assert error.value.job_id == first

    def test_a_finished_job_does_not_claim_its_subject(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        make_job(
            kind=JobKind.INGESTION_UPLOAD, subject="s/again", state=JobState.COMPLETED
        )

        job_id = store.create(
            definition=JobKind.INGESTION_UPLOAD,
            subject_key="s/again",
            owner_user_id=owner.id,
        )

        assert store.get(job_id).state == "queued"  # type: ignore[union-attr]

    def test_one_subject_is_claimed_per_definition(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        make_job(kind=JobKind.DERIVATIVES_MESH, subject="file/1")

        job_id = store.create(
            definition=JobKind.DERIVATIVES_GCODE,
            subject_key="file/1",
            owner_user_id=None,
        )

        assert store.get(job_id) is not None

    def test_a_lost_race_names_the_winner(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        # Both creators passed the existence check; the index decides, and the
        # loser is told who won rather than handed an IntegrityError.
        winner = make_job(kind=JobKind.INGESTION_UPLOAD, subject="s/race")

        with patch.object(JobStore, "_active_id", side_effect=[None, winner.id]):
            with pytest.raises(ActiveJobExists) as error:
                store.create(
                    definition=JobKind.INGESTION_UPLOAD,
                    subject_key="s/race",
                    owner_user_id=owner.id,
                )

        assert error.value.job_id == winner.id

    def test_an_integrity_error_with_no_winner_is_raised(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        taken = make_job(kind=JobKind.INGESTION_UPLOAD, subject="s/pk")

        with pytest.raises(IntegrityError):
            store.create(
                definition=JobKind.INGESTION_UPLOAD,
                subject_key="s/other",
                owner_user_id=owner.id,
                job_id=taken.id,
            )

    def test_joins_the_callers_transaction(
        self, store: JobStore, owner: User, db_session: Session
    ) -> None:
        # The intent (an upload request) and its Job commit together or not at
        # all; a Job without its intent is an orphan the reconciler fails.
        job_id = store.create(
            definition=JobKind.INGESTION_UPLOAD,
            subject_key="s/txn",
            owner_user_id=owner.id,
            session=db_session,
        )
        db_session.rollback()

        assert store.get(job_id) is None

    def test_refuses_a_claimed_subject_inside_the_callers_transaction(
        self, store: JobStore, owner: User, db_session: Session, make_job
    ) -> None:
        existing = make_job(kind=JobKind.INGESTION_UPLOAD, subject="s/txn-dup")

        with pytest.raises(ActiveJobExists) as error:
            store.create(
                definition=JobKind.INGESTION_UPLOAD,
                subject_key="s/txn-dup",
                owner_user_id=owner.id,
                session=db_session,
            )

        assert error.value.job_id == existing.id

    def test_reports_the_active_job_of_a_subject(
        self, store: JobStore, make_job
    ) -> None:
        active = make_job(kind=JobKind.SOURCES_SCAN, subject="library/3")
        make_job(kind=JobKind.SOURCES_SCAN, subject="library/4", state=JobState.FAILED)

        assert store.active_for_subject(JobKind.SOURCES_SCAN, "library/3") == active.id
        assert store.active_for_subject(JobKind.SOURCES_SCAN, "library/4") is None


class TestUpdate:
    def test_progress_keeps_total_unknown_until_discovery(
        self, store: JobStore, make_job
    ) -> None:
        job = make_job()
        store.update(job.id, stage="resolving", processed=0)
        assert store.get(job.id).total is None  # type: ignore[union-attr]

        store.update(job.id, stage="ingesting", total=3, processed=1)

        status = store.get(job.id)
        assert status is not None
        assert (status.processed, status.total) == (1, 3)

    @pytest.mark.parametrize(("reported", "shown"), [(150, 99), (100, 99), (-5, 0)])
    def test_progress_stays_short_of_done_until_the_job_finishes(
        self, store: JobStore, make_job, reported: float, shown: float
    ) -> None:
        # 100% is a promise that the work is over; only finishing makes it.
        job = make_job()

        store.update(job.id, progress=reported)

        assert store.get(job.id).progress == shown  # type: ignore[union-attr]

    def test_progress_never_moves_backwards(self, store: JobStore, make_job) -> None:
        # Steps report independently; a late, lower report must not rewind the bar.
        job = make_job()
        store.update(job.id, progress=60)

        store.update(job.id, progress=20)

        assert store.get(job.id).progress == 60  # type: ignore[union-attr]

    def test_a_finished_job_reads_as_complete(self, store: JobStore, make_job) -> None:
        job = make_job()
        store.update(job.id, progress=40)

        store.finish(job.id, JobOutcome.COMPLETED)

        assert store.get(job.id).progress == 100  # type: ignore[union-attr]

    def test_counts_are_never_negative(self, store: JobStore, make_job) -> None:
        job = make_job()

        store.update(job.id, processed=-3, failed=-1)

        status = store.get(job.id)
        assert status is not None
        assert (status.processed, status.failed) == (0, 0)

    def test_a_terminal_job_never_changes_again(
        self, store: JobStore, make_job, db_session: Session
    ) -> None:
        # A lost execution that reports late must not reopen or relabel a Job
        # the user has already been told is finished.
        job = make_job(state=JobState.COMPLETED)
        before = _row(db_session, job.id).status_json

        store.update(job.id, error="late", progress=5)

        row = _row(db_session, job.id)
        assert (row.state, row.status_json) == (JobState.COMPLETED, before)

    def test_a_missing_job_is_ignored(self, store: JobStore) -> None:
        store.update("no-such-job", progress=1)

        assert store.get("no-such-job") is None

    @pytest.mark.parametrize(
        "stage",
        [
            "resolving",
            "downloading",
            "inspecting",
            "extracting",
            "hashing",
            "ingesting",
            "completed",
        ],
    )
    def test_supports_every_import_stage(
        self, store: JobStore, make_job, stage: str
    ) -> None:
        job = make_job()

        store.update(job.id, stage=stage)

        assert store.get(job.id).stage == stage  # type: ignore[union-attr]

    def test_notifies_listeners_of_each_change(self, store: JobStore, make_job) -> None:
        job = make_job(state=JobState.RUNNING)
        seen: list[tuple[str, float | None]] = []
        store.subscribe(lambda status: seen.append((status.state, status.progress)))

        store.update(job.id, progress=30)

        assert seen == [("running", 30.0)]


class TestFinish:
    def test_reports_a_job_that_partly_succeeded_as_partial(
        self, store: JobStore, make_job
    ) -> None:
        job = make_job(state=JobState.RUNNING)

        store.finish(
            job.id, JobOutcome.COMPLETED, succeeded=2, failed=1, retryable=True
        )

        # Distinct from both "completed" and "failed": some models arrived and
        # some did not, and the user has to be told which without being told the
        # whole import worked.
        assert store.get(job.id).completion == "partial"  # type: ignore[union-attr]

    def test_a_skipped_item_also_makes_a_success_partial(
        self, store: JobStore, make_job
    ) -> None:
        job = make_job(state=JobState.RUNNING)

        store.finish(job.id, JobOutcome.COMPLETED, succeeded=1, skipped=1)

        assert store.get(job.id).completion == "partial"  # type: ignore[union-attr]

    def test_a_clean_success_is_complete(self, store: JobStore, make_job) -> None:
        job = make_job(state=JobState.RUNNING)

        store.finish(job.id, JobOutcome.COMPLETED, succeeded=3)

        status = store.get(job.id)
        assert status is not None
        assert (status.completion, status.progress, status.stage) == (
            "complete",
            100.0,
            "completed",
        )

    def test_keeps_a_completion_the_definition_reported(
        self, store: JobStore, make_job
    ) -> None:
        job = make_job(state=JobState.RUNNING)

        store.finish(job.id, JobOutcome.COMPLETED, completion="partial")

        assert store.get(job.id).completion == "partial"  # type: ignore[union-attr]

    def test_complete_failure_is_distinct_from_partial_success(
        self, store: JobStore, make_job
    ) -> None:
        job = make_job(state=JobState.RUNNING)

        store.finish(
            job.id,
            JobOutcome.FAILED,
            error="download_failed",
            retryable=True,
            completion="partial",
        )

        status = store.get(job.id)
        assert status is not None
        assert (status.state, status.completion, status.succeeded) == (
            "failed",
            None,
            0,
        )
        assert (status.error, status.retryable) == ("download_failed", True)

    def test_a_job_that_never_ran_still_records_a_start(
        self, store: JobStore, make_job, db_session: Session
    ) -> None:
        # queued -> running -> terminal is the only valid sequence, even for a
        # Job cancelled before an executor picked it up.
        job = make_job()

        store.finish(job.id, JobOutcome.CANCELLED)

        row = _row(db_session, job.id)
        assert row.state == JobState.CANCELLED
        assert row.started_at is not None
        assert row.finished_at is not None
        assert ensure_utc(row.started_at) <= ensure_utc(row.finished_at)

    def test_records_the_terminal_outcome_metric(
        self, store: JobStore, make_job
    ) -> None:
        job = make_job(kind=JobKind.BACKUPS_CREATE, state=JobState.RUNNING)
        counter = jobs_terminal.labels(kind=JobKind.BACKUPS_CREATE, result="complete")
        before = counter._value.get()

        store.finish(job.id, JobOutcome.COMPLETED)

        assert counter._value.get() == before + 1

    def test_a_failure_must_say_why(
        self, store: JobStore, make_job, db_session: Session
    ) -> None:
        job = make_job(state=JobState.RUNNING)

        with pytest.raises(ValueError, match="failed_job_requires_error"):
            store.finish(job.id, JobOutcome.FAILED)

        assert _row(db_session, job.id).state == JobState.RUNNING

    def test_the_first_terminal_write_wins(
        self, store: JobStore, make_job, db_session: Session
    ) -> None:
        # A cancel racing a step that then finishes: the cancel stands.
        job = make_job(state=JobState.RUNNING)
        store.finish(job.id, JobOutcome.CANCELLED, error="cancelled_by_user")

        store.finish(job.id, JobOutcome.COMPLETED)

        assert _row(db_session, job.id).state == JobState.CANCELLED

    def test_refuses_a_truncating_subject_key(self, store: JobStore) -> None:
        # Cutting a long key short would claim another subject's Jobs.
        with pytest.raises(ValueError, match="job_subject_key_length"):
            store.create(
                definition=JobKind.SOURCES_SCAN,
                subject_key="s" * 256,
                owner_user_id=None,
            )


class TestListForUser:
    def test_an_administrator_sees_scheduled_backups_in_tasks(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        automatic = make_job(kind=JobKind.BACKUPS_AUTOMATIC)
        make_job(kind=JobKind.DERIVATIVES_MESH)

        listed = store.list_for_user(owner.id, is_superuser=True)  # type: ignore[arg-type]

        assert [job.job_id for job in listed] == [automatic.id]

    def test_a_regular_user_cannot_see_scheduled_backups(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        make_job(kind=JobKind.BACKUPS_AUTOMATIC)

        assert store.list_for_user(owner.id) == []  # type: ignore[arg-type]

    def test_reconnect_listing_respects_owner_permissions(
        self, store: JobStore, owner: User, other_owner: User, make_job
    ) -> None:
        own = make_job(owner=owner)
        other = make_job(owner=other_owner)

        assert [job.job_id for job in store.list_for_user(owner.id)] == [own.id]  # type: ignore[arg-type]
        assert {
            job.job_id
            for job in store.list_for_user(owner.id, is_superuser=True)  # type: ignore[arg-type]
        } == {own.id, other.id}

    def test_an_administrator_sees_system_jobs_only_when_asked(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        # System Jobs (derivatives, scans) are high-volume; they would bury
        # every user's own imports in the Task Center.
        system = make_job(kind=JobKind.DERIVATIVES_MESH)

        default = store.list_for_user(owner.id, is_superuser=True)  # type: ignore[arg-type]
        asked = store.list_for_user(owner.id, is_superuser=True, include_system=True)  # type: ignore[arg-type]

        assert system.id not in {job.job_id for job in default}
        assert system.id in {job.job_id for job in asked}

    def test_a_user_never_sees_system_jobs(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        make_job(kind=JobKind.DERIVATIVES_MESH)

        assert store.list_for_user(owner.id, include_system=True) == []  # type: ignore[arg-type]

    def test_filters_by_definition(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        wanted = make_job(kind=JobKind.INGESTION_UPLOAD, owner=owner)
        make_job(kind=JobKind.BACKUPS_CREATE, owner=owner)

        listed = store.list_for_user(owner.id, kinds=[JobKind.INGESTION_UPLOAD])  # type: ignore[arg-type]

        assert [job.job_id for job in listed] == [wanted.id]

    def test_reconnect_listing_scopes_before_status_deserialization(
        self, store: JobStore, owner: User, other_owner: User, make_job
    ) -> None:
        make_job(owner=other_owner, state=JobState.COMPLETED, status_json="not-json")
        mine = make_job(owner=owner, state=JobState.RUNNING)

        listed = store.list_for_user(owner.id)  # type: ignore[arg-type]

        assert [job.job_id for job in listed] == [mine.id]

    def test_lists_active_jobs_with_a_bounded_terminal_tail(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        now = utcnow()
        active = make_job(owner=owner, state=JobState.RUNNING, updated_at=now)
        done = [
            make_job(
                owner=owner,
                state=JobState.COMPLETED,
                updated_at=now - timedelta(seconds=index + 1),
            )
            for index in range(5)
        ]

        listed = store.list_for_user(owner.id, terminal_limit=2)  # type: ignore[arg-type]

        assert [job.job_id for job in listed] == [active.id, done[0].id, done[1].id]

    def test_an_interrupted_job_is_listed_as_active(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        job = make_job(owner=owner, state=JobState.INTERRUPTED)

        listed = store.list_for_user(owner.id, terminal_limit=0)  # type: ignore[arg-type]

        assert [item.job_id for item in listed] == [job.id]

    def test_explicitly_tracked_terminal_job_survives_history_expiry(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        old = utcnow() - timedelta(hours=2)
        job = make_job(
            owner=owner,
            state=JobState.COMPLETED,
            status_json=json.dumps({"progress": 100}),
            updated_at=old,
        )
        make_job(owner=owner, state=JobState.COMPLETED)

        listed = store.list_for_user(
            owner.id,  # type: ignore[arg-type]
            terminal_limit=1,
            tracked_job_ids=(job.id,),
        )

        assert (job.id, "completed", 100) in [
            (item.job_id, item.state, item.progress) for item in listed
        ]

    def test_explicit_tracking_does_not_bypass_owner_scope(
        self, store: JobStore, owner: User, other_owner: User, make_job
    ) -> None:
        job = make_job(owner=other_owner, state=JobState.RUNNING)

        listed = store.list_for_user(owner.id, tracked_job_ids=(job.id,))  # type: ignore[arg-type]

        assert all(item.job_id != job.id for item in listed)

    def test_explicit_tracking_deduplicates_recent_history(
        self, store: JobStore, owner: User, make_job
    ) -> None:
        job = make_job(owner=owner, state=JobState.COMPLETED)

        listed = store.list_for_user(owner.id, tracked_job_ids=(job.id, job.id))  # type: ignore[arg-type]

        assert [item.job_id for item in listed].count(job.id) == 1


class TestFailed:
    def test_lists_only_failed_jobs_newest_first(
        self, store: JobStore, make_job
    ) -> None:
        now = utcnow()
        older = make_job(state=JobState.FAILED, updated_at=now - timedelta(minutes=1))
        newer = make_job(state=JobState.FAILED, updated_at=now)
        make_job(state=JobState.COMPLETED)

        assert [job.job_id for job in store.failed()] == [newer.id, older.id]

    def test_is_bounded(self, store: JobStore, make_job) -> None:
        for _ in range(3):
            make_job(state=JobState.FAILED)

        assert len(store.failed(limit=2)) == 2


class TestCounts:
    def test_counts_jobs_per_definition_state(self, store: JobStore, make_job) -> None:
        make_job(kind=JobKind.SOURCES_SCAN)
        make_job(kind=JobKind.SOURCES_SCAN, state=JobState.FAILED)
        make_job(kind=JobKind.SOURCES_SCAN, state=JobState.FAILED)
        make_job(kind=JobKind.BACKUPS_CREATE, state=JobState.RUNNING)

        assert store.counts_by_definition() == {
            "sources.scan": {"queued": 1, "failed": 2},
            "backups.create": {"running": 1},
        }

    def test_snapshot_counts_every_state(self, store: JobStore, make_job) -> None:
        make_job(state=JobState.RUNNING)
        make_job(state=JobState.COMPLETED)

        counts = store.snapshot_counts()

        assert counts["running"] == 1
        assert counts["completed"] == 1
        assert counts["cancelled"] == 0
        assert counts["total"] == 2

    def test_an_active_job_silent_past_three_passes_counts_as_stuck(
        self, store: JobStore, make_job
    ) -> None:
        silent = utcnow() - timedelta(
            seconds=settings.jobs_reconcile_interval_seconds * 3 + 60
        )
        make_job(state=JobState.RUNNING, updated_at=silent)
        make_job(state=JobState.RUNNING)
        make_job(state=JobState.FAILED, updated_at=silent)

        store.snapshot_counts()

        assert stuck_jobs._value.get() == 1


class TestPrune:
    def test_drops_a_user_job_past_retention(
        self, store: JobStore, owner: User, make_job, db_session: Session
    ) -> None:
        expired = utcnow() - timedelta(days=settings.jobs_retention_days, hours=1)
        make_job(owner=owner, state=JobState.COMPLETED, updated_at=expired)
        recent = make_job(owner=owner, state=JobState.COMPLETED).id

        assert store.prune() == 1

        assert _ids(db_session) == {recent}

    def test_drops_a_system_job_sooner(
        self, store: JobStore, owner: User, make_job, db_session: Session
    ) -> None:
        # Derivative Jobs are backfill records by the thousand; a user Job of
        # the same age is still someone's import history.
        day_old = utcnow() - timedelta(
            hours=settings.jobs_system_retention_hours, minutes=5
        )
        make_job(
            kind=JobKind.DERIVATIVES_MESH, state=JobState.FAILED, updated_at=day_old
        )
        user = make_job(owner=owner, state=JobState.FAILED, updated_at=day_old).id

        store.prune()

        assert _ids(db_session) == {user}

    def test_never_drops_an_active_job(
        self, store: JobStore, make_job, db_session: Session
    ) -> None:
        ancient = utcnow() - timedelta(days=365)
        job = make_job(state=JobState.RUNNING, updated_at=ancient, finished=True)

        assert store.prune() == 0
        assert db_session.get(Job, job.id) is not None

    def test_never_drops_a_job_that_owns_staged_bytes(
        self, store: JobStore, owner: User, make_job, db_session: Session, tmp_path
    ) -> None:
        # The lease points at the Job; dropping it would orphan the staged file
        # and fail the lease's foreign key.
        expired = utcnow() - timedelta(days=settings.jobs_retention_days, hours=1)
        job = make_job(owner=owner, state=JobState.FAILED, updated_at=expired)
        _lease(db_session, job, tmp_path)

        assert store.prune() == 0
        assert db_session.get(Job, job.id) is not None

    def test_a_pending_import_outlives_the_job_that_resolved_it(
        self, store: JobStore, owner: User, make_job, db_session: Session
    ) -> None:
        # The inbox item is the user's record; the Job is only its history.
        from app.db.models import InboxItem
        from tests.factories import build_inbox_item

        expired = utcnow() - timedelta(days=settings.jobs_retention_days, hours=1)
        job = make_job(owner=owner, state=JobState.COMPLETED, updated_at=expired)
        item = build_inbox_item(db_session, owner, job_id=job.id)

        assert store.prune() == 1

        db_session.expire_all()
        kept = db_session.get(InboxItem, item.id)
        assert kept is not None and kept.job_id is None

    def test_an_upload_outlives_the_job_that_finalized_it(
        self,
        store: JobStore,
        owner: User,
        make_job,
        make_artifact_upload,
        db_session: Session,
    ) -> None:
        from app.db.models import ArtifactUploadSession

        expired = utcnow() - timedelta(days=settings.jobs_retention_days, hours=1)
        job = make_job(owner=owner, state=JobState.COMPLETED, updated_at=expired)
        upload = make_artifact_upload(owner, job_id=job.id)

        assert store.prune() == 1

        db_session.expire_all()
        kept = db_session.get(ArtifactUploadSession, upload.id)
        assert kept is not None and kept.job_id is None

    def test_a_pruned_ingest_job_takes_its_request_with_it(
        self, store: JobStore, owner: User, make_ingest_request, db_session: Session
    ) -> None:
        from app.db.models import IngestRequest

        request = make_ingest_request(owner)
        job = db_session.get(Job, request.job_id)
        assert job is not None
        job.state = JobState.COMPLETED
        job.finished_at = utcnow() - timedelta(
            days=settings.jobs_retention_days, hours=1
        )
        db_session.add(job)
        db_session.commit()
        job_id = job.id

        assert store.prune() == 1

        db_session.expire_all()
        assert db_session.get(IngestRequest, job_id) is None

    def test_caps_each_users_history(
        self, store: JobStore, owner: User, make_job, db_session: Session
    ) -> None:
        _overlay["jobs_retention_per_user"] = 10
        now = utcnow()
        jobs = [
            make_job(
                owner=owner,
                state=JobState.COMPLETED,
                updated_at=now - timedelta(minutes=index),
            )
            for index in range(12)
        ]

        assert store.prune(now=now) == 2

        db_session.expire_all()
        kept = db_session.exec(
            select(Job.id).where(Job.owner_user_id == owner.id)
        ).all()
        assert set(kept) == {job.id for job in jobs[:10]}

    def test_the_cap_never_drops_an_active_job(
        self, store: JobStore, owner: User, make_job, db_session: Session
    ) -> None:
        _overlay["jobs_retention_per_user"] = 10
        now = utcnow()
        active = make_job(
            owner=owner, state=JobState.RUNNING, updated_at=now - timedelta(days=1)
        )
        for index in range(10):
            make_job(
                owner=owner,
                state=JobState.COMPLETED,
                updated_at=now - timedelta(minutes=index),
            )

        store.prune(now=now)

        assert db_session.get(Job, active.id) is not None


class TestSettleAttempt:
    @pytest.mark.parametrize(
        ("observed_attempt", "observed_state", "old_epoch"),
        [
            pytest.param(1, JobState.RUNNING, False, id="older-attempt"),
            pytest.param(2, JobState.QUEUED, False, id="older-state"),
            pytest.param(2, JobState.RUNNING, True, id="older-epoch"),
        ],
    )
    def test_stale_engine_evidence_leaves_current_derivatives_running(
        self,
        db_session,
        make_model,
        make_file,
        make_job,
        make_derivative,
        observed_attempt,
        observed_state,
        old_epoch,
    ):
        from app.db.models import DerivativeKind, DerivativeState
        from app.modules.derivatives.jobs import definitions

        file = make_file(make_model(), filename="retry.stl")
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=f"file/{file.id}",
            state=JobState.RUNNING,
            attempts=2,
        )
        derivative = make_derivative(
            file, DerivativeKind.THUMBNAIL, state=DerivativeState.RUNNING
        )
        definition = next(
            d for d in definitions() if d.name is JobKind.DERIVATIVES_MESH
        )

        status = JobStore().settle_attempt(
            job.id,
            observed_attempt,
            JobOutcome.FAILED,
            expected_state=observed_state,
            error="old_engine_failure",
            on_failure=definition.on_failure,
            execution_epoch="previous-epoch" if old_epoch else job.execution_epoch,
        )

        assert status is None
        db_session.refresh(job)
        db_session.refresh(derivative)
        assert job.state is JobState.RUNNING
        assert (derivative.state, derivative.failure_reason) == (
            DerivativeState.RUNNING,
            None,
        )

    def test_matching_queued_policy_cancellation_settles_its_derivatives(
        self, db_session, make_model, make_file, make_job, make_derivative
    ):
        from app.db.models import DerivativeKind, DerivativeState
        from app.modules.derivatives.jobs import definitions

        file = make_file(make_model(), filename="disabled.stl")
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=f"file/{file.id}",
            state=JobState.QUEUED,
            attempts=0,
        )
        derivative = make_derivative(
            file, DerivativeKind.THUMBNAIL, state=DerivativeState.RUNNING
        )
        definition = next(
            d for d in definitions() if d.name is JobKind.DERIVATIVES_MESH
        )

        status = JobStore().settle_attempt(
            job.id,
            0,
            JobOutcome.CANCELLED,
            expected_state=JobState.QUEUED,
            error="derivative_group_disabled",
            on_failure=definition.on_failure,
            execution_epoch=job.execution_epoch,
        )

        assert status.state is JobState.CANCELLED
        db_session.refresh(derivative)
        assert (derivative.state, derivative.failure_reason) == (
            DerivativeState.FAILED,
            "derivative_group_disabled",
        )

    def test_replayed_failure_does_not_settle_a_later_job_for_the_subject(
        self, db_session, make_model, make_file, make_job, make_derivative
    ):
        from app.db.models import DerivativeKind, DerivativeState
        from app.modules.derivatives.jobs import definitions

        file = make_file(make_model(), filename="replacement.stl")
        old = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=f"file/{file.id}",
            state=JobState.FAILED,
            attempts=1,
        )
        make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=f"file/{file.id}",
            state=JobState.RUNNING,
            attempts=1,
        )
        derivative = make_derivative(
            file, DerivativeKind.THUMBNAIL, state=DerivativeState.RUNNING
        )
        definition = next(
            d for d in definitions() if d.name is JobKind.DERIVATIVES_MESH
        )

        JobStore().settle_attempt(
            old.id,
            1,
            JobOutcome.FAILED,
            error="replayed_old_failure",
            on_failure=definition.on_failure,
            execution_epoch=old.execution_epoch,
        )

        db_session.refresh(derivative)
        assert (derivative.state, derivative.failure_reason) == (
            DerivativeState.RUNNING,
            None,
        )


class TestReconcileSettledAttempt:
    @pytest.mark.parametrize(
        "old_epoch", [False, True], ids=["older-attempt", "older-epoch"]
    )
    def test_old_cleanup_preserves_staging_owned_by_a_later_attempt(
        self,
        db_session,
        make_user,
        make_ingest_request,
        make_file,
        make_model,
        tmp_path,
        old_epoch,
    ):
        import hashlib

        from app.db.models import IngestRequestKind
        from app.modules.ingestion import jobs as ingestion_jobs
        from app.modules.ingestion import staging_leases

        owner = make_user()
        request = make_ingest_request(
            owner, kind=IngestRequestKind.UPLOAD, state=JobState.COMPLETED
        )
        job = db_session.get(Job, request.job_id)
        job.attempts = 1 if old_epoch else 2
        db_session.add(job)
        db_session.commit()
        make_file(make_model(), filename="committed.stl", ingestion_key=job.id)
        staged = tmp_path / "retry.stl"
        staged.write_bytes(b"new attempt bytes")
        lease = staging_leases.create_job_lease(
            db_session,
            job_id=job.id,
            owner_user_id=owner.id,
            path=staged,
            size_bytes=staged.stat().st_size,
            sha256=hashlib.sha256(staged.read_bytes()).hexdigest(),
        )
        db_session.commit()
        definition = next(
            d
            for d in ingestion_jobs.definitions()
            if d.name is JobKind.INGESTION_UPLOAD
        )

        JobStore().reconcile_settled_attempt(
            job.id,
            1,
            definition.on_settled,
            execution_epoch="previous-epoch" if old_epoch else job.execution_epoch,
        )

        db_session.expire_all()
        assert staged.read_bytes() == b"new attempt bytes"
        assert db_session.get(StagingLease, lease.id) is not None


class TestExecutionEpochInvariant:
    @pytest.mark.parametrize(
        "field,value",
        [("execution_epoch", ""), ("execution_epoch", None), ("submitted_epoch", "")],
    )
    def test_database_rejects_missing_execution_authority(
        self, db_session, make_job, field, value
    ):
        from sqlalchemy import text

        row = make_job()
        with pytest.raises(IntegrityError):
            db_session.execute(
                text(f"UPDATE jobs SET {field} = :value WHERE id = :job_id"),
                {"value": value, "job_id": row.id},
            )
            db_session.commit()


@pytest.fixture
def active_history_plan(db_session, make_model, make_file, make_job, request):
    from app.db.models import FileType
    from tests.factories.ops import build_job_history

    available = make_file(make_model(), file_type=FileType.STL)
    claimed = make_file(make_model(), file_type=FileType.STL)
    build_job_history(
        db_session,
        count=request.param,
        kind=JobKind.DERIVATIVES_MESH,
        subject=f"file/{available.id}",
    )
    backfill = make_job(
        kind=JobKind.DERIVATIVES_MESH,
        state=JobState.RUNNING,
        subject=f"file/{claimed.id}",
        priority=WorkPriority.BACKFILL,
    )
    interactive = make_job(
        kind=JobKind.DERIVATIVES_VIEWER_STL, priority=WorkPriority.INTERACTIVE
    )
    db_session.connection().exec_driver_sql("ANALYZE")
    return available, claimed, backfill, interactive


@pytest.fixture
def emitted_job_queries(db_session):
    from contextlib import contextmanager

    from sqlalchemy import event

    @contextmanager
    def capture():
        queries = []

        def before_cursor(_connection, _cursor, statement, parameters, _context, _many):
            normalized = statement.lstrip().lower()
            if normalized.startswith("select") and "from jobs" in normalized:
                queries.append((statement, parameters))

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", before_cursor)
        try:
            yield queries
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor)

    return capture


def _captured_plans(session, queries, prefix):
    selected = [
        query for query in queries if query[0].lstrip().lower().startswith(prefix)
    ]
    assert selected, "production did not emit the expected Job query"
    connection = session.connection()
    return [
        [
            row[3]
            for row in connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN " + statement, parameters
            ).all()
        ]
        for statement, parameters in selected
    ]


class TestActiveJobQueryPlans:
    @pytest.mark.parametrize("active_history_plan", [1000, 10000], indirect=True)
    def test_source_anti_join_uses_active_history_boundary(
        self, db_session, active_history_plan, emitted_job_queries
    ):
        from app.modules.derivatives.kinds import group
        from app.modules.derivatives.source import DerivativeSource, subject_key
        from app.modules.work.contracts import DiscoveryBudget

        available, claimed, _backfill, _interactive = active_history_plan
        with emitted_job_queries() as queries:
            pending = DerivativeSource(
                group(JobKind.DERIVATIVES_MESH)
            ).pending_prioritized(
                db_session,
                now=utcnow(),
                budget=DiscoveryBudget(total=2, interactive=2, backfill=2),
            )
        assert [item.subject_key for item in pending] == [subject_key(available.id)]
        assert subject_key(claimed.id) not in [item.subject_key for item in pending]
        plans = _captured_plans(db_session, queries, "select files.id")
        assert all(
            any("uq_jobs_active_subject" in detail for detail in plan) for plan in plans
        ), plans
        assert all(
            not any("SCAN jobs" in detail for detail in plan) for plan in plans
        ), plans

    @pytest.mark.parametrize("active_history_plan", [1000, 10000], indirect=True)
    def test_discovery_quota_uses_active_history_boundary(
        self, db_session, active_history_plan, emitted_job_queries, work_catalog
    ):
        from app.modules.work.contracts import DiscoveryBudget
        from app.modules.work.reconciler import _discovery_budget

        definition = work_catalog.definition(JobKind.DERIVATIVES_MESH)
        lane = work_catalog.lanes[definition.lane]
        interactive = max(0, lane.concurrency - 1)
        with emitted_job_queries() as queries:
            budget = _discovery_budget(db_session, definition)
        assert budget == DiscoveryBudget(
            total=min(settings.jobs_reconcile_batch, interactive),
            interactive=interactive,
            backfill=0,
        )
        plans = _captured_plans(db_session, queries, "select jobs.priority")
        assert all(
            any("uq_jobs_active_subject" in detail for detail in plan) for plan in plans
        ), plans
        assert all(
            not any("SCAN jobs" in detail for detail in plan) for plan in plans
        ), plans
