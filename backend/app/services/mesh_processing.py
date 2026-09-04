"""Mesh loading, geometry extraction, thumbnail rendering, and STL export.

Trimesh is heavy, so it is lazy-imported inside each function that needs it.
Callers pass a `Path` and receive plain dicts / bytes — they never touch a
trimesh object directly.

The software thumbnail rasteriser lives in `mesh_render` and is re-exposed
here as `render_thumbnail` for backwards compatibility. Ingestion uses
`analyze_mesh`, which loads the mesh once for both geometry and thumbnail when
safe; oversized STL files use the isolated streaming renderer instead.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import io
import os
import struct
import subprocess  # nosec B404 - fixed interpreter/module invocation only
import sys
import tempfile
import threading
import time
import warnings
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Literal, Optional, Tuple

from app.core.config import settings
from app.core.logging import get_logger
from app.services import mesh_render as mesh_render
from app.services import stl_fallback as stl_fallback
from app.services import stl_streaming as stl_streaming

logger = get_logger(__name__)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_3MF_THUMBNAIL_BYTES = 32 * 1024 * 1024
_MAX_3MF_THUMBNAIL_CANDIDATES = 64
_MAX_3MF_THUMBNAIL_AGGREGATE_BYTES = 64 * 1024 * 1024
_MAX_3MF_ENTRIES = 4096
_MAX_3MF_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_3MF_COMPRESSION_RATIO = 200
_MAX_3MF_ENTRY_NAME_BYTES = 1024


class FallbackThumbnail(bytes):
    """PNG bytes produced by the bounded STL fallback.

    ``complete`` records whether the fallback consumed a complete, valid source
    representation. Callers may persist the image when it is false, but must not
    treat sampled geometry statistics as exact metadata.
    """

    complete: bool

    def __new__(cls, value: bytes, *, complete: bool = True):
        instance = super().__new__(cls, value)
        instance.complete = complete
        return instance


# Resolved once: the glibc handle used by _reclaim_memory, or False on a libc
# without malloc_trim (musl/Alpine, non-Linux). None means "not looked up yet".
_LIBC: "ctypes.CDLL | bool | None" = None


def _reclaim_memory() -> None:
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
    except (OSError, AttributeError):  # pragma: no cover - platform dependent
        _LIBC = False


# Process-wide gate limiting how many mesh load+render jobs run at once. Cached
# as (limit, semaphore) so a runtime override / test change to max_render_jobs
# rebuilds it; protected by a lock because ingestion calls in from the
# background-task threadpool.
_RENDER_SEMAPHORE: "tuple[int, threading.BoundedSemaphore] | None" = None
_RENDER_SEMAPHORE_LOCK = threading.Lock()


def _render_jobs_limit() -> int:
    """Effective max concurrent render jobs (always >= 1)."""
    try:
        return max(int(settings.max_render_jobs), 1)
    except (TypeError, ValueError):
        return 1


def _render_semaphore() -> "threading.BoundedSemaphore":
    """Concurrency gate for mesh load+render.

    Ingestion runs in FastAPI's background-task threadpool, so a bulk/folder
    upload (#26) can otherwise fire dozens of concurrent renders that each peak
    hundreds of MB and collectively OOM the box (#29). This caps how many run at
    once to ``VAULT_MAX_RENDER_JOBS``; the RAM-aware triangle cap separately
    divides its per-job budget by the same count so each concurrent job stays
    within its share.
    """
    global _RENDER_SEMAPHORE
    limit = _render_jobs_limit()
    with _RENDER_SEMAPHORE_LOCK:
        if _RENDER_SEMAPHORE is None or _RENDER_SEMAPHORE[0] != limit:
            _RENDER_SEMAPHORE = (limit, threading.BoundedSemaphore(limit))
        return _RENDER_SEMAPHORE[1]


def _canonical_suffix(path: Path, file_type: str | None = None) -> str:
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


def _estimate_triangle_count(
    path: Path, *, file_type: str | None = None
) -> Optional[int]:
    """Best-effort triangle count *without* loading the mesh into memory.

    Loading is itself the memory blow-up (trimesh.load_mesh of a 5M-triangle mesh
    peaks at ~3.5 GB), so the only way to keep a dense lattice/gyroid model from
    OOM-killing the process is to estimate before we load and bail out (#24).

    Exact for binary STL (the triangle count is a uint32 in the header) and for
    PLY (the face count is declared in the ASCII header); a face-directive count
    for OBJ; a size-based estimate for ASCII STL and 3MF (uncompressed mesh XML).
    For an STL that fails the exact binary size check we distinguish ASCII from a
    binary file with trailing bytes and pick the *conservative* density, so we
    never underestimate a binary mesh into an unsafe load. Returns None for
    formats we can't cheaply size up (incl. STEP, which trimesh can't mesh
    without optional CAD deps anyway) — the caller then relies on the post-load
    cap, which still skips the render.
    """
    suffix = _canonical_suffix(path, file_type)
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
            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
                xml_bytes = sum(
                    info.file_size
                    for info in infos
                    if info.filename.lower().endswith(".model")
                )
                if not xml_bytes:
                    # Some 3MF variants keep the mesh outside a ".model" part (or
                    # name it unusually). Rather than return None and let the
                    # caller load a possibly-huge archive blind (#29), fall back to
                    # the total uncompressed payload as a conservative upper bound.
                    xml_bytes = sum(info.file_size for info in infos)
            # 3MF mesh XML runs ~70 bytes per <triangle> (verts are shared).
            return xml_bytes // 70 if xml_bytes else None
    except (OSError, zipfile.BadZipFile, struct.error):
        return None
    return None


# Measured peak RSS per triangle for a full load + thumbnail render, rounded up
# for safety margin. 3MF's XML loader plus the crease-aware rasteriser cost far
# more than a raw STL of the same geometry (~4.5x), so it gets its own factor.
_PEAK_BYTES_PER_TRIANGLE: dict[str, int] = {".3mf": 3600}
_DEFAULT_PEAK_BYTES_PER_TRIANGLE = 2200  # stl / ply / obj

# Cached once: the memory ceiling this process can reach before the OOM killer
# fires. False means "looked up, nothing usable"; None means "not looked up yet".
_MEMORY_LIMIT_BYTES: "int | bool | None" = None


def _detect_memory_limit_bytes() -> int | None:
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


def _ram_triangle_cap(suffix: str) -> Optional[int]:
    """RAM-derived triangle ceiling for *suffix*, or None when RAM capping is off.

    Turns the ``mesh_memory_budget_fraction`` of detected memory into a triangle
    count using the format's measured per-triangle peak cost, so the same config
    auto-skips a mesh on a 4 GB box that a 32 GB box renders fine. The budget is
    divided by ``max_render_jobs`` so concurrent renders share the RAM ceiling
    rather than each claiming the whole of it (#29)."""
    fraction = settings.mesh_memory_budget_fraction
    if fraction <= 0:
        return None
    global _MEMORY_LIMIT_BYTES
    if _MEMORY_LIMIT_BYTES is None:
        _MEMORY_LIMIT_BYTES = _detect_memory_limit_bytes() or False
    if not _MEMORY_LIMIT_BYTES:
        return None
    budget = _MEMORY_LIMIT_BYTES * fraction / _render_jobs_limit()
    per_tri = _PEAK_BYTES_PER_TRIANGLE.get(suffix, _DEFAULT_PEAK_BYTES_PER_TRIANGLE)
    return max(int(budget / per_tri), 1)


def _exceeds_cap(path: Path, *, file_type: str | None = None) -> bool:
    """True when *path* is too expensive to hand to trimesh (#24, #29).

    Centralises the "bail out before loading" guard so every entry point
    (analyze/geometry/thumbnail/export) skips the same monster meshes and logs
    consistently. Two independent ceilings, because each covers the other's blind
    spot:

    * A raw on-disk **size** cap (``mesh_max_load_mb``). Format-blind, so it
      catches the files the triangle estimate can't size up — a 3MF whose mesh
      the estimator doesn't sum returns ``None`` below, and the old code then
      loaded the whole archive and OOM-killed the scan inside trimesh (#29).
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

    suffix = _canonical_suffix(path, file_type)
    if file_type is None:
        estimate = _estimate_triangle_count(path)
    else:
        estimate = _estimate_triangle_count(path, file_type=suffix)
    if estimate is None:
        # An unknown estimate may use the full loader only when a successful
        # stat has already proved that the source is inside the byte budget.
        # A disabled byte cap or unreadable stat is not permission for an
        # unbounded allocation; STL can continue through the isolated streamer.
        return not size_known
    # Effective cap = the smaller of the static ceiling and the RAM-derived cap,
    # so a small host auto-skips meshes a large host would render (#29).
    cap = settings.mesh_max_render_triangles
    ram_cap = _ram_triangle_cap(suffix)
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


# Slicer-generated 3MF archives usually embed a pre-rendered preview
# (Metadata/thumbnail.png per spec; plate_*.png from Orca/Bambu).
_3MF_THUMBNAIL_DIRS = ("metadata/", "3d/thumbnails/", "thumbnails/")


def _process_rss_bytes(pid: int) -> int | None:
    """Read one Linux process's resident set; unavailable platforms return None."""

    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _step_memory_budget_bytes() -> int | None:
    limit = _detect_memory_limit_bytes()
    fraction = settings.mesh_memory_budget_fraction
    if limit is None or fraction <= 0:
        return None
    return max(int(limit * fraction / _render_jobs_limit()), 1)


def _load_step_mesh_isolated(path: Path):
    """Tessellate unknown-complexity STEP in a monitored child process (#72)."""

    import trimesh

    with tempfile.TemporaryDirectory(prefix="printstash-step-") as tmp:
        output = Path(tmp) / "mesh.glb"
        env = os.environ.copy()
        static_cap = int(settings.mesh_max_render_triangles)
        ram_cap = _ram_triangle_cap(path.suffix.lower())
        env["PRINTSTASH_STEP_TRIANGLE_LIMIT"] = str(
            min(static_cap, ram_cap) if ram_cap is not None else static_cap
        )
        command = [
            sys.executable,
            "-m",
            "app.services.step_worker",
            str(path),
            str(output),
        ]
        process = subprocess.Popen(  # nosec B603 - argv is fixed; no shell
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=Path(__file__).resolve().parents[2],
        )
        deadline = time.monotonic() + settings.mesh_step_timeout_seconds
        memory_budget = _step_memory_budget_bytes()
        failure = ""
        while process.poll() is None:
            if time.monotonic() >= deadline:
                failure = "timeout"
                process.kill()
                break
            rss = _process_rss_bytes(process.pid)
            if memory_budget is not None and rss is not None and rss > memory_budget:
                failure = "memory budget"
                process.kill()
                break
            time.sleep(0.05)
        _stdout, stderr = process.communicate()
        if failure or process.returncode != 0 or not output.is_file():
            logger.warning(
                "mesh_processing: isolated STEP tessellation failed for %s (%s%s)",
                path.name,
                failure or f"exit {process.returncode}",
                f": {stderr.decode(errors='replace')[-300:]}" if stderr else "",
            )
            return None
        try:
            loaded = trimesh.load_mesh(str(output), process=False)
        except Exception:
            logger.warning(
                "mesh_processing: failed to load isolated STEP result for %s",
                path.name,
                exc_info=True,
            )
            return None
        if isinstance(loaded, trimesh.Trimesh):
            return loaded
        if isinstance(loaded, trimesh.Scene):
            meshes = [
                geometry
                for geometry in loaded.geometry.values()
                if isinstance(geometry, trimesh.Trimesh)
            ]
            return trimesh.util.concatenate(meshes) if meshes else None
        return None


def _load_mesh(path: Path, *, file_type: str | None = None):
    """Return a single `trimesh.Trimesh` for *path*, or None on failure."""
    import trimesh

    suffix = _canonical_suffix(path, file_type)
    if suffix in (".step", ".stp"):
        return _load_step_mesh_isolated(path)

    try:
        # Load the scene rather than asking trimesh for a mesh directly. 3MF
        # projects commonly represent a placed part as a component graph: the
        # mesh lives on one object while the build item and component carry its
        # transforms. ``load_mesh`` has changed how it coerces scenes across
        # trimesh releases, and flattening ``Scene.geometry`` directly drops
        # those instance transforms. Keep the scene until ``dump`` explicitly
        # bakes every graph path into each mesh instance.
        if file_type is None:
            loaded = trimesh.load_scene(str(path), process=False)
        else:
            loaded = trimesh.load_scene(
                str(path), file_type=suffix.lstrip(".") or None, process=False
            )
    except Exception:
        logger.warning(
            "mesh_processing: trimesh.load_scene failed for %s",
            path.name,
            exc_info=True,
        )
        return None

    if isinstance(loaded, trimesh.Scene):
        # ``dump`` applies build and component transforms and retains repeated
        # instances. Looking only at ``loaded.geometry.values()`` would return
        # the source mesh once at its untransformed coordinates.
        try:
            meshes = [
                geometry
                for geometry in loaded.dump()
                if isinstance(geometry, trimesh.Trimesh)
            ]
        except Exception:
            logger.warning(
                "mesh_processing: failed to flatten scene for %s",
                path.name,
                exc_info=True,
            )
            return None
        if not meshes:
            return None
        if len(meshes) == 1:
            return meshes[0]
        try:
            return trimesh.util.concatenate(meshes)
        except Exception:
            logger.warning(
                "mesh_processing: failed to concatenate scene meshes for %s",
                path.name,
                exc_info=True,
            )
            return None

    # Keep this defensive branch for custom trimesh loaders and test doubles
    # that return a mesh directly instead of a Scene.
    if isinstance(loaded, trimesh.Trimesh):
        return loaded

    return None


def _geometry_from_mesh(mesh) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {
        "bbox_x_mm": None,
        "bbox_y_mm": None,
        "bbox_z_mm": None,
        "volume_mm3": None,
        "triangle_count": None,
    }

    if mesh is None:
        return out

    if mesh.vertices.shape[0] > 0:
        extents = mesh.bounds[1] - mesh.bounds[0]
        out["bbox_x_mm"] = round(float(extents[0]), 2)
        out["bbox_y_mm"] = round(float(extents[1]), 2)
        out["bbox_z_mm"] = round(float(extents[2]), 2)

    if mesh.faces is not None and len(mesh.faces) > 0:
        out["triangle_count"] = len(mesh.faces)

    try:
        vol = mesh.volume
        if vol is not None and vol > 0:
            out["volume_mm3"] = round(float(vol), 2)
    except Exception:
        # Non-watertight meshes raise here; volume is best-effort only.
        pass

    return out


def extract_embedded_3mf_thumbnail(
    path: Path, *, validate_image: bool = False, file_type: str | None = None
) -> Optional[bytes]:
    """Return one semantically unambiguous PNG preview from a 3MF, or None.

    3MF files are ZIP archives; slicers store a rendered plate preview next to
    the mesh. Using it skips the software rasteriser entirely and matches what
    the user saw in the slicer. ``validate_image`` additionally decodes the
    candidate with Pillow before it is selected for the early thumbnail path;
    the default remains permissive for callers that only need a bounded raw
    archive read, while persistence still validates through ``thumbnail.to_webp``.
    """
    if _canonical_suffix(path, file_type) != ".3mf":
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_3MF_ENTRIES:
                logger.warning("mesh_processing: 3MF entry limit exceeded")
                return None
            total_uncompressed = 0
            candidates = []
            for info in infos:
                name = info.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                if (
                    len(name.encode("utf-8", errors="replace"))
                    > _MAX_3MF_ENTRY_NAME_BYTES
                    or name.startswith("/")
                    or ".." in parts
                ):
                    logger.warning("mesh_processing: unsafe 3MF member name")
                    return None
                total_uncompressed += info.file_size
                if total_uncompressed > _MAX_3MF_TOTAL_UNCOMPRESSED_BYTES:
                    logger.warning("mesh_processing: 3MF expanded size limit exceeded")
                    return None
                is_thumbnail_candidate = (
                    name.lower().startswith(_3MF_THUMBNAIL_DIRS)
                    and name.lower().endswith(".png")
                    and info.file_size > 0
                )
                if not is_thumbnail_candidate:
                    continue
                # Geometry members can legitimately compress extremely well and
                # are never inflated by this extractor. Their declared expanded
                # size still contributes to the archive-wide budget above, while
                # the ratio guard belongs on the image bytes we actually read.
                if (
                    info.file_size / max(info.compress_size, 1)
                    > _MAX_3MF_COMPRESSION_RATIO
                ):
                    logger.warning(
                        "mesh_processing: 3MF thumbnail compression ratio limit exceeded"
                    )
                    continue
                candidates.append(info)
                if len(candidates) > _MAX_3MF_THUMBNAIL_CANDIDATES:
                    logger.warning(
                        "mesh_processing: embedded 3MF thumbnail candidate limit exceeded",
                        extra={"count": len(candidates)},
                    )
                    return None
            if not candidates:
                return None

            def semantic_rank(info: zipfile.ZipInfo) -> tuple[int, str]:
                name = info.filename.lower().replace("\\", "/")
                basename = PurePosixPath(name).name
                if name == "metadata/thumbnail.png":
                    return (0, name)
                if basename == "thumbnail.png":
                    return (1, name)
                if "plate_1" in basename or "plate_01" in basename:
                    return (2, name)
                return (3, name)

            candidates.sort(key=semantic_rank)
            best_rank = semantic_rank(candidates[0])[0]
            if best_rank == 3 and len(candidates) > 1:
                logger.warning(
                    "mesh_processing: ambiguous embedded 3MF thumbnails",
                    extra={"count": len(candidates)},
                )
                return None
            aggregate_bytes = 0
            for candidate in candidates:
                if candidate.file_size > _MAX_3MF_THUMBNAIL_BYTES:
                    logger.warning(
                        "mesh_processing: embedded 3MF thumbnail exceeds limit",
                        extra={
                            "entry": candidate.filename,
                            "size": candidate.file_size,
                        },
                    )
                    continue
                remaining = _MAX_3MF_THUMBNAIL_AGGREGATE_BYTES - aggregate_bytes
                if candidate.file_size > remaining:
                    logger.warning(
                        "mesh_processing: embedded 3MF thumbnail aggregate limit reached",
                        extra={"entry": candidate.filename},
                    )
                    continue
                try:
                    with zf.open(candidate) as source:
                        data = source.read(candidate.file_size + 1)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    logger.warning(
                        "mesh_processing: embedded 3MF thumbnail candidate is unreadable",
                        extra={"entry": candidate.filename},
                    )
                    continue
                aggregate_bytes += len(data)
                if len(data) != candidate.file_size or not data.startswith(_PNG_MAGIC):
                    continue
                if len(data) >= 24 and data[12:16] == b"IHDR":
                    try:
                        png_width, png_height = struct.unpack(">II", data[16:24])
                    except struct.error:
                        continue
                    if png_width * png_height > 25_000_000:
                        continue
                elif validate_image:
                    continue
                if validate_image:
                    try:
                        from PIL import Image

                        with warnings.catch_warnings():
                            warnings.simplefilter(
                                "error", Image.DecompressionBombWarning
                            )
                            with Image.open(io.BytesIO(data)) as preview:
                                if preview.format != "PNG":
                                    continue
                                preview.load()
                    except Exception:  # noqa: BLE001 - hostile image input
                        logger.warning(
                            "mesh_processing: embedded 3MF thumbnail is invalid",
                            extra={"entry": candidate.filename},
                        )
                        continue
                logger.info(
                    "mesh_processing: using embedded 3MF thumbnail %s (%d bytes)",
                    candidate.filename,
                    len(data),
                )
                return data
    except (zipfile.BadZipFile, OSError, KeyError):
        logger.warning(
            "mesh_processing: embedded 3MF thumbnail read failed for %s",
            path.name,
            exc_info=True,
        )
    return None


def analyze_mesh(
    path: Path,
    *,
    width: int | None = None,
    height: int | None = None,
    report: Callable[[str], None] | None = None,
    file_type: str | None = None,
    output_format: Literal["PNG", "WEBP"] = "PNG",
) -> Tuple[Dict[str, Optional[float]], Optional[bytes]]:
    """Extract geometry and render a thumbnail with a single mesh load.

    Returns ``(geometry_dict, png_bytes_or_None)``. *report* receives progress
    labels as the stages run (see ingestion progress hints).
    """

    from app.services.thumbnail_engine import ThumbnailEngine, ThumbnailRequest

    result = ThumbnailEngine().generate(
        ThumbnailRequest(
            path=path,
            file_type=file_type,
            width=width,
            height=height,
            include_geometry=True,
            reason="ingestion",
            report=report,
            output_format=output_format,
        )
    )
    thumb = result.image
    if thumb is not None and result.strategy.value in ("streaming", "fallback"):
        thumb = FallbackThumbnail(thumb, complete=result.complete)
    return result.geometry, thumb


def extract_geometry(path: Path) -> Dict[str, Optional[float]]:
    """Extract bounding box, volume, and triangle count from a mesh file.

    The returned dict is shaped for direct use as **kwargs to the
    `Metadata` SQLModel constructor. Missing values are returned as None.
    """
    if _exceeds_cap(path):
        return _geometry_from_mesh(None)
    with _render_semaphore():
        mesh = _load_mesh(path)
        try:
            return _geometry_from_mesh(mesh)
        finally:
            if mesh is not None:
                del mesh
                _reclaim_memory()


def render_thumbnail(
    path: Path, width: int | None = None, height: int | None = None
) -> Optional[bytes]:
    """Render a PNG thumbnail of *path*. Returns PNG bytes or None on failure."""
    from app.services.thumbnail_engine import ThumbnailEngine, ThumbnailRequest

    result = ThumbnailEngine().generate(
        ThumbnailRequest(
            path=path,
            width=width,
            height=height,
            include_geometry=False,
            reason="repair",
        )
    )
    if result.image is None:
        return None
    if result.strategy.value in ("streaming", "fallback"):
        return FallbackThumbnail(result.image, complete=result.complete)
    return result.image


def to_stl_bytes(path: Path) -> Optional[bytes]:
    """Convert any supported mesh file to binary STL bytes.

    If *path* is already an STL, its raw bytes are returned untouched.
    Returns None on conversion failure.
    """
    if path.suffix.lower() == ".stl":
        try:
            return path.read_bytes()
        except OSError:
            return None

    # Converting means a full trimesh.load_mesh + export; an over-cap mesh would OOM
    # the process and take every request down with it (#24). Refuse it cleanly —
    # the caller surfaces a 500 instead, which is far better than a crash-loop.
    if _exceeds_cap(path):
        return None

    with _render_semaphore():
        mesh = _load_mesh(path)
        if mesh is None:
            return None

        try:
            out = io.BytesIO()
            mesh.export(out, file_type="stl")
            return out.getvalue()
        except Exception:
            logger.warning(
                "mesh_processing: STL export failed for %s", path.name, exc_info=True
            )
            return None
        finally:
            del mesh
            _reclaim_memory()
