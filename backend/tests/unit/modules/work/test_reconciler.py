"""The reconciler's decision table: what one non-terminal Job needs next.

``decide`` is where convergence is decided, so every row of the table has its
own test. It compares the Job with the engine's evidence about its current
attempt and returns a verdict with a reason; the reason is what an operator
reads on the Job when it was interrupted or failed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Job, JobState
from app.modules.work.contracts import EngineEvidence, EngineStatus
from app.modules.work.reconciler import Verdict, decide

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
GRACE = timedelta(seconds=60)
VERSION = "1.2.3"


def _job(state: JobState = JobState.RUNNING, *, attempts: int = 1, **fields) -> Job:
    return Job(
        id="j",
        kind="probe",
        subject_key="s",
        state=state,
        attempts=attempts,
        execution_epoch="test-epoch",
        submitted_epoch=fields.pop("submitted_epoch", "test-epoch"),
        updated_at=fields.pop("updated_at", NOW - timedelta(minutes=10)),
        **fields,
    )


def _decide(job: Job, evidence: EngineEvidence | None, **kw):
    return decide(
        job,
        evidence,
        now=NOW,
        app_version=kw.pop("app_version", VERSION),
        stale_executors=kw.pop("stale", set()),
        max_resubmits=kw.pop("max_resubmits", 3),
        grace=GRACE,
    )


def _seen(status: EngineStatus, **kw) -> EngineEvidence:
    return EngineEvidence(status, kw.get("version", VERSION), kw.get("executor", "e1"))


class TestDecide:
    def test_resubmits_an_interrupted_job_within_its_budget(self) -> None:
        decision = _decide(_job(JobState.INTERRUPTED, resubmits=3), None)

        assert (decision.verdict, decision.reason) == (Verdict.SUBMIT, "resubmit")

    def test_fails_a_job_interrupted_too_often(self) -> None:
        decision = _decide(_job(JobState.INTERRUPTED, resubmits=4), None)

        assert (decision.verdict, decision.reason) == (
            Verdict.FAIL,
            "interrupted_repeatedly",
        )

    def test_submits_a_job_that_never_had_an_attempt(self) -> None:
        decision = _decide(_job(JobState.QUEUED, attempts=0), None)

        assert (decision.verdict, decision.reason) == (Verdict.SUBMIT, "first_attempt")

    def test_waits_for_a_submission_still_in_flight(self) -> None:
        # The attempt was recorded moments ago; the engine may not show it yet.
        job = _job(updated_at=NOW - timedelta(seconds=5))

        decision = _decide(job, None)

        assert (decision.verdict, decision.reason) == (
            Verdict.NONE,
            "submission_in_flight",
        )

    def test_interrupts_an_execution_the_engine_lost(self) -> None:
        decision = _decide(_job(), None)

        assert (decision.verdict, decision.reason) == (
            Verdict.INTERRUPT,
            "execution_lost",
        )
        assert decision.cancel_engine is False

    def test_completes_a_job_whose_terminal_write_was_lost(self) -> None:
        decision = _decide(_job(), _seen(EngineStatus.SUCCEEDED))

        assert (decision.verdict, decision.reason) == (
            Verdict.COMPLETE,
            "terminal_write_lost",
        )

    def test_fails_a_job_the_engine_failed(self) -> None:
        decision = _decide(_job(), _seen(EngineStatus.FAILED))

        assert (decision.verdict, decision.reason) == (Verdict.FAIL, "engine_failed")

    def test_interrupts_a_cancelled_execution_of_an_active_job(self) -> None:
        # Only the engine was cancelled; the Job's intent still stands.
        decision = _decide(_job(), _seen(EngineStatus.CANCELLED))

        assert (decision.verdict, decision.reason) == (
            Verdict.INTERRUPT,
            "execution_cancelled",
        )

    def test_interrupts_work_of_another_application_version(self) -> None:
        decision = _decide(_job(), _seen(EngineStatus.RUNNING, version="1.2.2"))

        assert (decision.verdict, decision.reason) == (
            Verdict.INTERRUPT,
            "application_upgraded",
        )
        assert decision.cancel_engine is True

    def test_interrupts_an_execution_stranded_on_a_dead_executor(self) -> None:
        decision = _decide(
            _job(), _seen(EngineStatus.RUNNING, executor="dead"), stale={"dead"}
        )

        assert (decision.verdict, decision.reason) == (
            Verdict.INTERRUPT,
            "executor_lost",
        )
        assert decision.cancel_engine is True

    @pytest.mark.parametrize(
        "status", [EngineStatus.QUEUED, EngineStatus.DELAYED, EngineStatus.RUNNING]
    )
    def test_leaves_live_work_alone(self, status: EngineStatus) -> None:
        decision = _decide(_job(), _seen(status), stale={"someone-else"})

        assert (decision.verdict, decision.reason) == (Verdict.NONE, "in_progress")

    def test_a_queued_execution_on_a_dead_executor_is_left_for_the_queue(
        self,
    ) -> None:
        # Nobody owns a queued execution yet; any live executor can take it.
        decision = _decide(
            _job(), _seen(EngineStatus.QUEUED, executor="dead"), stale={"dead"}
        )

        assert decision.verdict is Verdict.NONE

    def test_evidence_without_a_version_is_not_an_upgrade(self) -> None:
        decision = _decide(_job(), EngineEvidence(EngineStatus.RUNNING, None, "e1"))

        assert decision.verdict is Verdict.NONE
