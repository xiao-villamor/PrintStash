"""Host detection and admitted geometry ceilings retain explicit headroom.

Concurrent native work shares a weighted pool. An idle large job can use the
whole budget, while actual worker permits narrow its loader ceiling. Static
source ceilings and conservative unknown-cost refusals remain independent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.modules.media import mesh_processing, mesh_render, native_process
from tests.fixtures.mesh_analysis import analyze

from .._meshes import _fake_mesh, _write_binary_stl


class TestRamTriangleCap:
    def test_uses_current_host_capacity(self, monkeypatch) -> None:
        monkeypatch.setattr(
            native_process, "memory_limit_bytes", lambda: 4 * 1024**3
        )
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

        assert mesh_processing._ram_triangle_cap(".stl") == (2 * 1024**3) // 2200

    @pytest.mark.parametrize(
        "suffix,cost", [(".stl", 2200), (".3mf", 3600)], ids=["stl", "3mf"]
    )
    def test_applies_format_specific_geometry_cost(
        self, monkeypatch, suffix, cost
    ) -> None:
        monkeypatch.setattr(
            native_process, "memory_limit_bytes", lambda: 4 * 1024**3
        )
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

        assert mesh_processing._ram_triangle_cap(suffix) == (2 * 1024**3) // cost

    def test_concurrency_does_not_reduce_an_idle_jobs_capacity(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            native_process, "memory_limit_bytes", lambda: 4 * 1024**3
        )
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        one = mesh_processing._ram_triangle_cap(".stl")
        monkeypatch.setitem(_overlay, "max_render_jobs", 4)

        assert mesh_processing._ram_triangle_cap(".stl") == one

    def test_ram_cap_disabled_when_fraction_zero(self, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)

        assert mesh_processing._ram_triangle_cap(".stl") is None

    def test_undetected_memory_retains_bounded_geometry(self, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: None)

        assert mesh_processing._ram_triangle_cap(".stl") == (2 * 1024**3) // 2200


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

        monkeypatch.setattr(mesh_render, "render_mesh_thumbnail", _slow_render)

        threads = [threading.Thread(target=lambda: analyze(p)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert state["peak"] >= 1  # work really ran
        assert state["peak"] <= 2


class TestExceedsCap:
    def test_unknown_estimate_without_a_size_proof_uses_the_bounded_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
        p = tmp_path / "ghost.stl"
        _write_binary_stl(p, 10)

        def fake_stat(self):
            raise OSError("gone")

        monkeypatch.setattr(Path, "stat", fake_stat)
        # A failed stat is not evidence that the file is small enough for an
        # unrestricted trimesh load. Real stat is restored by teardown.
        assert mesh_processing._exceeds_cap(p) is True

    def test_unknown_estimate_is_safe_when_the_byte_budget_is_proven(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        p = tmp_path / "small.ply"
        p.write_bytes(b"ply\nend_header\n")
        monkeypatch.setattr(
            mesh_processing, "_estimate_triangle_count", lambda *_a, **_k: None
        )

        assert mesh_processing._exceeds_cap(p) is False


class TestReclaimMemory:
    def test_reclaim_memory_is_safe_to_call(self) -> None:
        # Must never raise, regardless of libc/platform — it's best-effort cleanup.
        mesh_processing._reclaim_memory()
