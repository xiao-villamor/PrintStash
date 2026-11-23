"""The Job execution that code runs for is visible to telemetry, and only there.

The contract: inside ``execution_scope`` the identity is readable; outside any
scope it is ``None``, never a guessed value; a scope restores the previous one
when it exits, including on an exception.
"""

from __future__ import annotations

import pytest

from app.core.execution_scope import (
    ExecutionIdentity,
    current_execution,
    execution_scope,
)

IDENTITY = ExecutionIdentity(
    job_id="job-1", execution_id="job-1:e:1", definition="derivatives.mesh"
)


class TestExecutionScope:
    def test_outside_a_job_there_is_no_execution(self) -> None:
        assert current_execution() is None

    def test_a_scope_exposes_its_execution(self) -> None:
        with execution_scope(IDENTITY):
            assert current_execution() == IDENTITY

    def test_a_scope_restores_the_previous_execution_on_error(self) -> None:
        with pytest.raises(RuntimeError), execution_scope(IDENTITY):
            raise RuntimeError("step failed")

        assert current_execution() is None
