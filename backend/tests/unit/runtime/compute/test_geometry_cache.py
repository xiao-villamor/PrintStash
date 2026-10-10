"""Host input residency stays bounded independently of device allocations."""

import pytest

from app.runtime.compute.contracts import ComputeUnavailable, Reason
from app.runtime.compute.geometry_cache import GeometryCache


class TestGeometryCache:
    def test_reuses_a_validated_input(self):
        cache = GeometryCache(100)
        prepared = object()
        first = cache.insert("a", 40, (3, 1, 3), prepared)
        cache.release(first)
        second = cache.acquire("a", 40, (3, 1, 3))
        assert second.prepared is prepared
        assert cache.used == 40
        assert cache.hits == 1
        assert cache.transferred == 40
        cache.release(second)

    def test_evicts_idle_inputs(self):
        cache = GeometryCache(100)
        first = cache.insert("a", 60, (3, 1, 3), object())
        cache.release(first)
        second = cache.insert("b", 60, (3, 1, 3), object())
        assert cache.acquire("a", 60, (3, 1, 3)) is None
        assert cache.used == 60
        cache.release(second)

    def test_pins_active_inputs(self):
        cache = GeometryCache(100)
        first = cache.insert("a", 60, (3, 1, 3), object())
        with pytest.raises(ComputeUnavailable) as error:
            cache.insert("b", 60, (3, 1, 3), object())
        assert error.value.reason is Reason.CAPACITY
        assert first.pins == 1
        assert cache.used == 60
        cache.release(first)

    @pytest.mark.parametrize("size,counts", [(41, (3, 1, 3)), (40, (4, 1, 3))])
    def test_refuses_a_mismatched_descriptor(self, size, counts):
        cache = GeometryCache(100)
        entry = cache.insert("a", 40, (3, 1, 3), object())
        with pytest.raises(ValueError, match="compute_geometry_reference"):
            cache.acquire("a", size, counts)
        assert entry.pins == 1
        cache.release(entry)

    def test_expires_only_idle_entries(self, monkeypatch):
        from app.runtime.compute import geometry_cache

        monkeypatch.setattr(geometry_cache.time, "monotonic", lambda: 0)
        cache = GeometryCache(100)
        active = cache.insert("a", 40, (3, 1, 3), object())
        idle = cache.insert("b", 40, (3, 1, 3), object())
        cache.release(idle)
        monkeypatch.setattr(geometry_cache.time, "monotonic", lambda: 61)
        assert cache.acquire("b", 40, (3, 1, 3)) is None
        assert cache.used == 40
        cache.release(active)

    def test_rejects_oversized_input(self):
        cache = GeometryCache(10)
        with pytest.raises(ComputeUnavailable) as error:
            cache.insert("a", 11, (3, 1, 3), object())
        assert error.value.reason is Reason.CAPACITY
        assert cache.used == 0

    def test_keeps_one_concurrent_copy(self):
        cache = GeometryCache(100)
        first = cache.insert("a", 40, (3, 1, 3), object())
        second = cache.insert("a", 40, (3, 1, 3), object())
        assert second is first
        assert cache.used == 40
        assert first.pins == 2
        cache.release(first)
        cache.release(second)


class TestHostReservation:
    def test_preserves_queue_capacity_outside_renderer(self, tmp_path):
        from app.runtime.compute.contracts import ComputeMode
        from app.runtime.compute.dispatcher import Dispatcher
        from app.runtime.compute.geometry_cache import (
            INPUT_CACHE_BYTES,
            INPUT_QUEUE_BYTES,
            OUTPUT_QUEUE_BYTES,
        )

        owner = Dispatcher(
            tmp_path, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
        )
        owner.host_capacity = 1024**3
        remaining = (
            owner.host_capacity
            - 192 * 1024**2
            - INPUT_CACHE_BYTES
            - INPUT_QUEUE_BYTES
            - OUTPUT_QUEUE_BYTES
        )
        owner.make_host_room("renderer", remaining)
        with pytest.raises(ComputeUnavailable) as error:
            owner.make_host_room("renderer", remaining + 1)
        assert error.value.reason is Reason.CAPACITY
