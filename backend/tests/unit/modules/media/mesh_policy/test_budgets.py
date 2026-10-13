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

**Failing closed on unknown cost.** `_exceeds_cap` permits an unknown triangle
estimate only after a successful stat proves the source fits the byte budget.
That keeps an unreadable stat or disabled byte ceiling from authorising an
unbounded full-mesh allocation.

`_reclaim_memory` is the other half: a loaded mesh's arrays are returned to the
OS between files so a long library scan's RSS does not only ever climb. It is
best-effort by construction — it must never raise, whatever libc offers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.core.config import _overlay
from app.modules.media import (
    mesh_policy,
    native_process,
)
from app.modules.media.three_mf_scene import read_scene
from tests.factories import content
from tests.factories.geometry import three_mf

from .._meshes import _write_binary_stl


class TestDetectMemoryLimitBytes:
    @pytest.mark.parametrize(
        "membership",
        ["1:cpu:/slice", "0::/", "0::/../outside", "0::/" + "/".join(["slice"] * 129)],
    )
    def test_ignores_unusable_cgroup_membership(self, monkeypatch, membership):
        files = {
            "/proc/self/cgroup": membership,
            "/sys/fs/cgroup/memory.max": "max",
            "/proc/meminfo": "MemTotal:       8388608 kB\n",
        }

        def read(path, *args, **kwargs):
            if str(path) in files:
                return files[str(path)]
            raise OSError("absent")

        monkeypatch.setattr(Path, "read_text", read)

        assert mesh_policy.detect_memory_limit_bytes() == 8 * 1024**3

    @pytest.mark.parametrize(
        "parent,child,expected",
        [("max", "1073741824", 1073741824), ("536870912", "max", 536870912)],
        ids=["service-limit", "parent-slice-limit"],
    )
    def test_detects_effective_nested_cgroup_limit(
        self, monkeypatch, parent, child, expected
    ):
        files = {
            "/proc/self/cgroup": "0::/test.slice/printstash.service\n",
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/test.slice/memory.max": parent,
            "/sys/fs/cgroup/test.slice/printstash.service/memory.max": child,
            "/proc/meminfo": "MemTotal:       8388608 kB\n",
        }

        def read(path, *args, **kwargs):
            try:
                return files[str(path)]
            except KeyError as exc:
                raise OSError("absent") from exc

        monkeypatch.setattr(Path, "read_text", read)

        assert mesh_policy.detect_memory_limit_bytes() == expected

    def test_detect_memory_limit_is_positive_on_linux(self) -> None:
        limit = mesh_policy.detect_memory_limit_bytes()
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
        limit = mesh_policy.detect_memory_limit_bytes()
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
        limit = mesh_policy.detect_memory_limit_bytes()
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
        limit = mesh_policy.detect_memory_limit_bytes()
        assert limit is None or limit > 0

    def test_detect_memory_limit_survives_unreadable_sources(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text
        unreadable = {
            "/proc/self/cgroup",
            "/sys/fs/cgroup/memory.max",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
            "/proc/meminfo",
        }

        def fake_read_text(self, *a, **k):
            if str(self) in unreadable:
                raise OSError("no such file")
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        assert mesh_policy.detect_memory_limit_bytes() is None


class TestRamTriangleCap:
    @pytest.mark.parametrize("suffix,cost", [(".stl", 3000), (".3mf", 3600)])
    def test_uses_the_admitted_memory_allowance(self, monkeypatch, suffix, cost):
        from app.runtime.native_admission import Resources
        from app.runtime.native_runtime import admit

        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        with admit(Resources(1, 72000), Resources(4, 288000), checkpoint=lambda: None):
            assert mesh_policy.ram_triangle_cap(suffix) == 72000 // cost

    def test_uses_current_host_capacity(self, monkeypatch) -> None:
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: 4 * 1024**3)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

        assert mesh_policy.ram_triangle_cap(".stl") == (2 * 1024**3) // 3000

    @pytest.mark.parametrize(
        "suffix,cost", [(".stl", 3000), (".3mf", 3600)], ids=["stl", "3mf"]
    )
    def test_applies_format_specific_geometry_cost(
        self, monkeypatch, suffix, cost
    ) -> None:
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: 4 * 1024**3)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

        assert mesh_policy.ram_triangle_cap(suffix) == (2 * 1024**3) // cost

    def test_concurrency_does_not_reduce_an_idle_jobs_capacity(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: 4 * 1024**3)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        one = mesh_policy.ram_triangle_cap(".stl")
        monkeypatch.setitem(_overlay, "max_render_jobs", 4)

        assert mesh_policy.ram_triangle_cap(".stl") == one

    def test_ram_cap_disabled_when_fraction_zero(self, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)

        assert mesh_policy.ram_triangle_cap(".stl") is None

    def test_undetected_memory_retains_bounded_geometry(self, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: None)

        assert mesh_policy.ram_triangle_cap(".stl") == (2 * 1024**3) // 3000


class TestExceedsCap:
    def test_3mf_keeps_the_global_source_byte_ceiling(self, tmp_path, monkeypatch):
        source = tmp_path / "bytes.3mf"
        source.write_bytes(
            content.zip_bytes({"Metadata/unused.bin": b"x" * 1024**2}, compress=False)
        )
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)

        assert mesh_policy.exceeds_cap(source) is True

    def test_3mf_with_disabled_byte_cap_still_uses_bounded_scene_admission(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "bounded.3mf"
        source.write_bytes(three_mf(extras={"3D/unused.model": b"unreachable-" * 1000}))
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)

        assert mesh_policy.exceeds_cap(source) is False
        with pytest.raises(GeometryError, match="resource_limit"):
            read_scene(source, max_faces=3)

    def test_unknown_estimate_without_a_size_proof_uses_the_bounded_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
        p = tmp_path / "ghost.stl"
        _write_binary_stl(p, 10)

        original_stat = Path.stat

        def fake_stat(self, *args, **kwargs):
            if self == p:
                raise OSError("gone")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", fake_stat)
        # A failed stat is not evidence that the file is small enough for an
        # unrestricted trimesh load. Real stat is restored by teardown.
        assert mesh_policy.exceeds_cap(p) is True

    def test_unknown_estimate_is_safe_when_the_byte_budget_is_proven(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        p = tmp_path / "small.ply"
        p.write_bytes(b"ply\nend_header\n")
        monkeypatch.setattr(
            mesh_policy,
            "estimate_triangle_count",
            lambda *_a, **_k: None,
        )

        assert mesh_policy.exceeds_cap(p) is False


class TestReclaimMemory:
    def test_reclaim_memory_is_safe_to_call(self) -> None:
        # Must never raise, regardless of libc/platform — it's best-effort cleanup.
        mesh_policy.reclaim_memory()
