"""Load physical mesh sources and convert STEP or export STL under mesh policy.

Parsers remain lazy imports; callers obtain a typed materialized mesh or a
refusal. Admission policy belongs to mesh_policy, never a compatibility facade.
"""

from __future__ import annotations

import io
import os
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.media import mesh_policy

if TYPE_CHECKING:
    from trimesh import Trimesh

logger = get_logger(__name__)


def load_step_mesh(
    path: Path, *, include_brep: bool = False, strict_failures: bool = False
) -> Trimesh | None:
    """Tessellate unknown-complexity STEP in a monitored child process (#72)."""

    import trimesh

    static_cap = int(settings.mesh_max_render_triangles)
    if include_brep or strict_failures:
        static_cap = min(static_cap, MAX_ANALYSIS_FACES)
    ram_cap = mesh_policy.ram_triangle_cap(path.suffix.lower())
    triangle_limit = min(static_cap, ram_cap) if ram_cap is not None else static_cap
    # Worst case: three float64 vertices and three int64 indices per face.
    # Include NPZ headers and the bounded B-rep sidecar in the capacity lease.
    result_limit = max(triangle_limit, 1) * 96 + 1024 * 1024
    from app.modules.media.worker_bootstrap import WORKER_MARKER

    isolated = os.environ.get(WORKER_MARKER) == str(os.getpid())
    with ExitStack() as resources:
        permit = resources.enter_context(mesh_policy.render_admission())
        tmp = resources.enter_context(
            tempfile.TemporaryDirectory(prefix="printstash-mesh-")
        )
        if include_brep and not isolated:
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
        if isolated:
            from app.modules.media.step_worker import convert

            returncode = convert(
                path, output, triangle_limit, include_brep=include_brep
            )
            failure = ""
            stderr = b""
        else:
            from app.modules.media.mesh_contracts import ThumbnailFailureReason
            from app.modules.media.mesh_isolation import (
                MeshWorkerError,
                supervise_result,
            )
            from app.modules.media.worker_bootstrap import WorkerLifecycle, command

            failure = ""
            stderr = b""
            try:
                reply = supervise_result(
                    command(
                        "app.modules.media.step_worker",
                        [str(path.absolute()), str(output)],
                        permit.resources.bytes,
                    ),
                    memory_budget=permit.resources.bytes,
                    timeout_seconds=float(settings.mesh_step_timeout_seconds),
                    permit=permit,
                    lifecycle=WorkerLifecycle.GUARDED,
                    accepted_exit_codes=frozenset({0, 3, 4, 7}),
                    temporary_directory=Path(tmp),
                    environment={
                        "PRINTSTASH_STEP_BREP": "1" if include_brep else "0",
                        "PRINTSTASH_STEP_TRIANGLE_LIMIT": str(triangle_limit),
                    },
                )
                returncode = reply.returncode
            except MeshWorkerError as exc:
                failure = (
                    "timeout"
                    if exc.reason is ThumbnailFailureReason.TIMEOUT
                    else "memory budget"
                    if exc.reason is ThumbnailFailureReason.RESOURCE_LIMIT
                    else "worker failed"
                )
                returncode = 4
        if failure or returncode != 0 or not output.is_file():
            if include_brep or strict_failures:
                from printstash_core.mesh.similarity import GeometryError

                raise GeometryError(
                    "tessellation_timeout"
                    if failure == "timeout"
                    else "worker_oom"
                    if failure == "memory budget" or returncode == -9
                    else "step_unavailable"
                    if returncode == 7
                    else "geometry_work_limit"
                    if returncode == 3
                    else "invalid_step"
                )
            logger.warning(
                "mesh_processing: isolated STEP tessellation failed for %s (%s%s)",
                path.name,
                failure or f"exit {returncode}",
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
        except MemoryError:
            raise
        except Exception:
            logger.warning(
                "mesh_processing: failed to load isolated STEP result for %s",
                path.name,
                exc_info=True,
            )
            return None
        # The pinned trimesh.load_mesh delegates to load_scene(...).to_mesh();
        # its result is already a transformed, concatenated Trimesh.
        return cast("Trimesh", loaded)


def _load_3mf_mesh(path: Path, suffix: str) -> Trimesh:
    """One placed `Trimesh` for a 3MF, with typed source refusals preserved.

    trimesh expands repeated build/component placements while it loads, so a few
    KiB of XML can allocate for millions of faces before any post-load check runs
    (#259). The resource loader counts the expanded faces first and bounds the XML
    it parses, so every 3MF entry point shares that guard rather than the
    size-based estimate, which cannot see placements.
    """
    from app.modules.media.mesh_resources import load_3mf

    return load_3mf(path, max_faces=mesh_policy.load_face_budget(suffix)).whole_mesh


def _load_stl_mesh(path: Path) -> Trimesh:
    """Materialize admitted STL facets through the canonical bounded reader."""
    import numpy as np
    import trimesh
    from printstash_core.mesh.similarity import GeometryError

    from app.modules.media.mesh_facts import FingerprintFailureCode
    from app.modules.media.stl_reader import InvalidSTL, STLReadLimits, materialize_stl

    size_limit = (
        min(1 << 30, int(settings.mesh_max_load_mb * 1024 * 1024))
        if settings.mesh_max_load_mb > 0
        else 1 << 30
    )
    limits = STLReadLimits(
        max_triangles=mesh_policy.load_face_budget(".stl"),
        max_source_bytes=size_limit,
    )
    try:
        loaded = materialize_stl(path, limits=limits)
    except InvalidSTL as exc:
        raise GeometryError(exc.reason.value) from exc
    except OSError as exc:
        raise GeometryError(FingerprintFailureCode.SOURCE_UNAVAILABLE.value) from exc
    count, facets = loaded.measurements.triangle_count, loaded.triangles
    return trimesh.Trimesh(
        vertices=facets.reshape(-1, 3),
        faces=np.arange(count * 3, dtype=np.int64).reshape(-1, 3),
        process=False,
    )


def load_mesh(path: Path, *, file_type: str | None = None) -> Trimesh | None:
    """Load a placed mesh; STL/3MF source refusals remain typed `GeometryError`."""
    import trimesh

    suffix = mesh_policy.canonical_suffix(path, file_type)
    if suffix in (".step", ".stp"):
        return load_step_mesh(path)
    if suffix == ".3mf":
        return _load_3mf_mesh(path, suffix)
    if suffix == ".stl":
        return _load_stl_mesh(path)

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
    except MemoryError:
        raise
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
            combined = trimesh.util.concatenate(meshes)
            return combined if isinstance(combined, trimesh.Trimesh) else None
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


def to_stl_bytes(path: Path, *, file_type: str | None = None) -> Optional[bytes]:
    """Convert any supported mesh file to binary STL bytes.

    If *path* is already an STL, its raw bytes are returned untouched.
    Returns None on conversion failure; 3MF source refusals remain typed.
    """
    if mesh_policy.canonical_suffix(path, file_type) == ".stl":
        try:
            return path.read_bytes()
        except OSError:
            return None

    # Converting means a full trimesh.load_mesh + export; an over-cap mesh would OOM
    # the process and take every request down with it (#24). Refuse it cleanly —
    # the caller surfaces a 500 instead, which is far better than a crash-loop.
    over_cap = (
        mesh_policy.exceeds_cap(path)
        if file_type is None
        else mesh_policy.exceeds_cap(path, file_type=file_type)
    )
    if over_cap:
        return None

    with mesh_policy.render_admission():
        mesh = (
            load_mesh(path)
            if file_type is None
            else load_mesh(path, file_type=file_type)
        )
        if mesh is None:
            return None

        try:
            out = io.BytesIO()
            mesh.export(out, file_type="stl")
            return out.getvalue()
        except MemoryError:
            raise
        except Exception:
            logger.warning(
                "mesh_processing: STL export failed for %s", path.name, exc_info=True
            )
            return None
        finally:
            del mesh
            mesh_policy.reclaim_memory()
