"""Work a clean stop left running is rerun once the process starts again.

A clean stop waits a few seconds for running executions, then exits and
deletes its executor's row. A step that runs longer (a mesh, a backup, an
inference) stays running in the engine under that executor, and the engine
only ever recovers its own executor's work. Before the reconciler treated an
executor with no row as lost, such an execution was "in progress" forever: its
Job never finished and, on a lane of one, nothing else on that lane ever ran.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.db.models import JobState
from app.modules.work import submission as work_submission
from app.modules.work.reconciler import run_pass
from tests.contract.modules.work._harness import SERIAL_JOB, DbosHarness


def _holds_the_first_attempt(gate: threading.Event):
    """A step body whose first attempt runs until ``gate`` opens."""

    def hold(ctx: Any) -> None:
        if ctx.attempt == 1:
            assert gate.wait(60), "gate never opened"

    hold.gate = gate  # type: ignore[attr-defined]
    return hold


class TestRunPass:
    @pytest.mark.parametrize("harness", ["dbos"], indirect=True)
    def test_reruns_a_job_a_clean_stop_left_running(self, harness: DbosHarness) -> None:
        gate = threading.Event()
        job_id = harness.job(
            SERIAL_JOB, "stop/long", behaviour=_holds_the_first_attempt(gate)
        )
        work_submission.submit(job_id)
        harness.started("stop/long")
        harness.restart_as("restarted-executor")

        run_pass(SERIAL_JOB)
        harness.wait_for(lambda: harness.state(job_id) is JobState.COMPLETED)

        assert harness.state(job_id) is JobState.COMPLETED
