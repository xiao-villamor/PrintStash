"""Consistent source-relative oblique cameras; one prepared mesh per batch."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE
from printstash_core.mesh.rasterizer import render_prepared_thumbnail
from printstash_core.mesh.render_geometry import (
    PreparedRenderGeometry,
    prepare_mesh,
    prepare_normals,
)
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.components import ExpandedScene

from app.core.cancellation import checkpoint
from app.modules.media import thumbnail
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.preview_contracts import (
    HEIGHT,
    MAX_IMAGE_BYTES,
    WIDTH,
    CameraView,
    Matrix,
    PreviewLimitError,
    PreviewManifest,
    PublishedView,
    ViewId,
    ViewProfile,
    ViewRole,
)

if TYPE_CHECKING:
    from trimesh import Trimesh


def _matrix(value) -> Matrix:
    def vector(row):
        return float(row[0]), float(row[1]), float(row[2])

    return vector(value[0]), vector(value[1]), vector(value[2])


def _looking_from(normal) -> Matrix:
    normal = np.asarray(normal, dtype=np.float64)
    normal /= np.linalg.norm(normal)
    # Imported Z remains screen-up unless the camera is looking along it.
    up = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(up, normal)) > 0.95:
        up = np.array([0.0, 1.0, 0.0])
    up -= normal * np.dot(up, normal)
    up /= np.linalg.norm(up)
    return _matrix(np.stack([np.cross(up, normal), up, -normal]))


def _orbit(azimuth: float) -> Matrix:
    angle, tilt = (
        np.radians(azimuth),
        np.radians(-PREVIEW_PROFILE.hero_elevation_degrees),
    )
    ca, sa = np.cos(angle), np.sin(angle)
    ct, st = np.cos(tilt), np.sin(tilt)
    spin = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]])
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
    return _matrix(np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]]) @ base @ spin)


def candidates(
    mesh: Trimesh, *, max_faces: int, include_poles: bool = False
) -> tuple[ViewProfile, tuple[CameraView, ...]]:
    return _cameras(len(mesh.faces), max_faces=max_faces, include_poles=include_poles)


def _cameras(
    face_count: int, *, max_faces: int, include_poles: bool
) -> tuple[ViewProfile, tuple[CameraView, ...]]:
    if not 0 < face_count <= max_faces:
        raise PreviewLimitError
    views: list[CameraView] = []
    for index in range(4):
        azimuth = PREVIEW_PROFILE.hero_azimuth_degrees + index * 90
        views.append(
            CameraView(
                id=ViewId(f"orbit-{index}"),
                role=ViewRole.ORBIT,
                rotation=_orbit(azimuth),
                azimuth_degrees=azimuth,
                elevation_degrees=PREVIEW_PROFILE.hero_elevation_degrees,
            )
        )
    if not include_poles:
        return ViewProfile.ORBIT, tuple(views)
    for identifier, role, normal, elevation in (
        ("top", ViewRole.TOP, (0, 0, 1), 90),
        ("bottom", ViewRole.BOTTOM, (0, 0, -1), -90),
    ):
        views.append(
            CameraView(
                id=ViewId(identifier),
                role=role,
                rotation=_looking_from(normal),
                azimuth_degrees=None,
                elevation_degrees=elevation,
            )
        )
    return ViewProfile.ORBIT, tuple(views)


def render_views(
    mesh: Trimesh,
    *,
    source_sha256: str,
    max_faces: int,
    recipe: int = 1,
    include_poles: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[PreviewManifest, list[bytes]]:
    if not 0 < len(mesh.faces) <= max_faces:
        raise PreviewLimitError
    geometry = prepare_mesh(mesh)
    if geometry is None:
        raise ValueError("preview_no_geometry")
    used = np.asarray(mesh.vertices)[np.unique(mesh.faces)]
    return render_prepared_views(
        geometry,
        center=(used.min(axis=0) + used.max(axis=0)) * 0.5,
        source_sha256=source_sha256,
        max_faces=max_faces,
        recipe=recipe,
        include_poles=include_poles,
        progress=progress,
    )


def render_prepared_views(
    geometry: PreparedRenderGeometry,
    *,
    center,
    source_sha256: str,
    max_faces: int,
    recipe: int,
    include_poles: bool,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[PreviewManifest, list[bytes]]:
    """Render the view set from geometry the caller already holds.

    ``geometry`` is centred on its referenced bounding box, as both
    ``prepare_mesh`` and ``prepare_scene`` produce it; ``center`` is that box's
    centre in source coordinates. The cover pass and the standalone batch both
    end here, so one mesh load can serve the cover and every view.
    """
    profile, views = _cameras(
        geometry.face_count, max_faces=max_faces, include_poles=include_poles
    )
    prepared = prepare_normals(geometry)
    images: list[bytes] = []
    published: list[PublishedView] = []
    offset = 0
    for index, view in enumerate(views, start=1):
        checkpoint(force=True)
        encoded = render_prepared_thumbnail(
            prepared,
            view.id,
            WIDTH,
            HEIGHT,
            view_rotation=np.asarray(view.rotation),
            output_format="WEBP",
        )
        if encoded is None:
            raise ValueError("preview_renderer_no_output")
        # Every advertised image crosses the same canonical validation seam.
        try:
            encoded = thumbnail.to_webp(
                encoded,
                width=WIDTH,
                normalize=False,
                preserve_frame=True,
                renderer_encoded=True,
            )
        except thumbnail.EmptyThumbnailError:
            # A pole of a plate standing on edge sees less than a pixel. The
            # orbit still shows the Model, so that one view is left out; an
            # empty orbit view means there is nothing to show at all.
            if view.role is ViewRole.ORBIT:
                raise
            if progress is not None:
                progress(index, len(views))
            continue
        if len(encoded) > MAX_IMAGE_BYTES:
            raise PreviewLimitError
        points = geometry.vertices @ np.asarray(view.rotation).T.astype(np.float32)
        minimum, maximum = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
        extent = np.maximum(maximum - minimum, 1e-6)
        scale = min(
            WIDTH * (1 - 2 * PREVIEW_PROFILE.margin_fraction) / extent[0],
            HEIGHT * (1 - 2 * PREVIEW_PROFILE.margin_fraction) / extent[1],
        )
        published.append(
            PublishedView(
                **view.model_dump(),
                view_center=tuple((minimum + maximum) * 0.5),
                pixels_per_unit=float(scale),
                offset=offset,
                size=len(encoded),
                sha256=hashlib.sha256(encoded).hexdigest(),
            )
        )
        images.append(encoded)
        offset += len(encoded)
        if progress is not None:
            progress(index, len(views))
    return PreviewManifest(
        source_sha256=source_sha256,
        recipe=recipe,
        profile=profile,
        center=tuple(float(value) for value in center),
        views=tuple(published),
        size=offset,
    ), images


def scene_center(scene: ExpandedScene):
    """The placed bounding-box centre ``prepare_scene`` frames a scene around."""
    minimum = np.full(3, np.inf)
    maximum = np.full(3, -np.inf)
    by_id = {resource.resource_id: resource for resource in scene.resources}
    for instance in scene.instances:
        resource = by_id[instance.resource_id]
        source = resource.vertices[np.unique(resource.faces)]
        placed = source @ instance.transform[:3, :3].T + instance.transform[:3, 3]
        minimum = np.minimum(minimum, placed.min(axis=0))
        maximum = np.maximum(maximum, placed.max(axis=0))
    return (minimum + maximum) * 0.5


_RESOURCE_CODES = frozenset(
    {
        "geometry_work_limit",
        "resource_limit",
        "scene_resource_limit",
        "archive_resource_limit",
    }
)


# GeometryError is a ValueError. Anything else is a bug for the supervisor.
RENDER_FAILURES = (PreviewLimitError, ImportError, ValueError)


def failure_reason(exc: Exception) -> ThumbnailFailureReason:
    """The closed reason reported for one of ``RENDER_FAILURES``."""
    if isinstance(exc, PreviewLimitError):
        return ThumbnailFailureReason.RESOURCE_LIMIT
    if isinstance(exc, GeometryError):
        if exc.code in _RESOURCE_CODES:
            return ThumbnailFailureReason.RESOURCE_LIMIT
        if exc.code in {"unsupported_geometry", "step_unavailable"}:
            return ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
        return ThumbnailFailureReason.INVALID_SOURCE
    if isinstance(exc, ImportError):
        return ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
    if isinstance(exc, ValueError):
        return ThumbnailFailureReason.INVALID_SOURCE
    raise TypeError(f"unexpected_preview_failure:{type(exc).__name__}") from exc
