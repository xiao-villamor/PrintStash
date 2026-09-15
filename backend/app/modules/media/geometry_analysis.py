"""Bounded geometry operations under the existing render process budget."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from printstash_core.inference import EmbeddingInput
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from printstash_core.mesh.similarity.components import ExpandedScene
from printstash_core.mesh.similarity.verification import Verification, verify_meshes
from printstash_core.search.point_inputs import PointRecipe
from printstash_core.search.visual_inputs import VisualRecipe

from app.modules.media import mesh_processing, stl_fallback
from app.modules.media.mesh_resources import PreparedMesh, load_3mf, prepare_loaded_mesh


def _load(
    path: Path, file_type: str, *, triangle_cap: int, include_brep: bool = True
) -> PreparedMesh:
    import numpy as np
    import trimesh

    if type(triangle_cap) is not int or not 100 <= triangle_cap <= MAX_ANALYSIS_FACES:
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
            mesh_processing._load_step_mesh_isolated(path, include_brep=include_brep)
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
    triangle_cap: int = MAX_ANALYSIS_FACES,
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
    import trimesh

    if not 32 <= image_size <= 512:
        raise GeometryError("invalid_view_budget")
    with mesh_processing._render_semaphore():
        prepared = _load(path, file_type, triangle_cap=triangle_cap)
        if not prepared.complete:
            raise GeometryError("embedding_requires_complete_geometry")
        vertices, faces = _component(prepared, component_index)
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        return _render_views(mesh, image_size, canonical_frames())


def canonical_frames():
    return (
        ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((-1, 0, 0), (0, 1, 0), (0, 0, -1)),
        ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
        ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
        ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
        ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    )


def _render_views(mesh, image_size, frames):
    from printstash_core.mesh.native_rasterizer import render_views

    try:
        images = render_views(mesh, image_size, image_size, frames)
    except (ValueError, RuntimeError) as exc:
        raise GeometryError("embedding_view_failed") from exc
    return tuple(
        EmbeddingInput("image", rgb=rgb, width=image_size, height=image_size)
        for rgb in images
    )


@dataclass(frozen=True)
class VisualViews:
    thumbnail: EmbeddingInput | None
    views: tuple[EmbeddingInput, ...]


def visual_views(
    path: Path,
    *,
    file_type: str,
    recipe: VisualRecipe | PointRecipe,
    triangle_cap: int = MAX_ANALYSIS_FACES,
) -> VisualViews:
    """One source load and render permit for the thumbnail and canonical views."""
    prepared = None
    try:
        with mesh_processing._render_semaphore():
            # Visual encoding needs triangles only. The parent owns temporary
            # capacity; this isolated renderer never connects to the database.
            prepared = _load(
                path, file_type, triangle_cap=triangle_cap, include_brep=False
            )
            if not prepared.complete:
                raise GeometryError("embedding_requires_complete_geometry")
            if isinstance(recipe, PointRecipe):
                from printstash_core.inference.points import point_input

                points = point_input(
                    prepared.whole_mesh.vertices, prepared.whole_mesh.faces
                )
                return VisualViews(None, (points,))
            from app.modules.media import mesh_render, thumbnail

            encoded = mesh_render.render_mesh_thumbnail(
                prepared.whole_mesh, "", width=640, height=480, output_format="WEBP"
            )
            if encoded is None:
                raise GeometryError("embedding_view_failed")
            preview = thumbnail_input(
                thumbnail.to_webp(encoded, width=640), recipe.image_size
            )
            rendered = (
                _render_views(
                    prepared.whole_mesh, recipe.image_size, canonical_frames()
                )
                if recipe.profile == "multiview"
                else (preview,)
            )
            return VisualViews(preview, rendered)
    finally:
        prepared = None
        mesh_processing._reclaim_memory()


def thumbnail_input(encoded: bytes, size: int) -> EmbeddingInput:
    from PIL import Image
    from printstash_core.inference.images import decode_image

    source = decode_image(encoded, "image/webp")
    # Match the pinned v1 encoder's bicubic resize exactly. Keeping the pipe's
    # frames at native size avoids carrying a large cached image to the worker.
    image = Image.frombytes("RGB", (source.width, source.height), source.rgb)
    image = image.resize((size, size), Image.Resampling.BICUBIC)
    return EmbeddingInput("image", rgb=image.tobytes(), width=size, height=size)
