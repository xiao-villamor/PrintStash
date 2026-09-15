"""Resource admission promises independent of mesh implementation."""

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.modules.media import render_budget as rb


class TestRenderBudget:
    @pytest.mark.parametrize(
        "requested,cpus,mib,expected",
        [
            pytest.param(0, 8, 128, 1, id="small-host"),
            pytest.param(0, 8, 4096, 4, id="large-host"),
            pytest.param(1, 8, 4096, 1, id="explicit-serial"),
            pytest.param(2, 8, 4096, 2, id="manual-cap"),
            pytest.param(8, 2, 4096, 2, id="quota-wins"),
            pytest.param(0, 128, 65536, 32, id="bounded-executor"),
        ],
    )
    def test_worker_count_respects_resources(
        self, monkeypatch, requested, cpus, mib, expected
    ):
        monkeypatch.setitem(_overlay, "import_workers", requested)
        monkeypatch.setattr(rb, "effective_cpus", lambda: cpus)
        monkeypatch.setattr(rb, "memory_budget", lambda: mib * rb.MIB)
        assert rb.import_workers() == expected

    @pytest.mark.parametrize(
        "files,expected",
        [
            pytest.param(
                {"/sys/fs/cgroup/cpu.max": "150000 100000"}, 1, id="fractional-quota"
            ),
            pytest.param({"/sys/fs/cgroup/cpu.max": "max 100000"}, 4, id="affinity"),
            pytest.param(
                {
                    "/proc/self/cgroup": "0::/service/worker",
                    "/sys/fs/cgroup/service/cpu.max": "200000 100000",
                },
                2,
                id="parent-quota",
            ),
            pytest.param(
                {
                    "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "100000",
                    "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
                },
                1,
                id="v1-quota",
            ),
            pytest.param({"/sys/fs/cgroup/cpu.max": "bad"}, 4, id="unreadable-quota"),
        ],
    )
    def test_effective_cpu_capacity(self, monkeypatch, files, expected):
        def read(path, *args, **kwargs):
            if str(path) in files:
                return files[str(path)]
            raise OSError("absent")

        monkeypatch.setattr(Path, "read_text", read)
        monkeypatch.setattr(rb.os, "cpu_count", lambda: 16)
        monkeypatch.setattr(rb.os, "sched_getaffinity", lambda pid: {0, 1, 2, 3})
        assert rb.effective_cpus() == expected

    def test_reserves_memory_across_callers(self):
        gate = rb.RenderBudget()
        first = gate.acquire(60, capacity=100, jobs=4)
        assert gate.acquire(50, capacity=100, jobs=4, wait=False) is None
        first.release()
        assert gate.acquire(100, capacity=100, jobs=4, wait=False) is not None

    def test_limits_active_jobs(self):
        gate = rb.RenderBudget()
        first = gate.acquire(1, capacity=100, jobs=1)
        assert gate.acquire(1, capacity=100, jobs=1, wait=False) is None
        first.release()
        assert gate.acquire(1, capacity=100, jobs=1, wait=False) is not None

    def test_release_is_idempotent(self):
        gate = rb.RenderBudget()
        first = gate.acquire(20, capacity=100, jobs=1)
        first.release()
        first.release()
        assert gate.used == 0
        assert gate.jobs == 0

    def test_unknown_memory_uses_conservative_budget(self, monkeypatch):
        from app.modules.media import mesh_limits

        monkeypatch.setattr(mesh_limits, "_detect_memory_limit_bytes", lambda: None)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        assert rb.memory_budget() == 256 * rb.MIB

    def test_legacy_render_waits_for_import_reservation(self, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from app.modules.media import mesh_processing

        monkeypatch.setattr(rb, "budget", rb.RenderBudget())
        monkeypatch.setattr(rb, "memory_budget", lambda: 1024)
        monkeypatch.setattr(rb, "import_workers", lambda: 2)
        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        lease = rb.budget.acquire(512, capacity=1024, jobs=2)
        entered = Event()

        def render():
            with mesh_processing._render_semaphore():
                entered.set()
            return "rendered"

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(render)
            assert not entered.wait(0.1)
            lease.release()
            assert future.result(timeout=5) == "rendered"
        assert rb.budget.used == 0

    def test_unknown_mesh_cost_reserves_entire_budget(self, monkeypatch, tmp_path):
        from app.modules.media import mesh_limits

        monkeypatch.setattr(
            mesh_limits, "_estimate_triangle_count", lambda *a, **k: None
        )
        assert rb.estimate_work(tmp_path / "model.step", "step", 1024) == 1024

    def test_known_mesh_cost_includes_fixed_overhead(self, monkeypatch, tmp_path):
        from app.modules.media import mesh_limits

        monkeypatch.setattr(
            mesh_limits, "_estimate_triangle_count", lambda *a, **k: 100
        )
        (tmp_path / "model.stl").touch()
        assert (
            rb.estimate_work(tmp_path / "model.stl", "stl", 1024**3)
            == 100 * 2200 + 128 * rb.MIB
        )

    def test_unavailable_cpu_information_falls_back_to_one(self, monkeypatch):
        def unreadable(*args, **kwargs):
            raise OSError("not available")

        monkeypatch.setattr(Path, "read_text", unreadable)
        monkeypatch.setattr(rb.os, "cpu_count", lambda: None)
        monkeypatch.setattr(rb.os, "sched_getaffinity", unreadable)
        assert rb.effective_cpus() == 1


class TestStreamingAdmission:
    @pytest.mark.parametrize(
        "budget", [512 * rb.MIB, 4096 * rb.MIB], ids=["small-server", "larger-server"]
    )
    def test_large_stl_reserves_bounded_recovery(self, monkeypatch, tmp_path, budget):
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        path = tmp_path / "large.stl"
        with path.open("wb") as stream:
            stream.truncate(2 * rb.MIB)
        assert rb.estimate_work(path, "stl", budget) == min(budget, 1024 * rb.MIB)

    @pytest.mark.parametrize("cap", [0, 2], ids=["disabled-cap", "at-cap"])
    def test_full_load_keeps_conservative_reservation(self, monkeypatch, tmp_path, cap):
        from app.modules.media import mesh_limits

        monkeypatch.setitem(_overlay, "mesh_max_load_mb", cap)
        monkeypatch.setattr(
            mesh_limits, "_estimate_triangle_count", lambda *a, **k: 1_000_000
        )
        path = tmp_path / "mesh.stl"
        with path.open("wb") as stream:
            stream.truncate(2 * rb.MIB)
        budget = 4096 * rb.MIB
        assert rb.estimate_work(path, "stl", budget) == 1_000_000 * 2200 + 128 * rb.MIB

    def test_unreadable_stl_reserves_entire_budget(self, tmp_path):
        budget = 4096 * rb.MIB
        assert rb.estimate_work(tmp_path / "missing.stl", "stl", budget) == budget
