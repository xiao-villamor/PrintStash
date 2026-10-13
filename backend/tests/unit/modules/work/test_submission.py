"""The two engine keys, and how priority ranks in the engine.

``execution_id`` is exactly-once per (Job, attempt); ``dedupe_key`` is
at-most-one-active per (definition, subject). Both are rendered here and
nowhere else, so every engine sees the same keys.
"""

from __future__ import annotations

from app.db.models import JobKind, WorkPriority
from app.modules.work.submission import PRIORITY_RANK, dedupe_key, execution_id


class TestExecutionId:
    def test_is_unique_per_attempt(self) -> None:
        assert execution_id("job-1", 1, "epoch") != execution_id("job-1", 2, "epoch")

    def test_is_stable_for_one_attempt(self) -> None:
        # A crash between submitting and recording the attempt re-derives the
        # same id, so the engine returns the execution it already has.
        assert (
            execution_id("job-1", 2, "epoch")
            == execution_id("job-1", 2, "epoch")
            == "job-1:epoch:2"
        )


class TestDedupeKey:
    def test_ignores_which_job_asks(self) -> None:
        assert (
            dedupe_key(JobKind.DERIVATIVES_MESH, "file/1") == "derivatives.mesh|file/1"
        )

    def test_one_subject_is_distinct_per_definition(self) -> None:
        assert dedupe_key(JobKind.DERIVATIVES_MESH, "file/1") != dedupe_key(
            JobKind.DERIVATIVES_GCODE, "file/1"
        )


class TestPriorityRank:
    def test_interactive_runs_first(self) -> None:
        assert (
            PRIORITY_RANK[WorkPriority.INTERACTIVE]
            < PRIORITY_RANK[WorkPriority.BACKFILL]
        )
