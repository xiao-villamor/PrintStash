"""Host capacity and process-tree observation shared by native supervisors.

This owner imports no mesh loader, renderer or subprocess orchestrator.
"""

from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.runtime.native_admission import Resources


def memory_limit_bytes() -> int | None:
    """Best-effort bytes of RAM the process may use before being OOM-killed.

    Container-aware: a Docker/NAS deployment is usually capped well below host
    RAM by its cgroup, and that limit — not the host's total — is what the kernel
    enforces. Takes the smallest of the cgroup limit (v2 then v1) and host
    ``MemTotal`` so the RAM-aware cap reflects the real ceiling. Returns None when
    nothing can be read (non-Linux, locked-down /proc), disabling the RAM cap.
    """
    limits: list[int] = []
    try:  # cgroup v2
        raw = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if raw != "max":
            limits.append(int(raw))
    except (OSError, ValueError):
        pass
    # On a host service the cgroup filesystem is mounted above this process's
    # group. Reading only its root misses MemoryMax on the service or a parent
    # slice. Containers with a cgroup namespace already expose their group at /.
    try:
        root = Path("/sys/fs/cgroup")
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if not line.startswith("0::/"):
                continue
            parts = PurePosixPath(line[3:]).parts[1:]
            if len(parts) > 128 or any(part in (".", "..") for part in parts):
                continue
            group = root.joinpath(*parts)
            while group != root:
                try:
                    value = int((group / "memory.max").read_text().strip())
                    if value > 0:
                        limits.append(value)
                except (OSError, ValueError):
                    pass
                group = group.parent
    except OSError:
        pass
    try:  # cgroup v1
        v1 = int(
            Path("/sys/fs/cgroup/memory/memory.limit_in_bytes").read_text().strip()
        )
        if 0 < v1 < (1 << 62):  # v1 uses a huge sentinel for "unlimited"
            limits.append(v1)
    except (OSError, ValueError):
        pass
    try:  # host total
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                limits.append(int(line.split()[1]) * 1024)
                break
    except (OSError, ValueError, IndexError):
        pass
    return min(limits) if limits else None


def native_capacity() -> Resources:
    """Shared host capacity; independent from any currently admitted job."""
    from app.modules.media.native_budget import capacity

    return capacity(
        memory_limit_bytes(),
        settings.mesh_memory_budget_fraction,
        settings.max_render_jobs,
    )


def process_rss_bytes(pid: int) -> int | None:
    """Read one Linux process's resident set; unavailable platforms return None."""

    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def process_tree_rss_bytes(pid: int) -> int | None:
    """Resident memory of an admitted worker including its descendants."""
    pending = [pid]
    seen: set[int] = set()
    total = 0
    found = False
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        rss = process_rss_bytes(current)
        if rss is not None:
            total += rss
            found = True
        try:
            pending.extend(
                int(value)
                for value in Path(f"/proc/{current}/task/{current}/children")
                .read_text()
                .split()
            )
        except (OSError, ValueError):
            # A process can exit between the two reads.
            continue
    return total if found else None


def native_memory_budget_bytes() -> int:
    from app.runtime.native_runtime import current_permit

    permit = current_permit()
    if permit is not None:
        return permit.resources.bytes
    return native_capacity().bytes
