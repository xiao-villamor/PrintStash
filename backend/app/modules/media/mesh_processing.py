"""Temporary mesh_processing import facade with a fixed legacy consumer inventory.

No mutable policy, parsers or measurement algorithms live here. Legacy unit
imports remain during migration; production consumers use the destination owners.
The fixed consumer inventory is docs/architecture/mesh-owners.md.
"""

from __future__ import annotations

from pathlib import Path

from app.modules.media import mesh_loading, mesh_measurements, mesh_policy
from app.modules.media.mesh_loading import (
    load_mesh as _load_mesh,
)
from app.modules.media.mesh_loading import (
    load_step_mesh as _load_step_mesh_isolated,
)
from app.modules.media.mesh_loading import (
    to_stl_bytes,
)
from app.modules.media.mesh_measurements import (
    geometry_from_mesh as _geometry_from_mesh,
)
from app.modules.media.mesh_policy import (
    canonical_suffix as _canonical_suffix,
)
from app.modules.media.mesh_policy import (
    detect_memory_limit_bytes as _detect_memory_limit_bytes,
)
from app.modules.media.mesh_policy import (
    estimate_triangle_count as _estimate_triangle_count,
)
from app.modules.media.mesh_policy import (
    exceeds_cap as _exceeds_cap,
)
from app.modules.media.mesh_policy import (
    load_face_budget as _load_face_budget,
)
from app.modules.media.mesh_policy import (
    native_memory_budget_bytes,
    process_rss_bytes,
    process_tree_rss_bytes,
    step_memory_budget_bytes,
)
from app.modules.media.mesh_policy import (
    process_rss_bytes as _process_rss_bytes,
)
from app.modules.media.mesh_policy import (
    ram_triangle_cap as _ram_triangle_cap,
)
from app.modules.media.mesh_policy import (
    reclaim_memory as _reclaim_memory,
)
from app.modules.media.mesh_policy import (
    render_admission as _render_semaphore,
)
from app.modules.media.mesh_policy import (
    render_jobs_limit as _render_jobs_limit,
)
from app.modules.media.mesh_policy import (
    step_memory_budget_bytes as _step_memory_budget_bytes,
)
from app.modules.media.mesh_previews import (
    extract_embedded_3mf_thumbnail,
)


def extract_geometry(path: Path) -> dict[str, float | None]:
    """Legacy scalar projection; new consumers retain MeshMeasurements evidence."""
    if mesh_policy.exceeds_cap(path):
        return mesh_measurements.geometry_from_mesh(None).geometry
    with mesh_policy.render_admission():
        mesh = mesh_loading.load_mesh(path)
        try:
            return mesh_measurements.geometry_from_mesh(mesh).geometry
        finally:
            if mesh is not None:
                mesh = None
                mesh_policy.reclaim_memory()


__all__ = [
    "_canonical_suffix",
    "_detect_memory_limit_bytes",
    "_estimate_triangle_count",
    "_exceeds_cap",
    "_geometry_from_mesh",
    "_load_face_budget",
    "_load_mesh",
    "_load_step_mesh_isolated",
    "_process_rss_bytes",
    "_ram_triangle_cap",
    "_reclaim_memory",
    "_render_jobs_limit",
    "_render_semaphore",
    "_step_memory_budget_bytes",
    "extract_embedded_3mf_thumbnail",
    "extract_geometry",
    "native_memory_budget_bytes",
    "process_rss_bytes",
    "process_tree_rss_bytes",
    "step_memory_budget_bytes",
    "to_stl_bytes",
]
