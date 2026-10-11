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

    def test_grows_a_warm_model_without_reloading(self):
        released = []
        ledger = Residency(100)
        ledger.reserve("model", 40, lambda: released.append("model"), 0)
        ledger.reserve("geometry:old", 40, lambda: released.append("geometry"), 0)

        ledger.grow("model", 80)

        assert ledger.used == 80
        assert ledger.entries["model"].size == 80
        assert released == ["geometry"]
        assert ledger.entries["model"].pins == 0

    def test_keeps_warm_model_after_larger_batch_is_refused(self):
        ledger = Residency(100)
        ledger.reserve("model", 40, lambda: None, 0)
        ledger.reserve("runtime", 40, lambda: None, 0)
        ledger.pin("runtime", 0)

        with pytest.raises(ComputeUnavailable):
            ledger.grow("model", 80)

        assert ledger.entries["model"].size == 40
        assert ledger.entries["model"].pins == 0
        assert ledger.used == 80

    def test_rejects_unbalanced_release(self):
        ledger = Residency(100)
        ledger.reserve("model", 60, lambda: None, 0)

        with pytest.raises(RuntimeError, match="compute_unbalanced_pin"):
            ledger.unpin("model")

    @pytest.mark.parametrize("action", ["admit", "expire", "evict"])
    def test_release_callback_can_retire_owned_allocations(self, action):
        released = []
        ledger = Residency(100)

        def release_owner():
            ledger.unpin("owned")
            ledger.remove("owned")
            released.append("owner")

        ledger.reserve("owner", 40, release_owner, 0)
        ledger.reserve("owned", 10, lambda: released.append("owned"), 0)
        ledger.pin("owned", 0)
        ledger.reserve("other", 30, lambda: released.append("other"), 0)

        if action == "admit":
            ledger.reserve("new", 90, lambda: None, 301)
            assert ledger.used == 90
        elif action == "expire":
            ledger.expire(301)
            assert ledger.used == 0
        else:
            ledger.evict_idle()
            assert ledger.used == 0
        assert released == ["owned", "owner", "other"]

    def test_dispatcher_shutdown_closes_renderer_before_its_pinned_children(
        self, tmp_path
    ):
        from app.runtime.compute.contracts import ComputeMode
        from app.runtime.compute.dispatcher import Dispatcher

        owner = Dispatcher(
            tmp_path, mode=ComputeMode.CPU, selector=None, budget_bytes=100
        )
        released = []

        def release_renderer():
            owner.memory.unpin("packing-fence")
            owner.memory.remove("packing-fence")
            released.append("renderer")

        owner.memory.reserve("runtime", 10, lambda: released.append("runtime"), 0)
        owner.memory.pin("runtime", 0)
        owner.memory.reserve("renderer", 40, release_renderer, 0)
        owner.memory.reserve("packing-fence", 4, lambda: released.append("fence"), 0)
        owner.memory.pin("packing-fence", 0)
        # Normal rendering refreshes this owner after its persistent child.
        owner.memory.pin("renderer", 1)
        owner.memory.unpin("renderer")

        owner.close()

        assert owner.memory.used == 0
        assert released == ["fence", "renderer", "runtime"]

    def test_host_admission_skips_allocations_retired_by_their_owner(self, tmp_path):
        from app.runtime.compute.contracts import ComputeMode
        from app.runtime.compute.dispatcher import Dispatcher
        from app.runtime.compute.geometry_cache import (
            INPUT_CACHE_BYTES,
            INPUT_QUEUE_BYTES,
            OUTPUT_QUEUE_BYTES,
        )

        owner = Dispatcher(
            tmp_path, mode=ComputeMode.CPU, selector=None, budget_bytes=100
        )
        owner.host_capacity = (
            192 * 1024**2
            + INPUT_CACHE_BYTES
            + INPUT_QUEUE_BYTES
            + OUTPUT_QUEUE_BYTES
            + 100
        )
        owner.host_sizes = {"renderer": 40, "model": 40}
        released = []

        def release_renderer():
            owner.memory.unpin("packing-fence")
            owner.memory.remove("packing-fence")
            owner.host_sizes.pop("renderer")
            released.append("renderer")

        def release_model():
            owner.host_sizes.pop("model")
            released.append("model")

        owner.memory.reserve("renderer", 40, release_renderer, 0)
        owner.memory.reserve("packing-fence", 4, lambda: released.append("fence"), 0)
        owner.memory.pin("packing-fence", 0)
        owner.memory.reserve("model", 40, release_model, 0)

        owner.make_host_room("next", 90)

        assert owner.host_sizes == {}
        assert owner.memory.used == 0
        assert released == ["fence", "renderer", "model"]


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
