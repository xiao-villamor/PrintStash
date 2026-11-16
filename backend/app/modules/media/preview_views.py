"""Conservative, area-weighted camera profiles; one prepared mesh per batch."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE
from printstash_core.mesh.rasterizer import render_prepared_thumbnail
from printstash_core.mesh.render_geometry import prepare_mesh, prepare_normals
from printstash_core.mesh.similarity.geometry import prepare_surface

from app.core.cancellation import checkpoint
from app.modules.media import thumbnail
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
    angle, tilt = np.radians(azimuth), np.radians(-18)
    ca, sa = np.cos(angle), np.sin(angle)
    ct, st = np.cos(tilt), np.sin(tilt)
    spin = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]])
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
    return _matrix(np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]]) @ base @ spin)


def candidates(
    mesh: Trimesh, *, max_faces: int
) -> tuple[ViewProfile, tuple[CameraView, ...]]:
    if not 0 < len(mesh.faces) <= max_faces:
        raise PreviewLimitError
    surface = prepare_surface(mesh.vertices, mesh.faces)
    values = surface.eigenvalues
    # One confidently small principal extent, with two substantial broad axes.
    planar = (
        values[1] > 0 and values[0] / values[1] < 0.04 and values[1] / values[2] >= 0.09
    )
    views: list[CameraView] = []
    if planar:
        normal = surface.frame[:, 0].copy()
        # Eigenvector sign is arbitrary; anchor it in the imported coordinate frame.
        normal *= 1 if normal[np.argmax(np.abs(normal))] >= 0 else -1
        face = np.asarray(_looking_from(normal))
        for identifier, role, direction in (
            ("face", ViewRole.FACE, normal),
            ("reverse", ViewRole.REVERSE, -normal),
            (
                "edge",
                ViewRole.EDGE,
                normal * np.cos(np.radians(65)) + face[0] * np.sin(np.radians(65)),
            ),
        ):
            views.append(
                CameraView(
                    id=ViewId(identifier),
                    role=role,
                    rotation=_looking_from(direction),
                    azimuth_degrees=None,
                    elevation_degrees=None,
                )
            )
    else:
        for index, azimuth in enumerate((-35, 55, 145, 235)):
            views.append(
                CameraView(
                    id=ViewId(f"orbit-{index}"),
                    role=ViewRole.ORBIT,
                    rotation=_orbit(azimuth),
                    azimuth_degrees=azimuth,
                    elevation_degrees=18,
                )
            )
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
    return ViewProfile.PLANAR if planar else ViewProfile.ORBIT, tuple(views)


def render_views(
    mesh: Trimesh,
    *,
    source_sha256: str,
    max_faces: int,
    recipe: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[PreviewManifest, list[bytes]]:
    profile, views = candidates(mesh, max_faces=max_faces)
    geometry = prepare_mesh(mesh)
    if geometry is None:
        raise ValueError("preview_no_geometry")
    prepared = prepare_normals(geometry)
    used = np.asarray(mesh.vertices)[np.unique(mesh.faces)]
    center = (used.min(axis=0) + used.max(axis=0)) * 0.5
    images: list[bytes] = []
    published: list[PublishedView] = []
    offset = 0
    for view in views:
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
        encoded = thumbnail.to_webp(
            encoded,
            width=WIDTH,
            normalize=False,
            preserve_frame=True,
            renderer_encoded=True,
        )
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
            progress(len(images), len(views))
    return PreviewManifest(
        source_sha256=source_sha256,
        recipe=recipe,
        profile=profile,
        center=tuple(center),
        views=tuple(published),
        size=offset,
    ), images
