"""Sample live Linux worker processes without waiting for their exit."""

import os
from pathlib import Path


def sample_processes(observations, pid=None):
    """Sample Linux process-tree RSS/CPU while inference children are still alive."""
    pid = pid or os.getpid()
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        current = observations.setdefault(
            str(pid), {"peak_sampled_rss_bytes": 0, "cpu_seconds": 0.0}
        )
        current["peak_sampled_rss_bytes"] = max(
            current["peak_sampled_rss_bytes"],
            int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
        )
        current["cpu_seconds"] = (int(fields[11]) + int(fields[12])) / os.sysconf(
            "SC_CLK_TCK"
        )
        # Workers are often spawned by an executor thread. Linux keeps each
        # thread's children separately, so inspecting only the leader misses them.
        children = set()
        for path in Path(f"/proc/{pid}/task").glob("*/children"):
            try:
                children.update(int(child) for child in path.read_text().split())
            except (OSError, ValueError):
                continue
        for child in children:
            sample_processes(observations, child)
    except (OSError, ValueError, IndexError):
        pass
