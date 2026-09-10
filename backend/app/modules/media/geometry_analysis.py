"""Bounded geometry operations under the existing render process budget."""

from __future__ import annotations

from pathlib import Path

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.components import ExpandedScene
from printstash_core.mesh.similarity.verification import Verification, verify_meshes

from app.modules.media import mesh_processing, stl_fallback
from app.modules.media.mesh_resources import PreparedMesh, load_3mf, prepare_loaded_mesh


def _load(path: Path, file_type: str, *, triangle_cap: int) -> PreparedMesh:
    import numpy as np
    import trimesh

    if not 100 <= triangle_cap <= 200_000:
        raise GeometryError("invalid_triangle_cap")
    estimate = mesh_processing._estimate_triangle_count(path, file_type=file_type)
    over_cap = (
        mesh_processing._exceeds_cap(path, file_type=file_type)
        or estimate is not None
        and estimate > triangle_cap
    )
    if over_cap:
        if file_type != "stl":
            raise GeometryError("geometry_work_limit")
        sampled = stl_fallback.sample_stl_geometry(
            path, max_triangles=min(10_000, triangle_cap)
        )
        if sampled is None:
            raise GeometryError("invalid_geometry")
        mesh = trimesh.Trimesh(
            vertices=np.asarray(sampled.coordinates, dtype=np.float64).reshape(-1, 3),
            faces=np.arange(sampled.sampled_triangles * 3).reshape(-1, 3),
            process=False,
        )
        return PreparedMesh(
            mesh,
            ExpandedScene((), ()),
            file_type,
            complete=False,
            failure_code="sampled_oversized_source",
        )
    if file_type == "3mf":
        prepared = load_3mf(path)
    else:
        mesh = (
            mesh_processing._load_step_mesh_isolated(path, include_brep=True)
            if file_type == "step"
            else mesh_processing._load_mesh(path, file_type=file_type)
        )
        if mesh is None:
            raise GeometryError(
                "unsupported_geometry" if file_type == "step" else "invalid_geometry"
            )
        prepared = prepare_loaded_mesh(mesh, file_type=file_type)
    if len(prepared.whole_mesh.faces) > triangle_cap:
        raise GeometryError("geometry_work_limit")
    return prepared


def _component(prepared: PreparedMesh, index: int):
    if index == 0:
        return prepared.whole_mesh.vertices, prepared.whole_mesh.faces
    if not prepared.complete or not 1 <= index <= len(prepared.scene.resources):
        raise GeometryError("component_unavailable")
    resource = prepared.scene.resources[index - 1]
    return resource.vertices, resource.faces


def verify_paths(
    first: Path,
    second: Path,
    *,
    first_type: str,
    second_type: str,
    first_component: int = 0,
    second_component: int = 0,
    sample_points: int = 5000,
    triangle_cap: int = 200_000,
) -> Verification:
    """Materialized Artifact paths only; never open storage keys in media."""
    left = right = None
    try:
        with mesh_processing._render_semaphore():
            left = _load(first, first_type, triangle_cap=triangle_cap)
            right = _load(second, second_type, triangle_cap=triangle_cap)
            return verify_meshes(
                *_component(left, first_component),
                *_component(right, second_component),
                sample_points=sample_points,
                partial=not (left.complete and right.complete),
            )
    finally:
        left = right = None
        mesh_processing._reclaim_memory()


def embedding_views(
    path: Path,
    *,
    file_type: str,
    component_index: int,
    image_size: int,
    triangle_cap: int,
):
    """Six opaque RGB views, sharing the mesh loader and interactive render cap."""
    import io

    import numpy as np
    import trimesh
    from PIL import Image
    from printstash_core.inference import EmbeddingInput
    from printstash_core.mesh.rasterizer import render_mesh_thumbnail

    if not 32 <= image_size <= 512:
        raise GeometryError("invalid_view_budget")
    with mesh_processing._render_semaphore():
        prepared = _load(path, file_type, triangle_cap=triangle_cap)
        if not prepared.complete:
            raise GeometryError("embedding_requires_complete_geometry")
        vertices, faces = _component(prepared, component_index)
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        views = []
        frames = (
            ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
            ((-1, 0, 0), (0, 1, 0), (0, 0, -1)),
            ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
            ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
            ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
            ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
        )
        for frame in frames:
            rendered = render_mesh_thumbnail(
                mesh,
                "",
                width=image_size,
                height=image_size,
                view_rotation=np.asarray(frame, dtype=np.float64),
                matte=True,
            )
            if rendered is None:
                raise GeometryError("embedding_view_failed")
            with Image.open(io.BytesIO(rendered)) as image:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                rgb = background.convert("RGB").tobytes()
            views.append(
                EmbeddingInput("image", rgb=rgb, width=image_size, height=image_size)
            )
        return tuple(views)
