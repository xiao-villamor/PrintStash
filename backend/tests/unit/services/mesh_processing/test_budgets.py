"""How much mesh this host can afford, which is not a constant.

A fixed triangle ceiling is wrong in both directions: generous enough for a
32 GB workstation, it OOM-kills a 2 GB Raspberry Pi; tight enough for the Pi, it
refuses thumbnails the workstation would render in a second. So the effective
ceiling is derived at runtime from the memory actually available, divided by how
many renders may run at once (issue #29).

That derivation has three parts, and each has a failure mode worth pinning:

**Detecting the limit.** A container's real ceiling is its cgroup limit, not the
host's `/proc/meminfo` — a 512 MB container on a 64 GB host would otherwise
compute a budget 128x too large. cgroup v2, cgroup v1 and meminfo are all read,
the smallest wins, and every one of them can be absent or unreadable. `None`
means "no idea", which disables the RAM-aware cap rather than guessing.

**Dividing by concurrency.** A bulk upload runs several renders as background
tasks. Each one sized for the *whole* budget would collectively OOM the box, so
the per-job cap divides by `max_render_jobs` and a semaphore holds the actual
count to that number.

**Failing open on the cheap checks.** `_exceeds_cap` stats the file; a stat that
fails must not decide the file is over the cap, because that would silently
refuse a thumbnail for every file on a filesystem that hiccupped.

`_reclaim_memory` is the other half: a loaded mesh's arrays are returned to the
OS between files so a long library scan's RSS does not only ever climb. It is
best-effort by construction — it must never raise, whatever libc offers.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import _overlay
from app.services import mesh_processing

from .._meshes import _fake_mesh, _write_binary_stl


class TestDetectMemoryLimitBytes:
    def test_detect_memory_limit_is_positive_on_linux(self) -> None:
        limit = mesh_processing._detect_memory_limit_bytes()
        # On Linux CI this reads /proc/meminfo or a cgroup; elsewhere it may be None.
        assert limit is None or limit > 0

    def test_detect_memory_limit_reads_cgroup_v2_value(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text

        def fake_read_text(self, *a, **k):
            if str(self) == "/sys/fs/cgroup/memory.max":
                return "2147483648\n"  # 2 GB
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        limit = mesh_processing._detect_memory_limit_bytes()
        assert limit is not None
        assert limit <= 2147483648

    def test_detect_memory_limit_reads_cgroup_v1_value(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text

        def fake_read_text(self, *a, **k):
            if str(self) == "/sys/fs/cgroup/memory.max":
                raise OSError("cgroup v2 absent")
            if str(self) == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
                return "1073741824\n"  # 1 GB
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        limit = mesh_processing._detect_memory_limit_bytes()
        assert limit is not None
        assert limit <= 1073741824

    def test_detect_memory_limit_ignores_unlimited_cgroup_v2(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text

        def fake_read_text(self, *a, **k):
            if str(self) == "/sys/fs/cgroup/memory.max":
                return "max\n"
            if str(self) == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
                raise OSError("absent")
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        # Falls through to /proc/meminfo (real, host-dependent) or None.
        limit = mesh_processing._detect_memory_limit_bytes()
        assert limit is None or limit > 0

    def test_detect_memory_limit_survives_unreadable_sources(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text
        unreadable = {
            "/sys/fs/cgroup/memory.max",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
            "/proc/meminfo",
        }

        def fake_read_text(self, *a, **k):
            if str(self) in unreadable:
                raise OSError("no such file")
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        assert mesh_processing._detect_memory_limit_bytes() is None


class TestRamTriangleCap:
    def test_ram_triangle_cap_uses_cached_memory_limit(self, monkeypatch) -> None:
        # _MEMORY_LIMIT_BYTES already resolved (not None) -> _detect_memory_limit_bytes
        # is never called again.
        monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

        def _boom():  # pragma: no cover - must never run
            raise AssertionError("must reuse cached limit")

        monkeypatch.setattr(mesh_processing, "_detect_memory_limit_bytes", _boom)
        assert mesh_processing._ram_triangle_cap(".stl") is not None

    def test_the_ram_cap_scales_per_format(self, monkeypatch) -> None:
        # Pin a 4 GB ceiling so the result is host-independent.
        monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        stl_cap = mesh_processing._ram_triangle_cap(".stl")
        mf_cap = mesh_processing._ram_triangle_cap(".3mf")
        # 2 GB budget / per-triangle cost.
        assert stl_cap == int(
            2 * 1024**3 / mesh_processing._DEFAULT_PEAK_BYTES_PER_TRIANGLE
        )
        assert mf_cap == int(
            2 * 1024**3 / mesh_processing._PEAK_BYTES_PER_TRIANGLE[".3mf"]
        )
        # 3MF is the heavier format, so its cap is the lower of the two.
        assert mf_cap < stl_cap

    def test_ram_cap_divides_budget_by_max_render_jobs(self, monkeypatch) -> None:
        # Same RAM, same fraction — doubling the concurrent-job count halves the
        # per-job triangle cap.
        monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        one = mesh_processing._ram_triangle_cap(".stl")
        monkeypatch.setitem(_overlay, "max_render_jobs", 2)
        two = mesh_processing._ram_triangle_cap(".stl")

        assert one == int(
            2 * 1024**3 / mesh_processing._DEFAULT_PEAK_BYTES_PER_TRIANGLE
        )
        assert two == one // 2

    def test_ram_cap_disabled_when_fraction_zero(self, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        assert mesh_processing._ram_triangle_cap(".stl") is None

    def test_ram_triangle_cap_none_when_detection_fails(self, monkeypatch) -> None:
        monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", None)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setattr(mesh_processing, "_detect_memory_limit_bytes", lambda: None)
        assert mesh_processing._ram_triangle_cap(".stl") is None


class TestRenderJobsLimit:
    def test_render_jobs_limit_floors_at_one(self, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "max_render_jobs", 0)
        assert mesh_processing._render_jobs_limit() == 1
        monkeypatch.setitem(_overlay, "max_render_jobs", -5)
        assert mesh_processing._render_jobs_limit() == 1

    def test_render_jobs_limit_falls_back_to_one_on_bad_config(
        self, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "max_render_jobs", "not-a-number")
        assert mesh_processing._render_jobs_limit() == 1


class TestRenderSemaphore:
    def test_render_semaphore_caps_concurrent_renders(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import threading
        import time

        monkeypatch.setitem(_overlay, "max_render_jobs", 2)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
        # Drop any cached semaphore built at a different limit by an earlier test.
        monkeypatch.setattr(mesh_processing, "_RENDER_SEMAPHORE", None)

        p = tmp_path / "ok.stl"
        _write_binary_stl(p, 500)
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(500))

        state = {"current": 0, "peak": 0}
        lock = threading.Lock()

        def _slow_render(*_a, **_k):
            with lock:
                state["current"] += 1
                state["peak"] = max(state["peak"], state["current"])
            time.sleep(0.05)  # hold the slot so overlap is observable
            with lock:
                state["current"] -= 1
            return b"PNG"

        monkeypatch.setattr(
            mesh_processing.mesh_render, "render_mesh_thumbnail", _slow_render
        )

        threads = [
            threading.Thread(target=lambda: mesh_processing.analyze_mesh(p))
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert state["peak"] >= 1  # work really ran
        assert state["peak"] <= 2


class TestExceedsCap:
    def test_exceeds_cap_survives_stat_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
        p = tmp_path / "ghost.stl"
        _write_binary_stl(p, 10)

        def fake_stat(self):
            raise OSError("gone")

        monkeypatch.setattr(Path, "stat", fake_stat)
        # size_mb falls back to 0.0 on OSError, so the size cap can't trip; the
        # (also-mocked-out) triangle estimate then decides. Real stat is restored
        # by monkeypatch teardown.
        assert mesh_processing._exceeds_cap(p) is False


class TestReclaimMemory:
    def test_reclaim_memory_is_safe_to_call(self) -> None:
        # Must never raise, regardless of libc/platform — it's best-effort cleanup.
        mesh_processing._reclaim_memory()
