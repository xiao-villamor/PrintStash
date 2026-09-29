"""The ``JobEngine`` port, held to one contract on the inline engine and DBOS.

Every other tier runs background work on the inline engine and trusts it to
behave like the engine production runs. These tests are that trust: each body
runs against both, and a promise either engine breaks fails here rather than
as a Job that never runs in production.

Three rows exist because DBOS once broke them while the inline engine did not:
a reconcile pass submits the Jobs it finds from inside its own step, a Job's
step submits other work, and a route handler submits from the event loop's
thread. Each is an ordinary path in production.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import JobKind, JobState, LaneName, WorkPriority
from app.modules.work import submission as work_submission
from app.modules.work.contracts import (
    Deduplicated,
    EngineStatus,
    ExecutionKind,
    JobSubmission,
    SubmitOutcome,
)
from app.modules.work.jobs import jobs
from app.modules.work.submission import dedupe_key, execution_id, nudge
from tests.contract.modules.work._harness import (
    DISCOVERED,
    FAST,
    PLAIN,
    RECORD,
    RETRYING,
    SERIAL,
    SERIAL_JOB,
    Flaky,
    Harness,
    gated,
    harness_for,
    shared_app_db,
)


def _submission(
    job_id: str, definition: JobKind, subject: str, lane: LaneName, **kw
) -> JobSubmission:
    return JobSubmission(
        execution_id=kw.pop("execution", execution_id(job_id, 1)),
        job_id=job_id,
        definition=definition,
        subject_key=subject,
        lane=lane,
        priority=kw.pop("priority", WorkPriority.INTERACTIVE),
        attempt=1,
        routing=Deduplicated(kw.pop("dedupe", dedupe_key(definition, subject))),
        **kw,
    )


class TestRunning:
    def test_exposes_progress_reported_inside_a_step(self, harness: Harness) -> None:
        def report(ctx) -> None:
            ctx.update(stage="archiving", processed=3, total=8)

        job_id = harness.job(PLAIN, "progress/1", behaviour=report)
        seen: list[tuple[str, str | None, int, int | None]] = []
        jobs.subscribe(
            lambda status: seen.append(
                (status.job_id, status.stage, status.processed, status.total)
            )
        )

        work_submission.submit(job_id)
        harness.settle()

        assert (job_id, "archiving", 3, 8) in seen

    def test_runs_a_jobs_steps_in_order_to_completion(self, harness: Harness) -> None:
        job_id = harness.job(PLAIN, "plain/1")

        assert work_submission.submit(job_id) is SubmitOutcome.ACCEPTED
        harness.settle()

        assert harness.state(job_id) == "completed"
        assert RECORD.steps == [("plain/1", "first"), ("plain/1", "second")]

    def test_a_failing_step_fails_its_job_before_later_steps(
        self, harness: Harness
    ) -> None:
        def broken(_ctx):
            raise ValueError("disk on fire at /srv/private/x")

        job_id = harness.job(PLAIN, "plain/broken", behaviour=broken)

        work_submission.submit(job_id)
        harness.settle()

        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        # The recorded reason is display-safe: no server path survives.
        assert status.error and "/srv/private" not in status.error
        assert ("plain/broken", "second") not in RECORD.steps

    def test_retries_a_transient_failure_within_one_attempt(
        self, harness: Harness
    ) -> None:
        tries: list[int] = []

        def flaky(_ctx):
            tries.append(1)
            if len(tries) < 3:
                raise Flaky("try again")

        job_id = harness.job(RETRYING, "retry/1", behaviour=flaky)

        work_submission.submit(job_id)
        harness.settle()

        assert harness.state(job_id) == "completed"
        assert len(tries) == 3

    def test_gives_up_once_the_retry_budget_is_spent(self, harness: Harness) -> None:
        tries: list[int] = []

        def always(_ctx):
            tries.append(1)
            raise Flaky("still down")

        job_id = harness.job(RETRYING, "retry/2", behaviour=always)

        work_submission.submit(job_id)
        harness.settle()

        assert harness.state(job_id) == "failed"
        assert len(tries) == 3

    def test_a_non_transient_error_is_not_retried(self, harness: Harness) -> None:
        tries: list[int] = []

        def deterministic(_ctx):
            tries.append(1)
            raise ValueError("bad input")

        job_id = harness.job(RETRYING, "retry/3", behaviour=deterministic)

        work_submission.submit(job_id)
        harness.settle()

        assert harness.state(job_id) == "failed"
        assert len(tries) == 1


class TestIdentity:
    def test_an_execution_id_runs_exactly_once(self, harness: Harness) -> None:
        job_id = harness.job(PLAIN, "exactly/1")
        submission = _submission(job_id, PLAIN, "exactly/1", FAST)

        first = harness.engine.submit(submission)
        again = harness.engine.submit(submission)
        harness.settle()

        assert (first, again) == (SubmitOutcome.ACCEPTED, SubmitOutcome.EXISTING)
        assert RECORD.firsts() == ["exactly/1"]

    def test_an_id_that_already_finished_is_not_run_again(
        self, harness: Harness
    ) -> None:
        job_id = harness.job(PLAIN, "exactly/2")
        submission = _submission(job_id, PLAIN, "exactly/2", FAST)
        harness.engine.submit(submission)
        harness.settle()

        assert harness.engine.submit(submission) is SubmitOutcome.EXISTING
        harness.settle()
        assert RECORD.firsts() == ["exactly/2"]

    def test_a_dedupe_key_admits_one_active_execution(self, harness: Harness) -> None:
        gate = threading.Event()
        first_job = harness.job(SERIAL_JOB, "single/1", behaviour=gated(gate))
        key = "contract|single"

        accepted = harness.engine.submit(
            _submission(first_job, SERIAL_JOB, "single/1", SERIAL, dedupe=key)
        )
        harness.started("single/1")
        # Engine-level: a second execution for the same key, not a Job (a
        # queued Job would rightly be submitted again by the reconciler).
        rejected = harness.engine.submit(
            _submission(
                "not-a-job",
                SERIAL_JOB,
                "single/1",
                SERIAL,
                dedupe=key,
                execution="not-a-job:1",
            )
        )
        gate.set()
        harness.settle()

        assert (accepted, rejected) == (
            SubmitOutcome.ACCEPTED,
            SubmitOutcome.DEDUPLICATED,
        )
        assert "not-a-job:1" not in harness.engine.evidence(["not-a-job:1"])

    def test_a_dedupe_key_is_free_again_once_its_execution_ends(
        self, harness: Harness
    ) -> None:
        key = "contract|again"
        first_job = harness.job(PLAIN, "again/1")
        harness.engine.submit(
            _submission(first_job, PLAIN, "again/1", FAST, dedupe=key)
        )
        harness.settle()
        second_job = harness.job(PLAIN, "again/2")

        outcome = harness.engine.submit(
            _submission(second_job, PLAIN, "again/2", FAST, dedupe=key)
        )
        harness.settle()

        assert outcome is SubmitOutcome.ACCEPTED
        assert RECORD.firsts() == ["again/1", "again/2"]


class TestScheduling:
    def test_a_delayed_execution_waits_for_its_delay(self, harness: Harness) -> None:
        job_id = harness.job(PLAIN, "later/1")
        harness.engine.submit(
            _submission(job_id, PLAIN, "later/1", FAST, delay_seconds=1.5)
        )

        harness.elapse(0.3)
        if harness.kind == "inline":
            harness.engine.drain()  # type: ignore[attr-defined]
        assert RECORD.firsts() == []

        harness.elapse(1.5)
        harness.settle()
        assert RECORD.firsts() == ["later/1"]

    def test_interactive_work_overtakes_queued_backfill(self, harness: Harness) -> None:
        gate = threading.Event()
        blocker = harness.job(
            SERIAL_JOB,
            "order/blocker",
            priority=WorkPriority.BACKFILL,
            behaviour=gated(gate),
        )
        work_submission.submit(blocker)
        harness.started("order/blocker")
        backfill = harness.job(
            SERIAL_JOB, "order/backfill", priority=WorkPriority.BACKFILL
        )
        work_submission.submit(backfill)
        interactive = harness.job(SERIAL_JOB, "order/interactive")
        work_submission.submit(interactive)

        gate.set()
        harness.settle()

        order = RECORD.firsts()
        assert order.index("order/interactive") < order.index("order/backfill")

    def test_a_lane_never_runs_more_than_its_concurrency(
        self, harness: Harness
    ) -> None:
        gate = threading.Event()
        for index in range(3):
            work_submission.submit(
                harness.job(SERIAL_JOB, f"lane/{index}", behaviour=gated(gate))
            )
        harness.started("lane/0")
        harness.elapse(0.5)

        gate.set()
        harness.settle()

        assert len(RECORD.firsts()) == 3
        assert RECORD.peak == 1

    def test_reports_each_lanes_depth(self, harness: Harness) -> None:
        gate = threading.Event()
        work_submission.submit(
            harness.job(SERIAL_JOB, "depth/0", behaviour=gated(gate))
        )
        harness.started("depth/0")
        work_submission.submit(harness.job(SERIAL_JOB, "depth/1"))

        depth = harness.engine.lane_depth(SERIAL)
        active = harness.engine.active()
        gate.set()
        harness.settle()

        assert depth.queued + depth.running == 2
        assert len([a for a in active if a.kind is ExecutionKind.JOB]) >= 2
        after = harness.engine.lane_depth(SERIAL)
        assert (after.queued, after.running) == (0, 0)


class TestCancelling:
    def test_a_cancelled_queued_execution_never_starts(self, harness: Harness) -> None:
        # Engine-level only. Cancelling a Job also withdraws its intent (see
        # the work service); an engine cancel alone is undone by the next
        # reconcile pass, by design.
        gate = threading.Event()
        work_submission.submit(
            harness.job(SERIAL_JOB, "cancel/blocker", behaviour=gated(gate))
        )
        harness.started("cancel/blocker")
        victim = "cancel-victim:1"
        harness.engine.submit(
            _submission(
                "cancel-victim", SERIAL_JOB, "cancel/victim", SERIAL, execution=victim
            )
        )

        harness.engine.cancel(victim)
        gate.set()
        harness.settle()

        evidence = harness.engine.evidence([victim])[victim]
        assert evidence.status is EngineStatus.CANCELLED

    def test_cancelling_a_finished_execution_changes_nothing(
        self, harness: Harness
    ) -> None:
        job_id = harness.job(PLAIN, "cancel/done")
        work_submission.submit(job_id)
        harness.settle()

        harness.engine.cancel(execution_id(job_id, 1))

        evidence = harness.engine.evidence([execution_id(job_id, 1)])
        assert evidence[execution_id(job_id, 1)].status is EngineStatus.SUCCEEDED


class TestEvidence:
    def test_an_unknown_execution_is_absent(self, harness: Harness) -> None:
        assert "never-submitted:1" not in harness.engine.evidence(["never-submitted:1"])

    def test_a_finished_execution_reads_as_succeeded(self, harness: Harness) -> None:
        job_id = harness.job(PLAIN, "evidence/1")
        work_submission.submit(job_id)
        harness.settle()

        evidence = harness.engine.evidence([execution_id(job_id, 1)])[
            execution_id(job_id, 1)
        ]

        assert evidence.status is EngineStatus.SUCCEEDED
        assert evidence.executor_id

    def test_an_execution_of_another_version_is_reported_foreign(
        self, harness: Harness
    ) -> None:
        # An upgrade cancels in-flight work of the old version; this is how
        # the reconciler finds it.
        harness.relaunch(app_version="contract-old", listen_lanes=[])
        job_id = harness.job(PLAIN, "version/1")
        work_submission.submit(job_id)

        harness.relaunch(app_version="contract-new", listen_lanes=[])

        assert execution_id(job_id, 1) in harness.engine.foreign_version_executions()

    def test_pruning_forgets_settled_executions(self, harness: Harness) -> None:
        job_id = harness.job(PLAIN, "prune/1")
        work_submission.submit(job_id)
        harness.settle()

        removed = harness.engine.prune_history(older_than=utcnow() + timedelta(days=1))

        assert removed >= 1
        assert execution_id(job_id, 1) not in harness.engine.evidence(
            [execution_id(job_id, 1)]
        )

    def test_pruning_keeps_an_active_execution(self, harness: Harness) -> None:
        gate = threading.Event()
        job_id = harness.job(SERIAL_JOB, "prune/active", behaviour=gated(gate))
        work_submission.submit(job_id)
        harness.started("prune/active")

        harness.engine.prune_history(older_than=utcnow() + timedelta(days=1))
        evidence = harness.engine.evidence([execution_id(job_id, 1)])
        gate.set()
        harness.settle()

        assert execution_id(job_id, 1) in evidence


class TestSubmittingFromInsideWork:
    def test_a_reconcile_pass_runs_the_work_its_source_reports(
        self, harness: Harness
    ) -> None:
        # The pass submits from inside its own execution. On DBOS that is a
        # step, where starting a workflow is refused unless it is detached.
        RECORD.discover.add("found/1")

        nudge(DISCOVERED)
        harness.wait_for(lambda: "found/1" in RECORD.firsts())
        harness.settle()

        assert RECORD.firsts() == ["found/1"]
        assert RECORD.discover == set()

    def test_a_step_can_submit_other_work(self, harness: Harness) -> None:
        child = harness.job(PLAIN, "child/1")

        def spawn(_ctx):
            work_submission.submit(child)

        parent = harness.job(PLAIN, "parent/1", behaviour=spawn)
        work_submission.submit(parent)
        harness.wait_for(lambda: "child/1" in RECORD.firsts())
        harness.settle()

        assert harness.state(child) == "completed"
        assert harness.state(parent) == "completed"

    def test_work_can_be_submitted_from_an_event_loop_thread(
        self, harness: Harness
    ) -> None:
        # A route handler nudges from the loop's thread, where DBOS's own
        # synchronous API refuses to run.
        job_id = harness.job(PLAIN, "loop/1")

        async def route() -> SubmitOutcome | None:
            return work_submission.submit(job_id)

        outcome = asyncio.run(route())
        harness.settle()

        assert outcome is SubmitOutcome.ACCEPTED
        assert harness.state(job_id) == "completed"

    def test_a_step_may_drive_its_own_event_loop(self, harness: Harness) -> None:
        # An SFTP listing runs its own loop inside a step. Work settled from an
        # async flow must still not run that step on the flow's loop thread.
        def own_loop(_ctx) -> None:
            asyncio.run(asyncio.sleep(0))

        job_id = harness.job(PLAIN, "loop/2", behaviour=own_loop)

        async def flow() -> None:
            work_submission.submit(job_id)
            harness.settle()

        asyncio.run(flow())

        assert harness.state(job_id) == "completed"


class TestReset:
    def test_reset_discards_every_execution(self, harness: Harness) -> None:
        job_id = harness.job(PLAIN, "reset/1")
        work_submission.submit(job_id)
        harness.settle()

        harness.engine.reset()

        if harness.kind == "dbos":
            harness.relaunch(app_version="contract-reset", listen_lanes=None)
        assert execution_id(job_id, 1) not in harness.engine.evidence(
            [execution_id(job_id, 1)]
        )
        # The application's record of the Job is untouched: the engine's state
        # is disposable, the Job row is not.
        assert harness.state(job_id) == JobState.COMPLETED.value

    def test_the_same_engine_runs_new_work_after_a_reset(
        self, harness: Harness
    ) -> None:
        # What a restore does: reset the engine it has, relaunch that same
        # engine, and keep submitting to it.
        harness.engine.reset()
        harness.engine.launch(listen_lanes=None)
        job_id = harness.job(PLAIN, "reset/2")

        work_submission.submit(job_id)
        harness.settle()

        assert harness.state(job_id) == JobState.COMPLETED.value


class TestLaneConcurrency:
    @pytest.mark.parametrize("kind", ["dbos"])
    def test_raising_a_lanes_concurrency_admits_more_at_once(
        self, kind, tmp_path
    ) -> None:
        """Only the durable engine runs executions concurrently."""
        with shared_app_db(), harness_for(kind, tmp_path) as harness:
            harness.engine.set_lane_concurrency(SERIAL, 2)
            gate = threading.Event()
            for index in range(2):
                work_submission.submit(
                    harness.job(SERIAL_JOB, f"wide/{index}", behaviour=gated(gate))
                )
            harness.wait_for(lambda: RECORD.running == 2)

            gate.set()
            harness.settle()

            assert RECORD.peak == 2
