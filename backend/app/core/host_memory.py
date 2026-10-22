"""Memory this process may use before the kernel reclaims it, container-aware.

Shared by the native mesh pool, local inference and the automatic worker count;
it lives in ``core`` because settings derive their defaults from it.
"""

from pathlib import Path, PurePosixPath


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
    except OSError, ValueError:
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
                except OSError, ValueError:
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
    except OSError, ValueError:
        pass
    try:  # host total
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                limits.append(int(line.split()[1]) * 1024)
                break
    except OSError, ValueError, IndexError:
        pass
    return min(limits) if limits else None
