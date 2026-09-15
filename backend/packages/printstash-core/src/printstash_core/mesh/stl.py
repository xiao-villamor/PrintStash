"""Native binary STL loading directly into immutable geometry buffers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .native_rasterizer import kernel


def load_binary_stl(path: Path) -> Any:
    """Return a mesh, or None for the existing loader to handle the input."""
    packed = kernel().load_binary_stl(path)
    if packed is None or not packed[0]:
        return None
    import numpy as np
    import trimesh

    vertices, faces = packed
    mesh: Any = trimesh.Trimesh(
        vertices=np.frombuffer(vertices, dtype=np.float64).reshape(-1, 3),
        faces=np.frombuffer(faces, dtype=np.int64).reshape(-1, 3),
        process=False,
    )
    # This path creates one mesh with its own final buffers, without scene
    # copies. The preview owner can verify its reclamation through a weakref.
    mesh._printstash_owned_buffers = True
    return mesh
