"""Disposable STEP tessellation worker used by mesh_processing.

This module has no application state. The parent monitors its RSS and timeout,
and only accepts an exported mesh below the configured triangle ceiling.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    triangle_limit = int(os.environ["PRINTSTASH_STEP_TRIANGLE_LIMIT"])

    if os.environ.get("PRINTSTASH_STEP_BREP") == "1":
        return _write_brep(source, destination, triangle_limit)

    import trimesh

    loaded = trimesh.load_mesh(str(source), process=False)
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
    except Exception:
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
