"""A transport budget stops work inside operations and preserves cancellation."""

import asyncio
import threading

import pytest

from app.modules.storage.remote_deadline import (
    operation_timeout,
    paced_sleep,
    remote_budget,
)
from app.modules.storage.storage_backend.contracts import StorageConfigurationError


class TestRemoteBudget:
    def test_job_withdrawal_prevents_transport_work(self):
        from app.core.cancellation import OperationCancelled, cancellation_scope

        with cancellation_scope(lambda: True), pytest.raises(OperationCancelled):
            operation_timeout()

    def test_expired_budget_refuses_another_transport_operation(self):
        with remote_budget(deadline=0):
            with pytest.raises(
                StorageConfigurationError, match="remote_scan_slice_deadline"
            ):
                operation_timeout()

    def test_cancelled_budget_propagates_cancellation(self):
        cancelled = threading.Event()
        cancelled.set()
        with remote_budget(cancelled=cancelled):
            with pytest.raises(asyncio.CancelledError):
                operation_timeout()

    def test_pacing_cannot_sleep_past_the_slice_deadline(self):
        with remote_budget(deadline=0):
            with pytest.raises(
                StorageConfigurationError, match="remote_scan_slice_deadline"
            ):
                paced_sleep(100)
