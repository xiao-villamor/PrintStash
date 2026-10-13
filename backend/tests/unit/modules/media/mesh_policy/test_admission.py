"""Native memory policy is independent of the configured concurrency count.

The shared pool's cancellation and configuration-drain contracts live with its
owner in integration/runtime/test_native_admission.py. These tests defend the
media composition of that policy.
"""

from app.core.config import _overlay
from app.modules.media import mesh_policy, native_process


class TestMemoryBudgetBytes:
    def test_disabling_estimates_keeps_whole_safety_budget(self, monkeypatch):
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        monkeypatch.setitem(_overlay, "max_render_jobs", 2)
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: 1024**3)

        assert mesh_policy.native_memory_budget_bytes() == 512 * 1024**2

    def test_native_fallback_is_independent_of_concurrency(self, monkeypatch):
        monkeypatch.setitem(_overlay, "max_render_jobs", 4)
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: None)

        assert native_process.native_memory_budget_bytes() == 2 * 1024**3


class TestRenderAdmission:
    def test_direct_work_reserves_the_whole_pool(self, monkeypatch):
        from app.runtime.native_admission import Resources
        from app.runtime.native_runtime import current_permit

        capacity = Resources(4, 72000)
        monkeypatch.setattr(native_process, "native_capacity", lambda: capacity)
        with mesh_policy.render_admission() as permit:
            assert permit.resources == Resources(1, capacity.bytes)
            assert current_permit() is permit
        assert current_permit() is None

    def test_nested_work_reuses_the_admitted_credit(self, monkeypatch):
        from app.runtime.native_admission import Resources
        from app.runtime.native_runtime import admit, current_permit

        capacity = Resources(4, 288000)
        monkeypatch.setattr(native_process, "native_capacity", lambda: capacity)
        with admit(Resources(1, 72000), capacity, checkpoint=lambda: None) as parent:
            with mesh_policy.render_admission() as nested:
                assert nested is parent
                assert nested.resources == Resources(1, 72000)
            assert current_permit() is parent
        assert current_permit() is None
