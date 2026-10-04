"""Mesh admission, resource budgets and reclamation shared by native consumers.

This owner holds the one mutable admission gate and cached detected ceiling.
It does not parse geometry or select thumbnail strategies.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Optional

from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.media import native_process
from app.modules.media.native_execution import admission
from app.runtime.native_admission import NativePermit, Resources
from app.runtime.native_runtime import current_permit

logger = get_logger(__name__)
_LIBC: ctypes.CDLL | Literal[False] | None = None


def reclaim_memory() -> None:
    """Force Python + the allocator to give a just-freed mesh back to the OS.

    Loading and rasterising a mesh churns hundreds of MB of NumPy/trimesh arrays.
    Dropping the references frees them on the Python heap, but glibc keeps the
    emptied arenas mapped, so across a long library scan RSS only ever climbs and
    never recedes — which presents exactly as a memory leak (#29). A
    ``gc.collect()`` breaks any reference cycles the mesh held, and
    ``malloc_trim(0)`` returns the freed arenas to the kernel so the high-water
    mark resets between files. Best-effort: a no-op where malloc_trim is absent.
    """
    gc.collect()
    global _LIBC
    try:
        if _LIBC is None:
            libc_name = ctypes.util.find_library("c")
            _LIBC = ctypes.CDLL(libc_name) if libc_name else False
        if _LIBC and hasattr(_LIBC, "malloc_trim"):
            _LIBC.malloc_trim(0)
    except OSError, AttributeError:  # pragma: no cover - platform dependent
        _LIBC = False


def render_jobs_limit() -> int:
    return native_process.native_capacity().slots


@contextmanager
def render_admission() -> Iterator[NativePermit]:
    """Reuse admitted credits; direct work conservatively reserves the pool."""
    capacity = native_process.native_capacity()
    active = current_permit()
    amount = active.resources if active is not None else Resources(1, capacity.bytes)
    with admission(amount, capacity, checkpoint=checkpoint) as permit:
        yield permit


def canonical_suffix(path: Path, file_type: str | None = None) -> str:
    """Return the source suffix even when *path* is an FD-backed alias.

    External-library scans deliberately read through ``/proc/self/fd`` so a
    mount replacement cannot change the bytes being processed.  Those aliases
    have no filename suffix, so callers that know the catalogued type pass it
    explicitly here.
    """
    if file_type is None:
        return path.suffix.lower()
    suffix = str(file_type).lower()
    return suffix if suffix.startswith(".") else f".{suffix}"


def estimate_triangle_count(
    path: Path, *, file_type: str | None = None
) -> Optional[int]:
    """Best-effort triangle count *without* loading the mesh into memory.

    Loading is itself the memory blow-up (trimesh.load_mesh of a 5M-triangle mesh
    peaks at ~3.5 GB), so the only way to keep a dense lattice/gyroid model from
    OOM-killing the process is to estimate before we load and bail out (#24).

    Exact for binary STL (the triangle count is a uint32 in the header) and for
    PLY (the face count is declared in the ASCII header); a face-directive count
    for OBJ; a size-based estimate for ASCII STL. 3MF stays unknown because its
    bounded source reader admits reachable and placed geometry separately.
    For an STL that fails the exact binary size check we distinguish ASCII from a
    binary file with trailing bytes and pick the *conservative* density, so we
    never underestimate a binary mesh into an unsafe load. Returns None for
    formats we can't cheaply size up (incl. STEP, which trimesh can't mesh
    without optional CAD deps anyway) — the caller then relies on the post-load
    cap, which still skips the render.
    """
    suffix = canonical_suffix(path, file_type)
    try:
        if suffix == ".stl":
            size = path.stat().st_size
            with path.open("rb") as fh:
                sample = fh.read(1024)
            if len(sample) >= 84:
                count = struct.unpack("<I", sample[80:84])[0]
                # Binary STL is exactly 84 + 50 bytes per triangle; if the math
                # checks out we trust the header count exactly.
                if size == 84 + count * 50:
                    return count
            # The exact binary check failed. Now disambiguate a true ASCII STL
            # from a binary STL with trailing bytes (which also fails the check).
            # Guessing wrong toward ASCII is dangerous: ASCII is ~250 B/triangle
            # but binary is only ~50 B/triangle, so an ASCII estimate of a binary
            # file underestimates 5x and can let an over-cap mesh slip through to
            # the exact OOM load #24 set out to prevent. An ASCII STL starts with
            # the text "solid" and contains no NUL bytes; binary headers do.
            looks_ascii = (
                sample[:6].lower().startswith(b"solid") and b"\x00" not in sample
            )
            if looks_ascii:
                # ASCII STL: ~7 lines / ~250 bytes per triangle.
                return size // 250
            # Binary STL body is exactly 50 bytes per facet after the 84-byte
            # header; this stays a safe upper bound even with trailing bytes.
            return max(size - 84, 0) // 50
        if suffix == ".ply":
            # The PLY header is ASCII even when the body is binary, and it
            # declares the face count up front ("element face N"), so we can size
            # the mesh without parsing the (possibly huge) body.
            with path.open("rb") as fh:
                for _ in range(256):  # headers are short; bound the scan
                    line = fh.readline()
                    if not line:
                        break
                    parts = line.split()
                    if (
                        len(parts) >= 3
                        and parts[0].lower() == b"element"
                        and parts[1].lower() == b"face"
                    ):
                        try:
                            return int(parts[2])
                        except ValueError:
                            return None
                    if parts and parts[0].lower() == b"end_header":
                        break
            return None
        if suffix == ".obj":
            # OBJ is plain text; each "f " line is one face. trimesh triangulates
            # an n-gon face into (n - 2) triangles, so summing that keeps the
            # estimate a conservative upper bound (tris/quads dominate real files,
            # where it's already exact). A full text scan is cheap — no float
            # parsing, no mesh build — versus the trimesh.load_mesh it guards against.
            faces = 0
            with path.open("rb") as fh:
                for line in fh:
                    if not line.startswith(b"f ") and not line.startswith(b"f\t"):
                        continue
                    # vertex refs on the line, minus 2 = triangles after fan
                    # triangulation; clamp at 1 so a malformed face never
                    # subtracts from the count.
                    verts = len(line.split()) - 1
                    faces += max(verts - 2, 1)
            return faces or None
        if suffix == ".3mf":
            # XML byte counts include unreachable objects and cannot represent
            # placement multiplicity. The bounded source reader owns admission.
            return None
    except OSError, struct.error:
        return None
    return None


def detect_memory_limit_bytes() -> int | None:
    return native_process.memory_limit_bytes()


def ram_triangle_cap(suffix: str) -> Optional[int]:
    """Use the admitted allowance without dividing large jobs by slot count."""
    if settings.mesh_memory_budget_fraction <= 0:
        return None
    from app.modules.media.native_budget import face_capacity

    permit = current_permit()
    memory = (
        permit.resources.bytes
        if permit is not None
        else native_process.native_capacity().bytes
    )
    return face_capacity(memory, suffix)


def load_face_budget(suffix: str) -> int:
    """Faces a loader may admit for *suffix*: the static, analysis and RAM ceilings.

    The single answer for every path that parses a mesh into memory, so a caller
    cannot pick a looser bound than the one the thumbnail engine enforces.
    """
    budget = min(int(settings.mesh_max_render_triangles), MAX_ANALYSIS_FACES)
    ram_cap = ram_triangle_cap(suffix)
    return budget if ram_cap is None else min(budget, ram_cap)


def exceeds_cap(path: Path, *, file_type: str | None = None) -> bool:
    """True when *path* is too expensive to hand to trimesh (#24, #29).

    Centralises the "bail out before loading" guard so every entry point
    (analyze/geometry/thumbnail/export) skips the same monster meshes and logs
    consistently. Two independent ceilings, because each covers the other's blind
    spot:

    * A raw on-disk **size** cap (``mesh_max_load_mb``). Format-blind, so it
      catches source files independently of their estimated geometry. 3MF then
      enters its bounded reader, whose reachable/placed counts own allocation.
    * The **triangle** estimate vs. ``mesh_max_render_triangles`` (#24), which
      catches a dense lattice/gyroid that is small on disk but explodes on load.

    Returns True if either ceiling is exceeded; the file is still indexed and a
    3MF still falls back to its embedded preview.
    """
    size_cap_mb = settings.mesh_max_load_mb
    size_known = False
    if size_cap_mb > 0:
        try:
            size_mb = path.stat().st_size / (1024 * 1024)
            size_known = True
        except OSError:
            size_mb = 0.0
        if size_mb > size_cap_mb:
            logger.warning(
                "mesh_processing: %s is %.0f MB (> cap %d MB); skipping mesh load "
                "to avoid OOM",
                path.name,
                size_mb,
                size_cap_mb,
            )
            return True

    suffix = canonical_suffix(path, file_type)
    if suffix == ".3mf":
        # This format always enters its bounded ZIP/XML scene reader, even when
        # the independent source-byte ceiling is disabled. No estimate can
        # certify arrays or build multiplicity before reachability is resolved.
        return False
    if file_type is None:
        estimate = estimate_triangle_count(path)
    else:
        estimate = estimate_triangle_count(path, file_type=suffix)
    if estimate is None:
        # An unknown estimate may use the full loader only when a successful
        # stat has already proved that the source is inside the byte budget.
        # A disabled byte cap or unreadable stat is not permission for an
        # unbounded allocation; STL can continue through the isolated streamer.
        return not size_known
    # Effective cap = the smaller of the static ceiling and the RAM-derived cap,
    # so a small host auto-skips meshes a large host would render (#29).
    cap = settings.mesh_max_render_triangles
    ram_cap = ram_triangle_cap(suffix)
    if ram_cap is not None and ram_cap < cap:
        cap = ram_cap
        limiter = "RAM budget"
    else:
        limiter = "static cap"
    if estimate > cap:
        logger.warning(
            "mesh_processing: %s is ~%d triangles (> %s %d); skipping mesh load "
            "to avoid OOM",
            path.name,
            estimate,
            limiter,
            cap,
        )
        return True
    return False


def process_rss_bytes(pid: int) -> int | None:
    return native_process.process_rss_bytes(pid)


def step_memory_budget_bytes() -> int | None:
    return native_process.native_memory_budget_bytes()


def process_tree_rss_bytes(pid: int) -> int | None:
    return native_process.process_tree_rss_bytes(pid)


def native_memory_budget_bytes() -> int:
    return native_process.native_memory_budget_bytes()
