"""CPUs this process can actually use, and the native mesh job default derived from them.

``os.process_cpu_count()`` honours CPU affinity but not a cgroup CPU quota: a
container started with ``--cpus 4`` on a 12-core host reports 12. The kernel
enforces the quota, so the usable count is the smaller of the two. Positive
fractional quotas round up to whole CPUs before deriving the slot default;
this is a scheduling heuristic, not a bound on each worker's CPU use.
"""

from __future__ import annotations

import math
import os
from pathlib import Path, PurePosixPath

# One native job per usable CPU plus one: measured on a mixed corpus, the extra
# job keeps every core busy while another waits on its source or its outputs.
EXTRA_NATIVE_JOBS = 1
# A job that fits the pool alongside others claims at least the thumbnail
# floor; more jobs than that only queue for memory.
NATIVE_JOB_FLOOR_BYTES = 640 * 1024**2
# Without a detected memory limit the native pool is this large.
UNDETECTED_POOL_BYTES = 2 * 1024**3
# Keep automatic concurrency bounded; explicit overrides remain available.
MAX_DEFAULT_NATIVE_JOBS = 8
# Local inference keeps its former default: one job per two CPUs, 1-4.
CPUS_PER_INFERENCE_JOB = 2
MAX_DEFAULT_INFERENCE_JOBS = 4

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_SELF_CGROUP = Path("/proc/self/cgroup")


def _quota_cpus(cpu_max: str) -> int | None:
    """CPUs granted by a cgroup v2 ``cpu.max`` line; None when unlimited."""
    quota, period = cpu_max.split()
    if quota == "max":
        return None
    return max(1, math.ceil(int(quota) / int(period)))


def _v2_groups(root: Path, self_cgroup: Path) -> list[Path]:
    """The v2 mount root plus the process's own group and its ancestors.

    A container with a cgroup namespace exposes its group at the root; a host
    service sits below the mount, where a parent slice may carry the quota.
    """
    groups = [root]
    try:
        lines = self_cgroup.read_text().splitlines()
    except OSError:
        return groups
    for line in lines:
        if not line.startswith("0::/"):
            continue
        parts = PurePosixPath(line[3:]).parts[1:]
        if any(part in (".", "..") for part in parts):
            continue
        group = root.joinpath(*parts)
        while group != root:
            groups.append(group)
            group = group.parent
    return groups


def _v1_groups(root: Path, self_cgroup: Path) -> list[Path]:
    """CPU-controller mount roots plus the process's v1 group and its ancestors."""
    mounts = {root / "cpu"}
    try:
        mounts.update(path for path in root.iterdir() if "cpu" in path.name.split(","))
    except OSError:
        pass
    groups = sorted(mounts)
    try:
        lines = self_cgroup.read_text().splitlines()
    except OSError:
        return groups
    for line in lines:
        fields = line.split(":", 2)
        if len(fields) != 3 or "cpu" not in fields[1].split(","):
            continue
        membership = PurePosixPath(fields[2])
        if not membership.is_absolute() or ".." in membership.parts:
            continue
        for mount in sorted(mounts):
            group = mount.joinpath(*membership.parts[1:])
            while group != mount:
                groups.append(group)
                group = group.parent
    return groups


def cgroup_cpu_limit(
    root: Path = _CGROUP_ROOT, self_cgroup: Path = _SELF_CGROUP
) -> int | None:
    """The tightest CPU quota on this process's cgroup path; None when none is set."""
    limits: list[int] = []
    for group in _v2_groups(root, self_cgroup):
        try:
            cpus = _quota_cpus((group / "cpu.max").read_text())
        except OSError:
            continue
        if cpus is not None:
            limits.append(cpus)
    for group in _v1_groups(root, self_cgroup):
        try:  # cgroup v1: a quota of -1 means unlimited
            quota = int((group / "cpu.cfs_quota_us").read_text())
            period = int((group / "cpu.cfs_period_us").read_text())
        except OSError:
            continue
        if quota > 0:
            limits.append(max(1, math.ceil(quota / period)))
    return min(limits) if limits else None


def usable_cpus(root: Path = _CGROUP_ROOT, self_cgroup: Path = _SELF_CGROUP) -> int:
    """Smaller of the affinity-visible CPUs and the cgroup quota."""
    visible = os.process_cpu_count()
    if visible is None:
        raise RuntimeError("the operating system reports no CPU count")
    quota = cgroup_cpu_limit(root, self_cgroup)
    return visible if quota is None else min(visible, quota)


def native_jobs_for(cpus: int, pool_bytes: int) -> int:
    """Native jobs that keep *cpus* busy and still fit a pool of *pool_bytes*."""
    if cpus < 1:
        raise ValueError("a process has at least one CPU")
    return min(
        MAX_DEFAULT_NATIVE_JOBS,
        cpus + EXTRA_NATIVE_JOBS,
        fitting_native_jobs(pool_bytes),
    )


def fitting_native_jobs(pool_bytes: int) -> int:
    """Most native jobs whose thumbnail floor fits a pool of *pool_bytes*."""
    if pool_bytes < 1:
        raise ValueError("a native pool has at least one byte")
    return max(1, pool_bytes // NATIVE_JOB_FLOOR_BYTES)


def native_pool_bytes(memory_fraction: float) -> int:
    """The native memory pool of this process's detected memory."""
    from app.core.host_memory import memory_limit_bytes

    memory = memory_limit_bytes()
    if memory is None:
        return UNDETECTED_POOL_BYTES
    return max(1, int(memory * (memory_fraction or 0.5)))


def default_native_jobs(memory_fraction: float) -> int:
    """Default ``max_render_jobs`` for this process's CPUs and native pool."""
    return native_jobs_for(usable_cpus(), native_pool_bytes(memory_fraction))


def inference_jobs_for(cpus: int) -> int:
    """One local inference per two CPUs, at least one and at most four."""
    if cpus < 1:
        raise ValueError("a process has at least one CPU")
    return min(MAX_DEFAULT_INFERENCE_JOBS, max(1, cpus // CPUS_PER_INFERENCE_JOB))
