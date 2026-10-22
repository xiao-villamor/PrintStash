"""Host capacity and process-tree observation shared by native supervisors.

This owner imports no mesh loader, renderer or subprocess orchestrator.
"""

from pathlib import Path

from app.core.native_settings import settings
from app.runtime.native_admission import Resources


def memory_limit_bytes() -> int | None:
    """See :func:`app.core.host_memory.memory_limit_bytes`."""
    from app.core.host_memory import memory_limit_bytes as detected

    return detected()


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
    except OSError, ValueError, IndexError:
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
        except OSError, ValueError:
            # A process can exit between the two reads.
            continue
    return total if found else None


def native_memory_budget_bytes() -> int:
    from app.runtime.native_runtime import current_permit

    permit = current_permit()
    if permit is not None:
        return permit.resources.bytes
    return native_capacity().bytes
