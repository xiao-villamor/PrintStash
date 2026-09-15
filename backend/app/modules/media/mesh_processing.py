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
import weakref
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from typing import Dict, Optional

from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app import __file__ as application_file
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.media.mesh_limits import (
    _DEFAULT_PEAK_BYTES_PER_TRIANGLE,
    _PEAK_BYTES_PER_TRIANGLE,
    _canonical_suffix,
    _detect_memory_limit_bytes,
    _estimate_triangle_count,
)

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


def _reclaim_memory(
    *, released_mesh: weakref.ReferenceType[object] | None = None
) -> None:
    """Force Python + the allocator to give a just-freed mesh back to the OS.

    Loading and rasterising a mesh churns hundreds of MB of NumPy/trimesh arrays.
    Dropping the references frees them on the Python heap, but glibc keeps the
    emptied arenas mapped, so across a long library scan RSS only ever climbs and
    never recedes — which presents exactly as a memory leak (#29). A
    ``gc.collect()`` breaks any reference cycles the mesh held, and
    ``malloc_trim(0)`` returns the freed arenas to the kernel so the high-water
    mark resets between files. Best-effort: a no-op where malloc_trim is absent.
    """
    if released_mesh is None:
        gc.collect()
    else:
        # Collect preview mesh cycles in the young generations first, then
        # verify that the mesh actually died. A live weakref
        # requires a full collection, including objects promoted while rendering.
        gc.collect(1)
        if released_mesh() is not None:
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


def _legacy_render_semaphore() -> "threading.BoundedSemaphore":
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


@contextmanager
def _render_semaphore() -> Iterator[None]:
    from app.modules.media import render_budget

    if render_budget.RENDER_ADMITTED.get() or render_budget.ADAPTIVE_RENDER.get():
        yield
        return
    with _legacy_render_semaphore():
        capacity = render_budget.memory_budget()
        limit = _render_jobs_limit()
        reservation = render_budget.budget.acquire(
            max(1, capacity // limit),
            capacity=capacity,
            jobs=max(limit, render_budget.import_workers()),
        )
        assert reservation is not None
        token = render_budget.RENDER_ADMITTED.set(True)
        try:
            yield
        finally:
            render_budget.RENDER_ADMITTED.reset(token)
            reservation.release()


# Cached once: the memory ceiling this process can reach before the OOM killer
# fires. False means "looked up, nothing usable"; None means "not looked up yet".
_MEMORY_LIMIT_BYTES: "int | bool | None" = None


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
    from app.modules.media.render_budget import ADAPTIVE_RENDER

    divisor = 1 if ADAPTIVE_RENDER.get() else _render_jobs_limit()
    budget = _MEMORY_LIMIT_BYTES * fraction / divisor
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


def _load_step_mesh_isolated(path: Path, *, include_brep: bool = False):
    """Tessellate unknown-complexity STEP in a monitored child process (#72)."""

    import trimesh

    static_cap = int(settings.mesh_max_render_triangles)
    if include_brep:
        static_cap = min(static_cap, MAX_ANALYSIS_FACES)
    ram_cap = _ram_triangle_cap(path.suffix.lower())
    triangle_limit = min(static_cap, ram_cap) if ram_cap is not None else static_cap
    # Worst case: three float64 vertices and three int64 indices per face.
    # Include NPZ headers and the bounded B-rep sidecar in the capacity lease.
    result_limit = max(triangle_limit, 1) * 96 + 1024 * 1024
    with ExitStack() as resources:
        tmp = resources.enter_context(
            tempfile.TemporaryDirectory(prefix="printstash-step-")
        )
        if include_brep:
            import secrets

            from app.db.session import get_session_factory
            from app.modules.storage.capacity import CapacityManager, CapacityResource

            reservation = CapacityManager(get_session_factory()).reserve(
                "step-tessellation:" + secrets.token_hex(12),
                [
                    CapacityResource.for_path(
                        Path(tmp), result_limit + 64 * 1024, role="STEP tessellation"
                    )
                ],
            )
            resources.callback(reservation.release)
        output = Path(tmp) / ("mesh.npz" if include_brep else "mesh.glb")
        env = os.environ.copy()
        env["PRINTSTASH_STEP_BREP"] = "1" if include_brep else "0"
        env["PRINTSTASH_STEP_TRIANGLE_LIMIT"] = str(triangle_limit)
        command = [
            sys.executable,
            "-m",
            "app.modules.media.step_worker",
            str(path),
            str(output),
        ]
        process = subprocess.Popen(  # nosec B603 - argv is fixed; no shell
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=Path(application_file).resolve().parent.parent,
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
            if include_brep:
                from printstash_core.mesh.similarity import GeometryError

                raise GeometryError(
                    "tessellation_timeout"
                    if failure == "timeout"
                    else "worker_oom"
                    if failure == "memory budget" or process.returncode == -9
                    else "step_unavailable"
                    if process.returncode == 7
                    else "geometry_work_limit"
                    if process.returncode == 3
                    else "invalid_step"
                )
            logger.warning(
                "mesh_processing: isolated STEP tessellation failed for %s (%s%s)",
                path.name,
                failure or f"exit {process.returncode}",
                f": {stderr.decode(errors='replace')[-300:]}" if stderr else "",
            )
            return None
        if include_brep:
            import json

            import numpy as np
            from printstash_core.mesh.similarity import GeometryError

            if (
                output.stat().st_size > result_limit
                or output.with_suffix(".json").stat().st_size > 64 * 1024
            ):
                raise GeometryError("geometry_work_limit")
            with np.load(output, allow_pickle=False) as arrays:
                loaded = trimesh.Trimesh(
                    vertices=arrays["vertices"], faces=arrays["faces"], process=False
                )
            loaded.metadata["brep"] = json.loads(
                output.with_suffix(".json").read_text()
            )
            return loaded
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
                for geometry in loaded.dump()
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
        loaded = None
        if suffix == ".3mf":
            from printstash_core.mesh.threemf import load_scene

            loaded = load_scene(path)
        elif suffix == ".stl":
            from printstash_core.mesh.stl import load_binary_stl

            loaded = load_binary_stl(path)
        # Load the scene rather than asking trimesh for a mesh directly. 3MF
        # projects commonly represent a placed part as a component graph: the
        # mesh lives on one object while the build item and component carry its
        # transforms. ``load_mesh`` has changed how it coerces scenes across
        # trimesh releases, and flattening ``Scene.geometry`` directly drops
        # those instance transforms. Keep the scene until ``dump`` explicitly
        # bakes every graph path into each mesh instance.
        if loaded is None:
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
    from printstash_core.mesh.native_geometry import measure_mesh

    if mesh is None:
        return dict.fromkeys(
            ("bbox_x_mm", "bbox_y_mm", "bbox_z_mm", "volume_mm3", "triangle_count")
        )
    return measure_mesh(mesh)


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


def to_stl_bytes(path: Path, *, file_type: str | None = None) -> Optional[bytes]:
    """Convert any supported mesh file to binary STL bytes.

    If *path* is already an STL, its raw bytes are returned untouched.
    Returns None on conversion failure.
    """
    if _canonical_suffix(path, file_type) == ".stl":
        try:
            return path.read_bytes()
        except OSError:
            return None

    # Converting means a full trimesh.load_mesh + export; an over-cap mesh would OOM
    # the process and take every request down with it (#24). Refuse it cleanly —
    # the caller surfaces a 500 instead, which is far better than a crash-loop.
    over_cap = (
        _exceeds_cap(path)
        if file_type is None
        else _exceeds_cap(path, file_type=file_type)
    )
    if over_cap:
        return None

    with _render_semaphore():
        mesh = (
            _load_mesh(path)
            if file_type is None
            else _load_mesh(path, file_type=file_type)
        )
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
