"""Native memory policy is independent of the configured concurrency count.

The shared pool's cancellation and configuration-drain contracts live with its
owner in integration/runtime/test_native_admission.py. These tests defend the
media composition of that policy.
"""

from app.core.config import _overlay
from app.modules.media import mesh_isolation, native_process


class TestMemoryBudgetBytes:
    def test_disabling_estimates_keeps_whole_safety_budget(self, monkeypatch):
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        monkeypatch.setitem(_overlay, "max_render_jobs", 2)
        monkeypatch.setattr(
            native_process, "memory_limit_bytes", lambda: 1024**3
        )

        assert mesh_isolation.memory_budget_bytes() == 512 * 1024**2

    def test_native_fallback_is_independent_of_concurrency(self, monkeypatch):
        monkeypatch.setitem(_overlay, "max_render_jobs", 4)
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: None)

        assert native_process.native_memory_budget_bytes() == 2 * 1024**3
