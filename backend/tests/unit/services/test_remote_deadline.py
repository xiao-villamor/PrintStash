"""A transport budget stops work inside operations and preserves cancellation."""

import asyncio
import threading

import pytest

from app.services.remote_deadline import operation_timeout, paced_sleep, remote_budget
from app.services.storage_backend import StorageConfigurationError


class TestRemoteBudget:
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
