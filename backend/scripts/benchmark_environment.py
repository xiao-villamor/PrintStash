"""Capture benchmark conditions without mistaking missing limits for unlimited."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")
SELF_CGROUP = Path("/proc/self/cgroup")
REPOSITORY = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CgroupLimits:
    cpu_quota_cores: float | None
    memory_limit_bytes: int | None
    cpu_limit_read: bool
    memory_limit_read: bool


@dataclass(frozen=True)
class BenchmarkEnvironment:
    commit: str | None
    working_tree_dirty: bool | None
    python_version: str
    platform: str
    machine: str
    cpu_model: str | None
    logical_cpu_count: int | None
    affinity_cpu_count: int | None
    host_memory_bytes: int | None
    cgroup: CgroupLimits
    thread_environment: dict[str, str | None]
    versions: dict[str, str | None]
    performance_gate_qualified: bool = False


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def cgroup_limits() -> CgroupLimits:
    """Read v2 current-group/ancestor limits, with conventional v1 fallback.

    A successful read of 'max' is unlimited at that visible level; an unreadable
    controller remains unknown. Ancestors hidden by a namespace are not inferred.
    """
    groups = [CGROUP_ROOT]
    membership = _read(SELF_CGROUP)
    if membership is not None:
        for line in membership.splitlines():
            if not line.startswith("0::"):
                continue
            relative = Path(line[3:].lstrip("/"))
            if ".." in relative.parts:
                continue
            current = CGROUP_ROOT / relative
            while current != CGROUP_ROOT:
                groups.append(current)
                current = current.parent
    cpu_limits: list[float] = []
    memory_limits: list[int] = []
    cpu_read = memory_read = False
    for group in groups:
        cpu = _read(group / "cpu.max")
        if cpu is not None:
            parts = cpu.split()
            if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) > 0:
                if parts[0] == "max":
                    cpu_read = True
                elif parts[0].isdigit() and int(parts[0]) > 0:
                    cpu_read = True
                    cpu_limits.append(int(parts[0]) / int(parts[1]))
        memory = _read(group / "memory.max")
        if memory == "max":
            memory_read = True
        elif memory is not None and memory.isdigit():
            memory_read = True
            memory_limits.append(int(memory))
    if not cpu_read:
        quota = _read(CGROUP_ROOT / "cpu/cpu.cfs_quota_us")
        period = _read(CGROUP_ROOT / "cpu/cpu.cfs_period_us")
        if (
            quota is not None
            and period is not None
            and period.isdigit()
            and int(period) > 0
        ):
            if quota == "-1":
                cpu_read = True
            elif quota.isdigit() and int(quota) > 0:
                cpu_read = True
                cpu_limits.append(int(quota) / int(period))
    if not memory_read:
        memory = _read(CGROUP_ROOT / "memory/memory.limit_in_bytes")
        if memory is not None and memory.isdigit():
            memory_read = True
            memory_limits.append(int(memory))
    return CgroupLimits(
        min(cpu_limits, default=None),
        min(memory_limits, default=None),
        cpu_read,
        memory_read,
    )


def _git(*arguments: str) -> str | None:
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed local Git arguments
            ["git", *arguments],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def collect_environment() -> BenchmarkEnvironment:
    status = _git("status", "--porcelain", "--untracked-files=normal")
    cpuinfo = _read(Path("/proc/cpuinfo"))
    cpu_model = None
    if cpuinfo is not None:
        for line in cpuinfo.splitlines():
            if line.startswith("model name"):
                cpu_model = line.partition(":")[2].strip()
                break
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    try:
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        if memory <= 0:
            memory = None
    except (AttributeError, OSError, ValueError):
        memory = None
    versions: dict[str, str | None] = {}
    for package in (
        "numpy",
        "scipy",
        "trimesh",
        "pillow",
        "printstash-core",
        "dbos",
        "cascadio",
        "cadquery-ocp-novtk",
    ):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return BenchmarkEnvironment(
        commit=_git("rev-parse", "HEAD"),
        working_tree_dirty=bool(status) if status is not None else None,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        machine=platform.machine(),
        cpu_model=cpu_model,
        logical_cpu_count=os.cpu_count(),
        affinity_cpu_count=affinity,
        host_memory_bytes=memory,
        cgroup=cgroup_limits(),
        thread_environment={
            name: os.environ.get(name)
            for name in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
            )
        },
        versions=versions,
    )
