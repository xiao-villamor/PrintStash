"""Software triangle rasteriser used by `mesh_processing.render_thumbnail`.

Pure numpy + Pillow — no GL, no display.

Lighting model (view-space, camera at -Z looking toward +Z):
  - Key light:  upper-left, facing the camera  → bright highlights
  - Fill light: lower-right, softer            → lift shadows
  - Rim light:  grazing angle (edge on)        → silhouette separation
  - Ambient:    constant floor                 → no pure-black faces

This is intentionally Python-only so source installs and Docker builds do not
need a Rust toolchain. Rasterisation is fully vectorised: candidate
pixels for all triangles are expanded into flat arrays and resolved against
the z-buffer with a single lexsort per chunk, so cost scales with covered
pixel area rather than with Python-level triangle count.

Memory shape: the per-face geometry/shading arrays (``tri``, ``view_tri``,
corner normals, RGB, …) are each O(faces). They are built and freed one
``mesh_render_face_chunk_size`` chunk at a time, so peak render memory is
O(chunk_size) rather than O(total_faces) — a million-triangle mesh no longer
materialises several ~70 MB float32 arrays at once (#29). Only the vertex-scale
arrays (the projected vertices and the welded smooth-normal table) are held whole.

Future architecture (not yet implemented): ``render_mesh_thumbnail`` is a pure
function — it takes an already-loaded mesh and returns PNG bytes, touching no
shared state — so it can be moved wholesale into a separate thumbnail worker
process. The intended split is: the API process accepts the upload; a worker
renders one job at a time under a timeout and the memory-aware cap; on failure or
over-cap it falls back to the embedded preview; and an OOM kills only the worker,
never the API. Keeping this function isolatable is what makes that move a
drop-in later.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

FLAT_MESH_THICKNESS_RATIO = 0.35

# Render at 2x and downsample with Lanczos for anti-aliasing. (3x was trialled:
# it only marginally smoothed residual facet banding on tessellated curves while
# ~doubling render time and the full-frame buffer area, so it wasn't worth the
# cost — the shading fixes, not supersampling, are what removed the artefacts.)
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
        # float32 throughout the per-face geometry/shading pipeline halves the
        # peak RSS of the arrays that scale with triangle count — and the render
        # is ~3/4 of a dense mesh's memory cost (#29). Screen-space thumbnail
        # rendering doesn't need float64 precision; the view-selection and weld
        # quantisation below are unaffected at this scale.
        verts = np.asarray(mesh.vertices, dtype=np.float32)
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
        # Keep the matmul in float32 (rotation is built in float64 for accuracy)
        # so `view` and everything derived from it stays half-width.
        view = verts @ rotation.T.astype(np.float32)  # (N, 3) camera at -Z → +Z

        # ------------------------------------------------------------------
        # 3. Orthographic projection with 8% margin on each side.
        # ------------------------------------------------------------------
        xs, ys = view[:, 0], view[:, 1]
        x_min, x_max = float(xs.min()), float(xs.max())
        y_min, y_max = float(ys.min()), float(ys.max())
        extent_x = max(x_max - x_min, 1e-6)
        extent_y = max(y_max - y_min, 1e-6)
        margin = 0.18
        scale = min(
            (ss_width * (1 - 2 * margin)) / extent_x,
            (ss_height * (1 - 2 * margin)) / extent_y,
        )
        px = (xs - (x_min + x_max) * 0.5) * scale + ss_width * 0.5
        py = ss_height * 0.5 - (ys - (y_min + y_max) * 0.5) * scale  # flip Y
        pz = view[:, 2]
        screen = np.stack([px, py, pz], axis=1)  # (N, 3)

        # ------------------------------------------------------------------
        # 4. Weld coincident vertices once (vertex-scale, O(N) — held whole).
        #     Quantise + pack each position into one integer key so the weld is a
        #     fast 1-D unique; the smoothed per-position normal table is then
        #     accumulated chunk-by-chunk below so we never build a (3F, 3) corner
        #     array for the whole mesh at once.
        # ------------------------------------------------------------------
        extent = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0
        q = np.round(verts / (extent * 1e-5)).astype(np.int64)
        q -= q.min(axis=0)
        span = q.max(axis=0) + 1
        key = (q[:, 0] * span[1] + q[:, 1]) * span[2] + q[:, 2]
        _, pos_id = np.unique(key, return_inverse=True)
        n_pos = int(pos_id.max()) + 1
        del q, key

        rot_T = rotation.T  # original-space -> view-space, applied per chunk
        n_faces = int(faces.shape[0])
        # Per-face arrays below are each O(faces); building them one chunk at a
        # time keeps peak render memory O(chunk_size) rather than O(total_faces).
        chunk = max(int(settings.mesh_render_face_chunk_size), 1)

        # ------------------------------------------------------------------
        # 4b. Crease-aware smooth normals — accumulation pass.
        #     Average each welded position's incident face normals into one
        #     smoothed normal. Smooth normals let shading interpolate across a
        #     triangle (Gouraud, below) instead of flat-shading every facet; the
        #     crease test in the shading pass falls back to the flat face normal
        #     across hard edges so mechanical parts keep crisp corners while
        #     organic models read smooth. bincount accumulation is additive, so
        #     summing it chunk-by-chunk (rather than over a full (3F, 3) array)
        #     gives the same table at O(chunk_size) memory.
        # ------------------------------------------------------------------
        vacc = np.zeros((n_pos, 3), dtype=np.float64)
        for s in range(0, n_faces, chunk):
            fc = faces[s : s + chunk]
            f_obj = verts[fc]  # (c, 3, 3)
            fn = np.cross(f_obj[:, 1] - f_obj[:, 0], f_obj[:, 2] - f_obj[:, 0])
            fn = fn / np.where(
                np.linalg.norm(fn, axis=1, keepdims=True) == 0,
                1.0,
                np.linalg.norm(fn, axis=1, keepdims=True),
            )
            # Angle-weighted normals (Thürmer–Wüthrich): weight each face's
            # contribution to a vertex by the triangle's interior angle there.
            # Plain incident-face averaging over-counts directions that simply
            # have more (or thinner) triangles — which skews the normal at mesh
            # "poles" (many triangles fanning into one vertex) and irregular
            # tessellation, the source of the radial "fan" streaks. Angle weights
            # make the smoothed normal independent of how the surface is cut up.
            e_ab = f_obj[:, [1, 2, 0]] - f_obj  # edge to "next" corner, per corner
            e_ac = f_obj[:, [2, 0, 1]] - f_obj  # edge to "prev" corner, per corner
            e_ab /= np.maximum(np.linalg.norm(e_ab, axis=2, keepdims=True), 1e-20)
            e_ac /= np.maximum(np.linalg.norm(e_ac, axis=2, keepdims=True), 1e-20)
            ang = np.arccos(np.clip(np.sum(e_ab * e_ac, axis=2), -1.0, 1.0))  # (c,3)
            flat_pos = pos_id[fc].ravel()  # (3c,) welded id per face corner
            # Each corner contributes its face normal scaled by that corner angle.
            fn_per_corner = np.repeat(fn, 3, axis=0) * ang.ravel()[:, None]  # (3c,3)
            for a in range(3):
                vacc[:, a] += np.bincount(
                    flat_pos, weights=fn_per_corner[:, a], minlength=n_pos
                )
            del f_obj, fn, flat_pos, fn_per_corner, e_ab, e_ac, ang
        vsm = vacc / np.where(
            np.linalg.norm(vacc, axis=1, keepdims=True) == 0,
            1.0,
            np.linalg.norm(vacc, axis=1, keepdims=True),
        )
        del vacc

        # ------------------------------------------------------------------
        # 5. Three-light shading + specular constants (per corner). Defined once,
        #    applied per chunk in the rasterisation pass below.
        #
        #    View-space axes: X=right, Y=up, Z=toward camera.
        #    Light directions point FROM the surface TOWARD the light. The
        #    rasteriser interpolates the three corner colours across each face.
        # ------------------------------------------------------------------

        def _normalise(v: "np.ndarray") -> "np.ndarray":
            return v / np.linalg.norm(v)

        # Model albedo: the blue-grey surface colour, baked into the light terms
        # below (not the rasteriser's per-pixel multiply, which is now pure white —
        # see `base_color`). Folding albedo into shading lets the specular and rim
        # add *white* highlights on top of the tinted body, so curved surfaces get
        # a bright sheen that reads on the dark card instead of clipping at a dim
        # blue-grey ceiling. Slightly lighter + less saturated than the old base so
        # the model pops against a near-black background.
        albedo = np.array([0.70, 0.75, 0.84])

        # Key light: main illumination, upper-left and well in front of the
        # camera so the lit side reads as one clean gradient. Strength >1 so the
        # directly-lit side drives toward white — the gradient has real range now.
        key_dir = _normalise(np.array([-0.5, 0.65, 1.0]))
        key_color = np.array([1.00, 0.98, 0.95])  # warm white
        key_str = 1.05

        # Fill light: opposite side, soft and cool. Kept gentle so it only lifts
        # the shadow side and never forms a second highlight — competing
        # directional highlights are what made smooth surfaces look muddy/blotchy.
        fill_dir = _normalise(np.array([0.55, -0.25, 0.55]))
        fill_color = np.array([0.55, 0.62, 0.78])  # cool blue-grey
        fill_str = 0.30

        # Rim: a view-based Fresnel edge light (brightens the silhouette where the
        # surface turns away from the camera). Additive white, so it lifts the
        # silhouette off the dark card rather than darkening into it.
        rim_color = np.array([0.85, 0.92, 1.00])  # near-white, slightly cool
        rim_str = 0.22
        rim_power = 3.0

        # Ambient: blue-grey floor tied to the albedo so shadowed faces stay a
        # dark version of the body colour (never crushed to muddy near-black, never
        # a flat grey wash). Lifted enough that curved faces turned away from the
        # key still read as form against the dark card.
        ambient_str = 0.30

        # Blinn-Phong specular off the key light — an additive *white* sheen that
        # picks out ridges, like the 3D viewer. Kept gentle: a strong spec sparkles
        # facet to facet on tessellated curves. Camera is on +Z in view-space, so
        # the half-vector is between key_dir and (0,0,1).
        half = _normalise(key_dir + np.array([0.0, 0.0, 1.0]))
        spec_str = 0.22
        spec_power = 32.0

        def _shade(n: "np.ndarray") -> "np.ndarray":
            # Per-fragment (Phong) shading from a view-space unit normal of any
            # leading shape (..., 3), returning absolute linear colour in [0, 1]
            # (the rasteriser scales by white). Diffuse terms are tinted by the
            # albedo; rim and specular add white on top so highlights brighten the
            # body toward white instead of clipping at the albedo. Evaluated per
            # pixel after the normal is interpolated across the triangle — per-
            # vertex (Gouraud) colour made triangle edges and many-triangle "poles"
            # show as banding and radial fan streaks; per-pixel shading removes them.
            diff_k = np.clip(n @ key_dir, 0.0, 1.0)[..., None]
            diff_f = np.clip(n @ fill_dir, 0.0, 1.0)[..., None]
            fres = (1.0 - np.clip(n[..., 2:3], 0.0, 1.0)) ** rim_power
            spec = np.clip(n @ half, 0.0, 1.0)[..., None] ** spec_power
            diffuse = (
                ambient_str
                + key_str * diff_k * key_color
                + fill_str * diff_f * fill_color
            ) * albedo
            rgb = diffuse + rim_str * fres * rim_color + spec_str * spec
            return np.clip(rgb, 0.0, 1.0)

        # ------------------------------------------------------------------
        # 6. Rasterise (z-buffered — no painter's sort needed), one face chunk at
        #    a time into the shared image/z-buffer. The z-buffer makes chunk order
        #    irrelevant, so the result is identical to a single-pass render.
        # ------------------------------------------------------------------
        # The albedo now lives in `_shade` (so highlights can add white on top),
        # so the rasteriser multiply is pure white — it just scales the absolute
        # [0, 1] colour `_shade` returns up to 8-bit.
        base_color = np.array([255, 255, 255], dtype=np.float32)

        # Transparent background — floats cleanly on any card colour.
        img = np.zeros((ss_height, ss_width, 3), dtype=np.uint8)
        zbuf = np.full((ss_height, ss_width), np.inf, dtype=np.float64)

        visible_total = 0
        for s in range(0, n_faces, chunk):
            fc = faces[s : s + chunk]
            tri = screen[fc]  # (c, 3, 3) screen-space
            view_tri = view[fc]  # (c, 3, 3) view-space

            edge1 = view_tri[:, 1] - view_tri[:, 0]
            edge2 = view_tri[:, 2] - view_tri[:, 0]
            # Back-face cull: in right-handed view-space visible faces have raw
            # z<0; a negative-determinant (front-on flat) view flips that sign.
            raw_normals = np.cross(edge1, edge2)  # (c, 3)
            norm_len = np.linalg.norm(raw_normals, axis=1)  # (c,)

            # Object-space face normals for the crease test (the view normals are
            # flipped toward the camera, which would corrupt smoothing across
            # silhouettes).
            f_obj = verts[fc]  # (c, 3, 3)
            fn = np.cross(f_obj[:, 1] - f_obj[:, 0], f_obj[:, 2] - f_obj[:, 0])
            fn = fn / np.where(
                np.linalg.norm(fn, axis=1, keepdims=True) == 0,
                1.0,
                np.linalg.norm(fn, axis=1, keepdims=True),
            )

            corner_smooth = vsm[pos_id[fc]]  # (c, 3, 3) object-space
            # Blend each corner between its smoothed normal and the flat face
            # normal by how far the two diverge (a crease measure). A *smooth*
            # blend (smoothstep), not a hard threshold, is essential: a binary
            # flip snaps neighbouring corners between smooth and flat, which on
            # coarse fillets paints a comb of sharp light/dark streaks. The window
            # cos(41°)=0.75 → cos(23°)=0.92 keeps a 90° edge's 45° half-angle
            # (cos 0.707, below the window) fully flat so box/mechanical edges stay
            # crisp, while curved fillets blend gradually and read smooth.
            cos_crease = np.sum(corner_smooth * fn[:, None, :], axis=2)  # (c, 3)
            t = np.clip((cos_crease - 0.75) / (0.92 - 0.75), 0.0, 1.0)
            t = (t * t * (3.0 - 2.0 * t))[..., None]  # smoothstep, (c, 3, 1)
            corner_n = t * corner_smooth + (1.0 - t) * fn[:, None, :]
            corner_n = corner_n / np.where(
                np.linalg.norm(corner_n, axis=2, keepdims=True) == 0,
                1.0,
                np.linalg.norm(corner_n, axis=2, keepdims=True),
            )
            # Into view-space and flip toward the camera (+Z). These per-corner
            # normals are interpolated per pixel in the rasteriser (Phong); the
            # Fresnel rim, diffuse and specular are all evaluated there from the
            # interpolated normal, so there is no separate per-vertex colour pass.
            cvn = corner_n @ rot_T  # (c, 3, 3)
            cvn = np.where(cvn[..., 2:3] >= 0, cvn, -cvn)

            front = (raw_normals[:, 2] * view_handedness) < 0.0
            valid = front & (norm_len > 1e-8)
            tri = tri[valid]
            cvn = cvn[valid]
            visible_total += int(tri.shape[0])

            _rasterise_triangles(
                img, zbuf, tri, cvn, _shade, base_color, ss_width, ss_height
            )
            # Free this chunk's temporaries before the next one so only one
            # chunk's worth of per-face arrays is ever live. No gc.collect() here:
            # there are no reference cycles in the hot loop, and the per-file
            # _reclaim_memory() in mesh_processing already returns arenas to the OS.
            del view_tri, edge1, edge2, raw_normals, norm_len, f_obj, fn
            del corner_smooth, cos_crease, corner_n, cvn, tri, valid

        if visible_total == 0:
            # Degenerate / fully back-facing mesh: paint every face flat so the
            # silhouette still reads, matching the single-pass fallback.
            logger.warning(
                "mesh_render: no visible triangles for %s — using silhouette", name
            )
            flat_color = albedo * 0.6

            def _flat_shade(n: "np.ndarray", _c=flat_color) -> "np.ndarray":
                return np.broadcast_to(_c, n.shape)

            for s in range(0, n_faces, chunk):
                fc = faces[s : s + chunk]
                tri = screen[fc]
                nrm = np.zeros((tri.shape[0], 3, 3), dtype=np.float32)
                _rasterise_triangles(
                    img, zbuf, tri, nrm, _flat_shade, base_color, ss_width, ss_height
                )
                del tri, nrm

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

    # "Hero" 3/4 view for a solid model. 3D-print models are Z-up (they sit
    # flat-based on the bed), so we keep Z as screen-up and look from the
    # front-left tilted ~18° down — the way the interactive 3D viewer frames a
    # model. The old view stared 30° *down the Z axis*, which showed the top of
    # an upright model (e.g. the gathered top of a dumpling) instead of its face.
    azim = np.radians(-35.0)  # spin around the up (Z) axis for a 3/4 view
    tilt = np.radians(-18.0)  # look slightly down on the top
    ca, sa = np.cos(azim), np.sin(azim)
    ct, st = np.cos(tilt), np.sin(tilt)
    spin_z = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]], dtype=np.float64)
    # Base: camera on -Y looking toward +Y, with object Z mapped to screen-up.
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    tilt_x = np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]], dtype=np.float64)
    return tilt_x @ base @ spin_z


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
    tilt_mat = np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]], dtype=np.float64)
    return tilt_mat @ base


# ---------------------------------------------------------------------------
# Rasterisation helpers
# ---------------------------------------------------------------------------


def _rasterise_triangles(
    img, zbuf, tri, vert_nrm, shade, base_color, width: int, height: int
) -> None:
    """Z-buffered Phong rasteriser, vectorised over triangles.

    ``vert_nrm`` is (F, 3, 3): a view-space normal for each of a triangle's three
    vertices. ``shade`` maps an (N, 3) array of unit normals to (N, 3) RGB in
    [0, 1]. Each triangle's clipped bounding box is expanded into a flat array of
    candidate pixels; the three corner normals are interpolated by the same
    barycentric weights used for the inside test, renormalised, then lit per
    fragment — so the surface shades smoothly without the Gouraud colour banding
    and pole "fan" streaks that interpolating a precomputed colour produced.
    Depth interpolation and per-pixel z-buffer resolution run as whole-array
    numpy operations.
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
    vert_nrm = vert_nrm[keep]  # (F, 3, 3)

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
            pix = (pix_y * width + pix_x)[inside]
            z = (w0 * a[:, 2] + w1 * b[:, 2] + w2 * c[:, 2])[inside]
            # Phong: interpolate the three corner normals by the same barycentric
            # weights, renormalise, then light per fragment. Only inside pixels are
            # shaded, so the per-pixel lighting cost stays proportional to covered
            # area, not bounding-box area.
            wi0, wi1, wi2 = w0[inside, None], w1[inside, None], w2[inside, None]
            vn = vert_nrm[tri_idx[inside]]  # (P, 3, 3)
            n = wi0 * vn[:, 0] + wi1 * vn[:, 1] + wi2 * vn[:, 2]
            nlen = np.linalg.norm(n, axis=1, keepdims=True)
            n = n / np.where(nlen == 0, 1.0, nlen)
            col = shade(n)  # (P, 3) in [0, 1]

            # Nearest candidate per pixel within this chunk: sort by
            # (pixel, z) and keep the first occurrence of each pixel.
            order = np.lexsort((z, pix))
            pix_s = pix[order]
            z_s = z[order]
            col_s = col[order]
            first = np.ones(len(pix_s), dtype=bool)
            first[1:] = pix_s[1:] != pix_s[:-1]
            pix_u = pix_s[first]
            z_u = z_s[first]
            col_u = col_s[first]

            # Then resolve against the global z-buffer.
            nearer = z_u < flat_z[pix_u]
            target = pix_u[nearer]
            flat_z[target] = z_u[nearer]
            flat_img[target] = np.clip(
                base_color * col_u[nearer], 0, 255
            ).astype(np.uint8)

        start = end
