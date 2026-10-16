"""Cancelling a Job keeps its lane's concurrency while its step still runs.

The engine frees an execution's lane slot the moment it is cancelled, but a
step already running stops only when it next checks for withdrawal. A user who
cancels a long native derivation, on a lane sized to the worker's memory,
would otherwise let the lane start another while the first is still inside its
heavy call. The inline engine runs nothing concurrently, so this is a property
of the durable engine only.
"""

from __future__ import annotations

import threading

import pytest

from app.db.session import get_session_factory
from app.modules.work import service as work_service
from app.modules.work import submission as work_submission
from tests.contract.modules.work._harness import RECORD, SERIAL_JOB, Harness, gated
from tests.factories.identity import build_user


class TestCancel:
    @pytest.mark.parametrize("harness", ["dbos"], indirect=True)
    def test_a_running_jobs_lane_slot_is_held_until_its_step_stops(
        self, harness: Harness
    ) -> None:
        gate = threading.Event()
        running = harness.job(SERIAL_JOB, "cancel/running", behaviour=gated(gate))
        work_submission.submit(running)
        harness.started("cancel/running")
        work_submission.submit(harness.job(SERIAL_JOB, "cancel/next"))
        with get_session_factory().scoped_session() as session:
            admin = build_user(session, superuser=True)
            session.expunge(admin)

        work_service.cancel(running, actor=admin)
        harness.elapse(1.0)
        gate.set()
        harness.settle()

        assert RECORD.peak == 1
