"""Software triangle rasteriser used by `mesh_processing.render_thumbnail`.

Pure numpy + Pillow — no GL, no display.

Lighting model (view-space, camera at -Z looking toward +Z):
  - Key light:  upper-left, facing the camera  → bright highlights
  - Fill light: lower-right, softer            → lift shadows
  - Rim light:  grazing angle (edge on)        → silhouette separation
  - Ambient:    constant floor                 → no pure-black faces

This is intentionally Python-only for 0.1 so source installs and Docker builds
do not need a Rust toolchain.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


def render_thumbnail(
    load_mesh, path: Path, width: int = 640, height: int = 480
) -> Optional[bytes]:
    """Render a PNG thumbnail of *path* via an isometric software rasteriser.

    `load_mesh` is injected to avoid a circular import on `mesh_processing`.
    Returns raw PNG bytes, or None on failure.
    """
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except ImportError:
        logger.error("mesh_render: numpy/Pillow unavailable; cannot render thumbnail")
        return None

    mesh = load_mesh(path)
    if (
        mesh is None
        or len(mesh.vertices) == 0
        or mesh.faces is None
        or len(mesh.faces) == 0
    ):
        logger.warning("mesh_render: empty mesh for %s", path.name)
        return None

    try:
        verts = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int64)

        # ------------------------------------------------------------------
        # 1. Centre and normalise the mesh to a unit-ish bounding sphere.
        # ------------------------------------------------------------------
        center = (verts.max(axis=0) + verts.min(axis=0)) * 0.5
        verts = verts - center

        # ------------------------------------------------------------------
        # 2. Isometric-ish view: yaw -45° around Z, pitch +30° around X.
        # ------------------------------------------------------------------
        elev = np.radians(30.0)
        azim = np.radians(-45.0)
        cz, sz = np.cos(azim), np.sin(azim)
        cx, sx = np.cos(elev), np.sin(elev)
        rot_z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
        rot_x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
        rotation = rot_x @ rot_z
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
            (width * (1 - 2 * margin)) / extent_x,
            (height * (1 - 2 * margin)) / extent_y,
        )
        px = (xs - (x_min + x_max) * 0.5) * scale + width * 0.5
        py = height * 0.5 - (ys - (y_min + y_max) * 0.5) * scale  # flip Y
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

        # Back-face cull: after normal-flipping, valid faces had original z<0.
        # Re-derive original-z sign from the raw (pre-flip) cross product.
        raw_normals = np.cross(edge1, edge2)
        front = raw_normals[:, 2] < 0.0
        valid = front & (norm_len.squeeze() > 1e-8)

        tri = tri[valid]
        shade_rgb = shade_rgb[valid]

        if tri.shape[0] == 0:
            logger.warning(
                "mesh_render: no visible triangles for %s — using silhouette", path.name
            )
            tri = screen[faces]
            shade_rgb = np.tile(ambient_color + 0.4, (faces.shape[0], 1))

        # ------------------------------------------------------------------
        # 6. Painter's sort (far-to-near).
        # ------------------------------------------------------------------
        avg_z = tri[:, :, 2].mean(axis=1)
        order = np.argsort(-avg_z)
        tri = tri[order]
        shade_rgb = shade_rgb[order]  # (F, 3) float

        # ------------------------------------------------------------------
        # 7. Rasterise.
        # ------------------------------------------------------------------
        # Model colour: a medium slate blue-grey.
        base_color = np.array([168, 186, 200], dtype=np.float32)

        bg_color = np.array([245, 246, 248], dtype=np.uint8)  # off-white bg
        img = np.broadcast_to(bg_color, (height, width, 3)).copy()
        zbuf = np.full((height, width), np.inf, dtype=np.float32)

        _rasterise_triangles(img, zbuf, tri, shade_rgb, base_color, width, height)

        # ------------------------------------------------------------------
        # 8. Post-process: subtle vignette + very light blur to soften aliasing.
        # ------------------------------------------------------------------
        pil = Image.frombytes("RGB", (width, height), img.tobytes())

        # Mild Gaussian to anti-alias the flat-shaded edges
        pil = pil.filter(ImageFilter.GaussianBlur(radius=0.6))

        # Vignette: darken the corners slightly so the model "pops"
        vx = np.linspace(-1, 1, width, dtype=np.float32)
        vy = np.linspace(-1, 1, height, dtype=np.float32)
        gx, gy = np.meshgrid(vx, vy)
        vignette = 1.0 - 0.18 * np.clip(gx**2 + gy**2, 0, 1)
        vig_arr = np.array(pil, dtype=np.float32) * vignette[:, :, None]
        pil = Image.fromarray(np.clip(vig_arr, 0, 255).astype(np.uint8))

        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    except Exception:
        logger.warning(
            "mesh_render: render_thumbnail failed for %s", path.name, exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# Rasterisation helpers
# ---------------------------------------------------------------------------


def _rasterise_triangles(
    img, zbuf, tri, shade_rgb, base_color, width: int, height: int
) -> None:
    """Z-buffered per-face rasteriser. shade_rgb is (F, 3) in [0, 1]."""
    import numpy as np

    if tri.shape[0] == 0:
        return

    xs = tri[:, :, 0]
    ys = tri[:, :, 1]
    x0 = np.clip(np.floor(xs.min(axis=1)).astype(np.int32), 0, width - 1)
    x1 = np.clip(np.ceil(xs.max(axis=1)).astype(np.int32), 0, width - 1)
    y0 = np.clip(np.floor(ys.min(axis=1)).astype(np.int32), 0, height - 1)
    y1 = np.clip(np.ceil(ys.max(axis=1)).astype(np.int32), 0, height - 1)

    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    denom = (v1[:, 1] - v2[:, 1]) * (v0[:, 0] - v2[:, 0]) + (v2[:, 0] - v1[:, 0]) * (
        v0[:, 1] - v2[:, 1]
    )
    valid = np.abs(denom) > 1e-9

    idx = np.where(valid)[0]
    for i in idx:
        _rasterise_one(
            img,
            zbuf,
            tri[i],
            shade_rgb[i],
            denom[i],
            int(x0[i]),
            int(x1[i]),
            int(y0[i]),
            int(y1[i]),
            base_color,
        )


def _rasterise_one(
    img,
    zbuf,
    tri_i,
    shade_i,
    denom_i,
    xmin,
    xmax,
    ymin,
    ymax,
    base_color,
) -> None:
    """Rasterise one triangle into img/zbuf. shade_i is a (3,) RGB float in [0,1]."""
    import numpy as np

    if xmax < xmin or ymax < ymin:
        return

    v0, v1, v2 = tri_i[0], tri_i[1], tri_i[2]

    xs = np.arange(xmin, xmax + 1) + 0.5
    ys = np.arange(ymin, ymax + 1) + 0.5
    gx, gy = np.meshgrid(xs, ys)

    w0 = ((v1[1] - v2[1]) * (gx - v2[0]) + (v2[0] - v1[0]) * (gy - v2[1])) / denom_i
    w1 = ((v2[1] - v0[1]) * (gx - v2[0]) + (v0[0] - v2[0]) * (gy - v2[1])) / denom_i
    w2 = 1.0 - w0 - w1
    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not inside.any():
        return

    z = w0 * v0[2] + w1 * v1[2] + w2 * v2[2]
    sub_z = zbuf[ymin : ymax + 1, xmin : xmax + 1]
    sub_img = img[ymin : ymax + 1, xmin : xmax + 1]
    write = inside & (z < sub_z)
    if not write.any():
        return

    # shade_i is (3,) RGB multiplier; base_color is (3,) float
    colour = np.clip(base_color * shade_i, 0, 255).astype(np.uint8)
    sub_img[write] = colour
    sub_z[write] = z[write]
