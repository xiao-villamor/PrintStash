"""Process-wide admission for mesh work, including retained import results.

Reservations precede executor submission, so a completed later result cannot
starve an earlier queued job. Estimates bound admitted work, not native RSS.
"""

from __future__ import annotations

import math
import os
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.core.config import settings

MIB = 1024**2
ADAPTIVE_RENDER: ContextVar[bool] = ContextVar("adaptive_render", default=False)
RENDER_ADMITTED: ContextVar[bool] = ContextVar("render_admitted", default=False)


def effective_cpus() -> int:
    """Conservative integer CPU capacity, respecting affinity and Linux quotas."""
    limits = [float(os.cpu_count() or 1)]
    try:
        limits.append(float(len(os.sched_getaffinity(0))))
    except (AttributeError, OSError):
        pass
    root = Path("/sys/fs/cgroup")
    groups = [root]
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::/"):
                parts = PurePosixPath(line[3:]).parts[1:]
                if len(parts) <= 128 and all(p not in (".", "..") for p in parts):
                    group = root.joinpath(*parts)
                    while group != root:
                        groups.append(group)
                        group = group.parent
    except OSError:
        pass
    for group in groups:
        try:
            quota, period = (group / "cpu.max").read_text().split()
            if quota != "max" and int(quota) > 0 and int(period) > 0:
                limits.append(int(quota) / int(period))
        except (OSError, ValueError):
            pass
    try:
        quota = int((root / "cpu/cpu.cfs_quota_us").read_text())
        period = int((root / "cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            limits.append(quota / period)
    except (OSError, ValueError):
        pass
    return max(1, math.floor(min(limits)))


def memory_budget() -> int:
    from app.modules.media import mesh_limits

    detected = mesh_limits._detect_memory_limit_bytes()
    # Unknown capacity and disabled mesh capping do not authorize unbounded
    # prefetch. The scheduler retains a conservative budget in those modes.
    fraction = float(settings.mesh_memory_budget_fraction) or 0.5
    return max(1, int((detected or 512 * MIB) * fraction))


def import_workers() -> int:
    requested = int(settings.import_workers)
    cpus = effective_cpus()
    # Leave CPU capacity for publication and foreground work. Explicit limits
    # can use the full effective allocation when that improves throughput.
    ceiling = requested if requested else max(1, (cpus + 1) // 2)
    return max(1, min(32, ceiling, cpus, memory_budget() // (512 * MIB)))


def estimate_work(path: Path, file_type: str, budget: int) -> int:
    from app.modules.media import mesh_limits

    suffix = mesh_limits._canonical_suffix(path, file_type)
    # Bulk previews of STL sources above the byte cap never load the whole
    # mesh. The isolated reader has a 256 MiB RSS ceiling; the bounded 100k
    # fallback and retained preview get the rest of this conservative estimate.
    # Reserve that route instead of sizing a full mesh that cannot be loaded.
    size_cap_mb = int(settings.mesh_max_load_mb)
    if suffix == ".stl" and size_cap_mb > 0:
        try:
            if path.stat().st_size > size_cap_mb * MIB:
                return min(budget, 1024 * MIB)
        except OSError:
            return budget
    triangles = mesh_limits._estimate_triangle_count(path, file_type=suffix)
    if triangles is None or suffix in (".step", ".stp"):
        return budget
    cost = mesh_limits._PEAK_BYTES_PER_TRIANGLE.get(
        suffix, mesh_limits._DEFAULT_PEAK_BYTES_PER_TRIANGLE
    )
    # Includes framebuffers, encoded output and fixed parser overhead. Keep the
    # reservation until publication has consumed the result, not only rendering.
    return min(budget, max(128 * MIB, triangles * cost + 128 * MIB))


class Reservation(Protocol):
    def release(self) -> None: ...


class RenderBudget:
    def __init__(self) -> None:
        from printstash_core.mesh.native_rasterizer import kernel

        self.native = kernel().NativeBudget()

    @property
    def used(self) -> int:
        return self.native.used

    @property
    def jobs(self) -> int:
        return self.native.jobs

    def acquire(
        self, size: int, *, capacity: int, jobs: int, wait: bool = True
    ) -> Reservation | None:
        if capacity <= 0 or jobs <= 0:
            raise ValueError("invalid admission capacity")
        return self.native.acquire(min(max(1, size), capacity), capacity, jobs, wait)


budget = RenderBudget()
