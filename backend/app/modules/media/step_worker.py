"""Disposable STEP tessellation worker used by mesh_loading.

This module has no application state. The parent monitors its RSS and timeout,
and only accepts an exported mesh below the configured triangle ceiling.
"""

from __future__ import annotations

import errno
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    triangle_limit = int(os.environ["PRINTSTASH_STEP_TRIANGLE_LIMIT"])

    return convert(
        source,
        destination,
        triangle_limit,
        include_brep=os.environ.get("PRINTSTASH_STEP_BREP") == "1",
    )


def convert(
    source: Path, destination: Path, triangle_limit: int, *, include_brep: bool
) -> int:
    if include_brep:
        return _write_brep(source, destination, triangle_limit)

    import cascadio
    import trimesh

    # Trimesh's STEP adapter enables OpenCASCADE's parallel pool, ignoring
    # OMP_NUM_THREADS. Thread stacks can exhaust the admitted address space and
    # hang native conversion. Use the same GLB intermediate with serial meshing.
    with tempfile.TemporaryDirectory(prefix="printstash-step-") as temporary:
        converted = Path(temporary) / "converted.glb"
        status = cascadio.step_to_glb(str(source), str(converted), use_parallel=False)
        if status != 0:
            return 4
        loaded = trimesh.load_mesh(str(converted), process=False)
    if isinstance(loaded, trimesh.Scene):
        meshes = [
            geometry
            for geometry in loaded.dump()
            if isinstance(geometry, trimesh.Trimesh)
        ]
        if not meshes:
            return 4
        loaded = trimesh.util.concatenate(meshes)
    if not isinstance(loaded, trimesh.Trimesh):
        return 4
    if len(loaded.faces) > triangle_limit:
        return 3
    loaded.export(destination, file_type="glb")
    return 0


def _write_brep(source: Path, destination: Path, triangle_limit: int) -> int:
    import json

    import numpy as np

    from app.modules.media.step_geometry import StepGeometryError, tessellate

    try:
        vertices, faces, evidence = tessellate(source, triangle_limit=triangle_limit)
        np.savez(destination, vertices=vertices, faces=faces)
        destination.with_suffix(".json").write_text(
            json.dumps(evidence, allow_nan=False)
        )
        return 0
    except ImportError:
        return 7
    except StepGeometryError as exc:
        return 3 if str(exc) == "geometry_work_limit" else 4
    except MemoryError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOMEM:
            raise
        return 4
    except Exception:
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
