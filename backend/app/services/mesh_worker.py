"""Runs one mesh load+render in an isolated, memory-capped subprocess.

Why: `mesh_processing._exceeds_cap` estimates a mesh's triangle count *before*
loading it, to skip files too dense to render safely. That estimate is a
best-effort guess — an unfamiliar format, a corrupted header, or a dense
lattice/gyroid can slip past it and still blow the memory budget once trimesh
actually loads it. In-process, that OOM-kills the `uvicorn` process itself and
takes the whole API down mid-scan (the crash this module exists to prevent).

The fix: do the load+render in a `multiprocessing` child with a hard
`RLIMIT_AS` (virtual memory) ceiling set *before* trimesh/numpy touch the
file. Blowing that ceiling raises `MemoryError` inside the child — which is
caught and reported back as a normal "render failed" status — instead of
triggering the kernel OOM-killer against the parent. Worst case the child
hangs or gets killed outright; either way the parent times out / detects the
dead child and moves on to the next file.

Uses the ``spawn`` start method (not ``fork``): the API process is already
running FastAPI's threadpool by the time a scan calls this, and forking a
multi-threaded process is a well-known source of subtle deadlocks in the
child. ``spawn`` re-executes cleanly and never inherits the parent's live
threads or already-imported heavy libraries.
"""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

# Status strings returned to callers alongside (geometry, thumb).
STATUS_OK = "ok"
STATUS_OOM = "oom"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"


def _child_entry(
    path_str: str, width: int, height: int, mem_limit_bytes: int, conn
) -> None:
    """Child process target. Not called directly — only via `run_isolated_analyze`."""
    try:
        if mem_limit_bytes and mem_limit_bytes > 0:
            try:
                import resource

                resource.setrlimit(
                    resource.RLIMIT_AS, (mem_limit_bytes, mem_limit_bytes)
                )
            except (ValueError, OSError):
                # Some container runtimes disallow lowering RLIMIT_AS further
                # than the cgroup already does — best-effort, not fatal.
                pass

        # Imported only after the rlimit is set, so the allocation ceiling
        # covers every byte trimesh/numpy touch for this file.
        from app.services import mesh_processing

        geometry, thumb = mesh_processing.analyze_mesh_in_process(
            Path(path_str), width=width, height=height
        )
        conn.send((STATUS_OK, geometry, thumb))
    except MemoryError:
        conn.send((STATUS_OOM, None, None))
    except Exception as exc:  # noqa: BLE001 - reported, scan continues either way
        logger.warning(
            "mesh_worker: isolated render for %s raised %s: %s",
            path_str,
            type(exc).__name__,
            exc,
        )
        conn.send((STATUS_ERROR, None, None))
    finally:
        conn.close()


def run_isolated_analyze(
    path: Path,
    *,
    width: int,
    height: int,
    mem_limit_bytes: Optional[int],
    timeout_s: float,
) -> Tuple[Optional[Dict[str, Optional[float]]], Optional[bytes], str]:
    """Run `analyze_mesh_in_process` for *path* in a memory-capped subprocess.

    Returns ``(geometry, thumb, status)``. On anything other than
    ``STATUS_OK`` (``oom`` / ``error`` / ``timeout``), geometry and thumb are
    both ``None`` — the caller falls back exactly as it already does for a
    file that failed the pre-load size estimate (index with no thumbnail /
    embedded slicer preview).
    """
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child_entry,
        args=(str(path), width, height, mem_limit_bytes or 0, child_conn),
        daemon=True,
    )
    proc.start()
    child_conn.close()  # only the child should hold the writable end

    status = STATUS_ERROR
    geometry = None
    thumb = None
    try:
        if parent_conn.poll(timeout_s):
            try:
                status, geometry, thumb = parent_conn.recv()
            except EOFError:
                # Child died mid-send (SIGKILL from a hard cgroup limit that's
                # tighter than RLIMIT_AS, e.g. a swap-less container) — same
                # outcome as OOM from the caller's point of view.
                status = STATUS_OOM
        else:
            status = STATUS_TIMEOUT
            logger.warning(
                "mesh_worker: isolated render for %s exceeded %.0fs timeout",
                path.name,
                timeout_s,
            )
    finally:
        parent_conn.close()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
        if proc.is_alive():  # last resort
            proc.kill()
            proc.join()

    if status != STATUS_OK:
        return None, None, status
    return geometry, thumb, status
