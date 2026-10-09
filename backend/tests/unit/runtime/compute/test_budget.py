"""Admission protects pinned resources and releases idle allocations exactly once."""

import pytest

from app.runtime.compute.budget import Residency
from app.runtime.compute.contracts import ComputeUnavailable, Reason


class TestResidency:
    def test_evicts_idle_entries_to_admit_work(self):
        released = []
        ledger = Residency(100)
        ledger.reserve("one", 60, lambda: released.append("one"), 0)

        ledger.reserve("two", 70, lambda: None, 1)

        assert released == ["one"]
        assert ledger.used == 70

    def test_preserves_pinned_allocations(self):
        ledger = Residency(100)
        ledger.reserve("active", 60, lambda: None, 0)
        ledger.pin("active", 1)

        with pytest.raises(ComputeUnavailable) as failure:
            ledger.reserve("other", 70, lambda: None, 2)

        assert failure.value.reason is Reason.CAPACITY
        assert ledger.used == 60

    def test_expires_idle_models(self):
        released = []
        ledger = Residency(100)
        ledger.reserve("model", 60, lambda: released.append("model"), 0)

        ledger.expire(300)

        assert released == ["model"]
        assert ledger.used == 0

    def test_refreshes_residency_on_use(self):
        ledger = Residency(100)
        ledger.reserve("model", 60, lambda: None, 0)
        ledger.pin("model", 250)
        ledger.unpin("model")

        ledger.expire(300)

        assert ledger.used == 60

    def test_rejects_unbalanced_release(self):
        ledger = Residency(100)
        ledger.reserve("model", 60, lambda: None, 0)

        with pytest.raises(RuntimeError, match="compute_unbalanced_pin"):
            ledger.unpin("model")


class TestQueueBudget:
    def test_admits_aggregate_frames_before_reading(self):
        from app.runtime.compute.budget import QueueBudget

        budget = QueueBudget(100)
        budget.reserve(80)
        with pytest.raises(ComputeUnavailable):
            budget.reserve(21)
        assert budget.used == 80
        budget.release(80)
        budget.reserve(100)
        assert budget.used == 100
