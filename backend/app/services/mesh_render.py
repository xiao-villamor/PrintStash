"""Software triangle rasteriser used by `mesh_processing.render_thumbnail`.

Pure numpy + Pillow — no GL, no display.

Lighting model (view-space, camera at -Z looking toward +Z):
  - Key light:  upper-left, facing the camera  → bright highlights
  - Fill light: lower-right, softer            → lift shadows
  - Rim light:  grazing angle (edge on)        → silhouette separation
  - Ambient:    constant floor                 → no pure-black faces

This is intentionally Python-only for 0.1 so source installs and Docker builds
do not need a Rust toolchain. Rasterisation is fully vectorised: candidate
pixels for all triangles are expanded into flat arrays and resolved against
the z-buffer with a single lexsort per chunk, so cost scales with covered
pixel area rather than with Python-level triangle count.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

FLAT_MESH_THICKNESS_RATIO = 0.35

# Render at 2x and downsample with Lanczos for anti-aliasing.
_SUPERSAMPLE = 2

# Cap candidate-pixel expansion per rasteriser chunk (~tens of MB of
# temporaries at this size).
_CHUNK_PIXEL_BUDGET = 2_000_000


def render_thumbnail(
    load_mesh, path: Path, width: int = 640, height: int = 480
) -> Optional[bytes]:
    """Render a PNG thumbnail of *path* via an isometric software rasteriser.

    `load_mesh` is injected to avoid a circular import on `mesh_processing`.
    Returns raw PNG bytes, or None on failure.
    """
    mesh = load_mesh(path)
    return render_mesh_thumbnail(mesh, path.name, width=width, height=height)


def render_mesh_thumbnail(
    mesh, name: str, width: int = 640, height: int = 480
) -> Optional[bytes]:
    """Render a PNG thumbnail from an already-loaded mesh.

    Lets callers that need both geometry and a thumbnail load the mesh once.
    Returns raw PNG bytes, or None on failure.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        logger.error("mesh_render: numpy/Pillow unavailable; cannot render thumbnail")
        return None

    if (
        mesh is None
        or len(mesh.vertices) == 0
        or mesh.faces is None
        or len(mesh.faces) == 0
    ):
        logger.warning("mesh_render: empty mesh for %s", name)
        return None

    try:
        verts = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int64)

        ss_width = width * _SUPERSAMPLE
        ss_height = height * _SUPERSAMPLE

        # ------------------------------------------------------------------
        # 1. Centre and normalise the mesh to a unit-ish bounding sphere.
        # ------------------------------------------------------------------
        center = (verts.max(axis=0) + verts.min(axis=0)) * 0.5
        verts = verts - center

        # ------------------------------------------------------------------
        # 2. Pick a camera view.
        #
        #    Thin display-like prints (badges, signs, character fronts) are
        #    most recognisable from their broad face, matching the web viewer's
        #    first-open camera. Chunkier parts keep the older isometric angle.
        # ------------------------------------------------------------------
        rotation = _select_view_rotation(verts, np)
        view_handedness = float(np.linalg.det(rotation))
        view = verts @ rotation.T  # (N, 3)  — camera is at -Z, looks toward +Z

        # ------------------------------------------------------------------
        # 3. Orthographic projection with 8% margin on each side.
        # ------------------------------------------------------------------
        xs, ys = view[:, 0], view[:, 1]
        x_min, x_max = float(xs.min()), float(xs.max())
        y_min, y_max = float(ys.min()), float(ys.max())
        extent_x = max(x_max - x_min, 1e-6)
        extent_y = max(y_max - y_min, 1e-6)
        margin = 0.10
        scale = min(
            (ss_width * (1 - 2 * margin)) / extent_x,
            (ss_height * (1 - 2 * margin)) / extent_y,
        )
        px = (xs - (x_min + x_max) * 0.5) * scale + ss_width * 0.5
        py = ss_height * 0.5 - (ys - (y_min + y_max) * 0.5) * scale  # flip Y
        pz = view[:, 2]
        screen = np.stack([px, py, pz], axis=1)  # (N, 3)

        # ------------------------------------------------------------------
        # 4. Per-face normals in view-space.
        #    Back-face cull: visible normals have z < 0 (they face the camera
        #    which sits at -Z).
        # ------------------------------------------------------------------
        tri = screen[faces]  # (F, 3, 3) screen-space
        view_tri = view[faces]  # (F, 3, 3) view-space

        edge1 = view_tri[:, 1] - view_tri[:, 0]
        edge2 = view_tri[:, 2] - view_tri[:, 0]
        normals = np.cross(edge1, edge2)  # (F, 3)
        norm_len = np.linalg.norm(normals, axis=1, keepdims=True)
        norm_len = np.where(norm_len == 0, 1.0, norm_len)
        normals = normals / norm_len  # unit normals

        # Flip normals so they consistently point toward the camera (-Z direction).
        # After this, every valid visible normal has z > 0 (pointing at camera).
        normals = np.where(normals[:, 2:3] > 0, normals, -normals)

        # ------------------------------------------------------------------
        # 5. Three-light shading model — all defined in view-space.
        #
        #    View-space axes: X=right, Y=up, Z=toward camera.
        #    All light directions must point FROM the surface TOWARD the light.
        # ------------------------------------------------------------------

        def _normalise(v: "np.ndarray") -> "np.ndarray":
            return v / np.linalg.norm(v)

        # Key light: upper-left, slightly in front of camera
        key_dir = _normalise(np.array([-0.6, 0.8, 0.8]))
        key_color = np.array([1.00, 0.97, 0.92])  # warm white
        key_str = 0.70

        # Fill light: lower-right, softer and cooler
        fill_dir = _normalise(np.array([0.5, -0.4, 0.6]))
        fill_color = np.array([0.60, 0.65, 0.80])  # cool blue-grey
        fill_str = 0.30

        # Rim / back-edge light: grazing from above-right-back
        rim_dir = _normalise(np.array([0.8, 0.6, -0.2]))
        rim_color = np.array([0.90, 0.95, 1.00])  # near-white, slightly cool
        rim_str = 0.20

        # Ambient: constant soft blue-grey floor
        ambient_color = np.array([0.18, 0.20, 0.24])

        key_diff = np.clip(normals @ key_dir, 0.0, 1.0)[:, None]  # (F,1)
        fill_diff = np.clip(normals @ fill_dir, 0.0, 1.0)[:, None]
        rim_diff = np.clip(normals @ rim_dir, 0.0, 1.0)[:, None]

        # Per-face RGB multiplier in [0,1]³
        shade_rgb = (
            ambient_color
            + key_str * key_diff * key_color
            + fill_str * fill_diff * fill_color
            + rim_str * rim_diff * rim_color
        )  # (F, 3)
        shade_rgb = np.clip(shade_rgb, 0.0, 1.0)

        # Back-face cull: in right-handed view-space, visible faces have
        # raw z<0. Front-on flat views preserve screen orientation with a
        # negative-determinant transform, which flips that sign.
        # Re-derive original-z sign from the raw (pre-flip) cross product.
        raw_normals = np.cross(edge1, edge2)
        front = (raw_normals[:, 2] * view_handedness) < 0.0
        valid = front & (norm_len.squeeze() > 1e-8)

        tri = tri[valid]
        shade_rgb = shade_rgb[valid]

        if tri.shape[0] == 0:
            logger.warning(
                "mesh_render: no visible triangles for %s — using silhouette", name
            )
            tri = screen[faces]
            shade_rgb = np.tile(ambient_color + 0.4, (faces.shape[0], 1))

        # ------------------------------------------------------------------
        # 6. Rasterise (z-buffered — no painter's sort needed).
        # ------------------------------------------------------------------
        # Model colour: warm blue-grey, slightly brighter for dark UI cards.
        base_color = np.array([185, 200, 215], dtype=np.float32)

        # Transparent background — floats cleanly on any card colour.
        img = np.zeros((ss_height, ss_width, 3), dtype=np.uint8)
        zbuf = np.full((ss_height, ss_width), np.inf, dtype=np.float64)

        _rasterise_triangles(img, zbuf, tri, shade_rgb, base_color, ss_width, ss_height)

        # Alpha = 255 wherever a triangle was painted, 0 elsewhere.
        alpha = np.where(zbuf < np.inf, np.uint8(255), np.uint8(0)).astype(np.uint8)
        rgba = np.dstack([img, alpha])

        # ------------------------------------------------------------------
        # 7. Post-process: Lanczos downsample (anti-aliasing) + subtle vignette.
        # ------------------------------------------------------------------
        pil = Image.frombytes("RGBA", (ss_width, ss_height), rgba.tobytes())
        pil = pil.resize((width, height), Image.LANCZOS)

        # Vignette: darken the corners slightly so the model "pops"
        vx = np.linspace(-1, 1, width, dtype=np.float32)
        vy = np.linspace(-1, 1, height, dtype=np.float32)
        gx, gy = np.meshgrid(vx, vy)
        vignette = 1.0 - 0.18 * np.clip(gx**2 + gy**2, 0, 1)
        vig_arr = np.array(pil, dtype=np.float32)
        vig_arr[:, :, :3] *= vignette[:, :, None]  # vignette RGB only, keep alpha
        pil = Image.fromarray(np.clip(vig_arr, 0, 255).astype(np.uint8), mode="RGBA")

        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    except Exception:
        logger.warning(
            "mesh_render: render_thumbnail failed for %s", name, exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# View selection helpers
# ---------------------------------------------------------------------------


def _select_view_rotation(verts, np):
    """Return an original-space -> view-space matrix for thumbnail rendering."""

    extents = verts.max(axis=0) - verts.min(axis=0)
    thin_axis = int(np.argmin(extents))
    thin_extent = float(extents[thin_axis])
    broad_extent = float(np.max(np.delete(extents, thin_axis)))

    if broad_extent > 1e-6 and thin_extent / broad_extent <= FLAT_MESH_THICKNESS_RATIO:
        return _front_rotation_for_thin_axis(thin_axis, np)

    elev = np.radians(30.0)
    azim = np.radians(-45.0)
    cz, sz = np.cos(azim), np.sin(azim)
    cx, sx = np.cos(elev), np.sin(elev)
    rot_z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    rot_x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    return rot_x @ rot_z


def _front_rotation_for_thin_axis(thin_axis: int, np):
    """View a flat mesh mostly face-on but tilted 25° to reveal depth in recesses."""

    # 25° tilt rotation around the screen-X axis (tips the model top toward camera).
    tilt = np.radians(25.0)
    ct, st = float(np.cos(tilt)), float(np.sin(tilt))

    if thin_axis == 0:
        # Base: camera from +X (screen X=Y, screen Y=Z).
        base = np.array([[0, 1, 0], [0, 0, 1], [-1, 0, 0]], dtype=np.float64)
    elif thin_axis == 1:
        # Base: camera from +Y (screen X=X, screen Y=Z).
        base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    else:
        # Base: camera from +Z (screen X=X, screen Y=Y, top-down).
        base = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=np.float64)

    # Tilt: rotate 25° around the screen-X axis so the far edge dips toward the
    # viewer, making depth recesses and raised features visible.
    tilt_mat = np.array(
        [[1, 0, 0], [0, ct, -st], [0, st, ct]], dtype=np.float64
    )
    return tilt_mat @ base


# ---------------------------------------------------------------------------
# Rasterisation helpers
# ---------------------------------------------------------------------------


def _rasterise_triangles(
    img, zbuf, tri, shade_rgb, base_color, width: int, height: int
) -> None:
    """Z-buffered rasteriser, vectorised over triangles. shade_rgb is (F, 3) in [0, 1].

    Each triangle's clipped bounding box is expanded into a flat array of
    candidate pixels; barycentric tests, depth interpolation, and per-pixel
    z-buffer resolution all run as whole-array numpy operations.
    """
    import numpy as np

    if tri.shape[0] == 0:
        return

    xs = tri[:, :, 0]
    ys = tri[:, :, 1]
    x0 = np.clip(np.floor(xs.min(axis=1)).astype(np.int64), 0, width - 1)
    x1 = np.clip(np.ceil(xs.max(axis=1)).astype(np.int64), 0, width - 1)
    y0 = np.clip(np.floor(ys.min(axis=1)).astype(np.int64), 0, height - 1)
    y1 = np.clip(np.ceil(ys.max(axis=1)).astype(np.int64), 0, height - 1)

    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    denom = (v1[:, 1] - v2[:, 1]) * (v0[:, 0] - v2[:, 0]) + (v2[:, 0] - v1[:, 0]) * (
        v0[:, 1] - v2[:, 1]
    )
    keep = (np.abs(denom) > 1e-9) & (x1 >= x0) & (y1 >= y0)
    if not keep.any():
        return

    v0, v1, v2 = v0[keep], v1[keep], v2[keep]
    denom = denom[keep]
    x0, x1, y0, y1 = x0[keep], x1[keep], y0[keep], y1[keep]
    colours = np.clip(base_color * shade_rgb[keep], 0, 255).astype(np.uint8)  # (F, 3)

    bbox_w = x1 - x0 + 1
    bbox_h = y1 - y0 + 1
    areas = bbox_w * bbox_h

    flat_img = img.reshape(-1, 3)
    flat_z = zbuf.reshape(-1)

    # Chunk triangles so the candidate-pixel expansion stays within budget.
    cum_areas = np.cumsum(areas)
    start = 0
    n_faces = len(areas)
    consumed = 0
    while start < n_faces:
        end = int(
            np.searchsorted(cum_areas, consumed + _CHUNK_PIXEL_BUDGET, side="right")
        )
        end = max(end, start + 1)
        end = min(end, n_faces)
        consumed = int(cum_areas[end - 1])

        counts = areas[start:end]
        tri_idx = np.repeat(np.arange(start, end), counts)
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
        offsets = np.arange(int(counts.sum())) - np.repeat(starts, counts)

        w_per_tri = bbox_w[tri_idx]
        pix_x = x0[tri_idx] + offsets % w_per_tri
        pix_y = y0[tri_idx] + offsets // w_per_tri

        fx = pix_x + 0.5
        fy = pix_y + 0.5
        a = v0[tri_idx]
        b = v1[tri_idx]
        c = v2[tri_idx]
        d = denom[tri_idx]

        w0 = (
            (b[:, 1] - c[:, 1]) * (fx - c[:, 0]) + (c[:, 0] - b[:, 0]) * (fy - c[:, 1])
        ) / d
        w1 = (
            (c[:, 1] - a[:, 1]) * (fx - c[:, 0]) + (a[:, 0] - c[:, 0]) * (fy - c[:, 1])
        ) / d
        w2 = 1.0 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if inside.any():
            z = w0 * a[:, 2] + w1 * b[:, 2] + w2 * c[:, 2]
            pix = (pix_y * width + pix_x)[inside]
            z = z[inside]
            owner = tri_idx[inside]

            # Nearest candidate per pixel within this chunk: sort by
            # (pixel, z) and keep the first occurrence of each pixel.
            order = np.lexsort((z, pix))
            pix_s = pix[order]
            z_s = z[order]
            owner_s = owner[order]
            first = np.ones(len(pix_s), dtype=bool)
            first[1:] = pix_s[1:] != pix_s[:-1]
            pix_u = pix_s[first]
            z_u = z_s[first]
            owner_u = owner_s[first]

            # Then resolve against the global z-buffer.
            nearer = z_u < flat_z[pix_u]
            target = pix_u[nearer]
            flat_z[target] = z_u[nearer]
            flat_img[target] = colours[owner_u[nearer]]

        start = end
