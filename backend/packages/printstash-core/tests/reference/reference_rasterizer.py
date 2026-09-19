"""Frozen pre-migration stages, used only to compare native results in tests."""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray: TypeAlias = NDArray[np.floating[Any]]
    UInt8Array: TypeAlias = NDArray[np.uint8]
    IntArray: TypeAlias = NDArray[np.int64]
    Shade: TypeAlias = Callable[[FloatArray], FloatArray]

FLAT_MESH_THICKNESS_RATIO = PREVIEW_PROFILE.flat_thickness_ratio

# Cap candidate-pixel expansion per rasteriser chunk (~tens of MB of
# temporaries at this size).
# Smaller batches bound dense-model temporaries without reducing the image or
# sampling faces. Every triangle still resolves through the same z-buffer.
_CHUNK_PIXEL_BUDGET = 250_000


@dataclass(frozen=True)
class PhongShader:
    """Reference callback plus the same light parameters for the optional kernel."""

    reference: Shade
    parameters: tuple[float, ...]

    def __call__(self, normals: FloatArray) -> FloatArray:
        return self.reference(normals)


@dataclass
class RasterBudget:
    """Cumulative candidate-pixel budget shared by rasteriser calls."""

    limit: int = 1_000_000
    used: int = 0


class LogSink(Protocol):
    """Minimal logging contract optionally supplied by an application facade."""

    def error(self, msg: object, *args: object) -> None: ...

    def warning(
        self,
        msg: object,
        *args: object,
        exc_info: bool = False,
    ) -> None: ...


class Rasteriser(Protocol):
    """Typed callback boundary for the vectorized triangle rasterizer."""

    def __call__(
        self,
        img: UInt8Array,
        zbuf: FloatArray,
        tri: FloatArray,
        vert_nrm: FloatArray,
        shade: Shade,
        base_color: FloatArray,
        width: int,
        height: int,
    ) -> int | None: ...


class RasterFrame(Protocol):
    """Owned render storage, with a single immutable image transfer."""

    def draw(
        self, tri: FloatArray, normals: FloatArray, shade: Shade, base_color: FloatArray
    ) -> None: ...

    def rgba(self) -> bytes: ...


class PreparedMesh(Protocol):
    lower: tuple[float, float, float]
    upper: tuple[float, float, float]

    def render(
        self,
        rotation: list[list[float]],
        handedness: float,
        width: int,
        height: int,
        margin: float,
        lighting: tuple[float, ...],
        flat_color: tuple[int, ...],
    ) -> bytes: ...


def _preview_shader(matte: bool) -> tuple[FloatArray, PhongShader]:
    import numpy as np

    def _normalise(v: FloatArray) -> FloatArray:
        return v / np.linalg.norm(v)

    # Model albedo: the blue-grey surface colour, baked into the light terms
    # below (not the rasteriser's per-pixel multiply, which is now pure white —
    # see `base_color`). Folding albedo into shading lets the specular and rim
    # add *white* highlights on top of the tinted body, so curved surfaces get
    # a bright sheen that reads on the dark card instead of clipping at a dim
    # blue-grey ceiling. Slightly lighter + less saturated than the old base so
    # the model pops against a near-black background.
    albedo = np.array(PREVIEW_PROFILE.material_albedo)

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
    spec_str = 0.0 if matte else 0.22
    spec_power = 32.0

    def _shade(n: FloatArray) -> FloatArray:
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
            ambient_str + key_str * diff_k * key_color + fill_str * diff_f * fill_color
        ) * albedo
        rgb = diffuse + rim_str * fres * rim_color + spec_str * spec
        return np.clip(rgb, 0.0, 1.0)

    shade = PhongShader(
        _shade,
        tuple(
            float(value)
            for vector in (
                key_dir,
                fill_dir,
                half,
                albedo,
                key_color,
                fill_color,
                rim_color,
            )
            for value in vector
        )
        + (
            key_str,
            fill_str,
            rim_str,
            ambient_str,
            spec_str,
            rim_power,
            spec_power,
        ),
    )
    return albedo, shade


def _encode_preview(
    rgba_bytes: bytes,
    width: int,
    height: int,
    supersample: int,
    output_format: Literal["PNG", "WEBP"],
) -> bytes:
    import numpy as np
    from PIL import Image  # pyright: ignore[reportMissingTypeStubs]

    ss_width, ss_height = width * supersample, height * supersample
    # ------------------------------------------------------------------
    # 7. Post-process: Lanczos downsample (anti-aliasing) + subtle vignette.
    # ------------------------------------------------------------------
    pil = Image.frombytes("RGBA", (ss_width, ss_height), rgba_bytes)
    # ``Resampling`` is present in both supported Pillow lines (10 and 12),
    # while Pillow 12's typing no longer exposes the legacy Image.LANCZOS.
    if supersample > 1:
        pil = pil.resize((width, height), Image.Resampling.LANCZOS)

    # Vignette: darken the corners slightly so the model "pops"
    vx = np.linspace(-1, 1, width, dtype=np.float32)
    vy = np.linspace(-1, 1, height, dtype=np.float32)
    gx, gy = np.meshgrid(vx, vy)
    vignette = 1.0 - 0.18 * np.clip(gx**2 + gy**2, 0, 1)
    vig_arr = np.array(pil, dtype=np.float32)
    vig_arr[:, :, :3] *= vignette[:, :, None]  # vignette RGB only, keep alpha
    pil = Image.fromarray(np.clip(vig_arr, 0, 255).astype(np.uint8), mode="RGBA")

    buf = io.BytesIO()
    if output_format == "WEBP":
        pil.save(
            buf,
            format="WEBP",
            lossless=True,
            exact=True,
            method=PREVIEW_PROFILE.encoding_method,
        )
    else:
        pil.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_mesh_thumbnail(
    mesh: Any,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    face_chunk_size: int = 64_000,
    logger: LogSink | None = None,
    rasterise_triangles: Rasteriser | None = None,
    output_format: Literal["PNG", "WEBP"] = "PNG",
    view_rotation: FloatArray | None = None,
    matte: bool = False,
    frame_factory: Callable[[int, int], RasterFrame] | None = None,
    normal_preparer: Callable[..., FloatArray] | None = None,
    mesh_preparer: Callable[..., PreparedMesh] | None = None,
    image_encoder: Callable[..., bytes] | None = None,
) -> bytes | None:
    """Render a PNG thumbnail from an already-loaded mesh.

    Lets callers that need both geometry and a thumbnail load the mesh once.
    Returns raw PNG bytes, or None on failure.
    """
    try:
        import numpy as np

        # Pillow 10 does not ship the ``py.typed`` marker that later supported
        # versions provide. Runtime imports are still valid across the matrix.
        import PIL.Image  # pyright: ignore[reportMissingTypeStubs]  # noqa: F401
    except ImportError:
        if logger is not None:
            logger.error(
                "mesh_render: numpy/Pillow unavailable; cannot render thumbnail"
            )
        return None

    if (
        mesh is None
        or len(mesh.vertices) == 0
        or mesh.faces is None
        or len(mesh.faces) == 0
    ):
        if logger is not None:
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

        supersample = PREVIEW_PROFILE.supersample_for(width)
        ss_width = width * supersample
        ss_height = height * supersample

        if mesh_preparer is not None:
            prepared = mesh_preparer(verts, faces, max(int(face_chunk_size), 1))
            rotation = (
                _select_view_rotation(
                    np.array([prepared.lower, prepared.upper], dtype=np.float32)
                )
                if view_rotation is None
                else np.asarray(view_rotation, dtype=np.float64)
            )
            if (
                rotation.shape != (3, 3)
                or not np.isfinite(rotation).all()
                or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-6)
            ):
                raise ValueError("invalid orthographic view rotation")
            albedo, shade = _preview_shader(matte)
            rgba_bytes = prepared.render(
                rotation.tolist(),
                float(np.linalg.det(rotation)),
                ss_width,
                ss_height,
                PREVIEW_PROFILE.margin_fraction,
                shade.parameters + (255.0, 255.0, 255.0),
                tuple(int(v) for v in np.clip(albedo * 0.6 * 255.0, 0, 255)),
            )
            return (image_encoder or _encode_preview)(
                rgba_bytes, width, height, supersample, output_format
            )

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
        rotation = (
            _select_view_rotation(verts)
            if view_rotation is None
            else np.asarray(view_rotation, dtype=np.float64)
        )
        if (
            rotation.shape != (3, 3)
            or not np.isfinite(rotation).all()
            or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-6)
        ):
            raise ValueError("invalid orthographic view rotation")
        view_handedness = float(np.linalg.det(rotation))
        # Keep the matmul in float32 (rotation is built in float64 for accuracy)
        # so `view` and everything derived from it stays half-width.
        view = verts @ rotation.T.astype(np.float32)  # (N, 3) camera at -Z → +Z

        # ------------------------------------------------------------------
        # 3. Orthographic projection with the canonical safe margin.
        # ------------------------------------------------------------------
        xs, ys = view[:, 0], view[:, 1]
        x_min, x_max = float(xs.min()), float(xs.max())
        y_min, y_max = float(ys.min()), float(ys.max())
        extent_x = max(x_max - x_min, 1e-6)
        extent_y = max(y_max - y_min, 1e-6)
        margin = PREVIEW_PROFILE.margin_fraction
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
        chunk = max(int(face_chunk_size), 1)
        rasterise = rasterise_triangles or _rasterise_triangles

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
        if normal_preparer is not None:
            vsm = normal_preparer(verts, faces, pos_id, n_pos, chunk)
        else:
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
                ang = np.arccos(
                    np.clip(np.sum(e_ab * e_ac, axis=2), -1.0, 1.0)
                )  # (c,3)
                flat_pos = pos_id[fc].ravel()  # (3c,) welded id per face corner
                # Each corner contributes its face normal scaled by that corner angle.
                fn_per_corner = (
                    np.repeat(fn, 3, axis=0) * ang.ravel()[:, None]
                )  # (3c,3)
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

        albedo, shade = _preview_shader(matte)

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
        frame = frame_factory(ss_width, ss_height) if frame_factory else None
        img = (
            np.zeros((ss_height, ss_width, 3), dtype=np.uint8)
            if frame is None
            else None
        )
        zbuf = (
            np.full((ss_height, ss_width), np.inf, dtype=np.float64)
            if frame is None
            else None
        )

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
            front = (raw_normals[:, 2] * view_handedness) < 0.0
            valid = front & (norm_len > 1e-8)
            # Keep the same visibility rule, but apply it before gathering and
            # interpolating corner normals for faces that cannot be drawn.
            fc = fc[valid]
            tri = tri[valid]

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

            visible_total += int(tri.shape[0])

            if frame is not None:
                frame.draw(tri, cvn, shade, base_color)
            else:
                assert img is not None and zbuf is not None
                rasterise(img, zbuf, tri, cvn, shade, base_color, ss_width, ss_height)
            # Free this chunk's temporaries before the next one so only one
            # chunk's worth of per-face arrays is ever live. No gc.collect() here:
            # there are no reference cycles in the hot loop, and the per-file
            # _reclaim_memory() in mesh_processing already returns arenas to the OS.
            del view_tri, edge1, edge2, raw_normals, norm_len, f_obj, fn
            del corner_smooth, cos_crease, corner_n, cvn, tri, valid

        if visible_total == 0:
            # Degenerate / fully back-facing mesh: paint every face flat so the
            # silhouette still reads, matching the single-pass fallback.
            if logger is not None:
                logger.warning(
                    "mesh_render: no visible triangles for %s — using silhouette",
                    name,
                )
            flat_color = albedo * 0.6

            def _flat_shade(
                n: FloatArray,
                _c: FloatArray = flat_color,
            ) -> FloatArray:
                return np.broadcast_to(_c, n.shape)

            for s in range(0, n_faces, chunk):
                fc = faces[s : s + chunk]
                tri = screen[fc]
                nrm = np.zeros((tri.shape[0], 3, 3), dtype=np.float32)
                if frame is not None:
                    frame.draw(tri, nrm, _flat_shade, base_color)
                else:
                    assert img is not None and zbuf is not None
                    rasterise(
                        img,
                        zbuf,
                        tri,
                        nrm,
                        _flat_shade,
                        base_color,
                        ss_width,
                        ss_height,
                    )
                del tri, nrm

        # Alpha = 255 wherever a triangle was painted, 0 elsewhere.
        if frame is not None:
            rgba_bytes = frame.rgba()
        else:
            assert img is not None and zbuf is not None
            alpha = np.where(zbuf < np.inf, np.uint8(255), np.uint8(0)).astype(np.uint8)
            rgba_bytes = np.dstack([img, alpha]).tobytes()

        return (image_encoder or _encode_preview)(
            rgba_bytes, width, height, supersample, output_format
        )

    except Exception:
        if logger is not None:
            logger.warning(
                "mesh_render: render_thumbnail failed for %s", name, exc_info=True
            )
        return None


# ---------------------------------------------------------------------------
# View selection helpers
# ---------------------------------------------------------------------------


def _select_view_rotation(verts: FloatArray) -> FloatArray:
    """Return an original-space -> view-space matrix for thumbnail rendering."""
    import numpy as np

    extents = verts.max(axis=0) - verts.min(axis=0)
    thin_axis = int(np.argmin(extents))
    thin_extent = float(extents[thin_axis])
    broad_extent = float(np.max(np.delete(extents, thin_axis)))

    if broad_extent > 1e-6 and thin_extent / broad_extent <= FLAT_MESH_THICKNESS_RATIO:
        return _front_rotation_for_thin_axis(thin_axis)

    # "Hero" 3/4 view for a solid model. 3D-print models are Z-up (they sit
    # flat-based on the bed), so we keep Z as screen-up and look from the
    # front-left tilted ~18° down — the way the interactive 3D viewer frames a
    # model. The old view stared 30° *down the Z axis*, which showed the top of
    # an upright model (e.g. the gathered top of a dumpling) instead of its face.
    azim = np.radians(PREVIEW_PROFILE.hero_azimuth_degrees)
    # View-space rotation is opposite the positive camera elevation.
    tilt = np.radians(-PREVIEW_PROFILE.hero_elevation_degrees)
    ca, sa = np.cos(azim), np.sin(azim)
    ct, st = np.cos(tilt), np.sin(tilt)
    spin_z = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]], dtype=np.float64)
    # Base: camera on -Y looking toward +Y, with object Z mapped to screen-up.
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    tilt_x = np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]], dtype=np.float64)
    return tilt_x @ base @ spin_z


def _front_rotation_for_thin_axis(thin_axis: int) -> FloatArray:
    """View a flat mesh mostly face-on but tilted 25° to reveal depth in recesses."""
    import numpy as np

    # 25° tilt rotation around the screen-X axis (tips the model top toward camera).
    tilt = np.radians(PREVIEW_PROFILE.flat_tilt_degrees)
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
    img: UInt8Array,
    zbuf: FloatArray,
    tri: FloatArray,
    vert_nrm: FloatArray,
    shade: Shade,
    base_color: FloatArray,
    width: int,
    height: int,
    *,
    budget: RasterBudget | None = None,
) -> int:
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
        return 0

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
        return 0

    v0, v1, v2 = v0[keep], v1[keep], v2[keep]
    denom = denom[keep]
    x0, x1, y0, y1 = x0[keep], x1[keep], y0[keep], y1[keep]
    vert_nrm = vert_nrm[keep]  # (F, 3, 3)

    bbox_w = x1 - x0 + 1
    bbox_h = y1 - y0 + 1
    areas = bbox_w * bbox_h

    flat_img = img.reshape(-1, 3)
    flat_z = zbuf.reshape(-1)

    # Chunk triangles so the candidate-pixel expansion stays within budget. A
    # budget passed by a caller is cumulative across all rasteriser calls; this
    # matters for fallback renderers, which call us once per input chunk.
    candidates = 0
    for (
        source_faces,
        candidate_x0,
        candidate_y0,
        candidate_width,
        counts,
        pixel_offset,
    ) in _triangle_pixel_batches(v0, v1, v2, x0, y0, bbox_w, bbox_h, areas, budget):
        candidates += int(counts.sum())
        tri_idx = np.repeat(np.arange(len(source_faces)), counts)
        source_idx = source_faces[tri_idx]
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
        offsets = (
            pixel_offset + np.arange(int(counts.sum())) - np.repeat(starts, counts)
        )

        w_per_tri = candidate_width[tri_idx]
        pix_x = candidate_x0[tri_idx] + offsets % w_per_tri
        pix_y = candidate_y0[tri_idx] + offsets // w_per_tri

        fx = pix_x + 0.5
        fy = pix_y + 0.5
        a = v0[source_idx]
        b = v1[source_idx]
        c = v2[source_idx]
        d = denom[source_idx]

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
            # Nearest candidate per pixel within this chunk: sort by
            # (pixel, z) and keep the first occurrence of each pixel.
            order = np.lexsort((z, pix))
            pix_s = pix[order]
            z_s = z[order]
            first = np.ones(len(pix_s), dtype=bool)
            first[1:] = pix_s[1:] != pix_s[:-1]
            pix_u = pix_s[first]
            z_u = z_s[first]

            # Then resolve against the global z-buffer.
            nearer = z_u < flat_z[pix_u]
            target = pix_u[nearer]
            # Only visible winners need Phong shading. Interpolating normals for
            # hidden layers formerly allocated a 3×3 array for every covered
            # fragment, then immediately discarded most of those colors.
            winners = np.flatnonzero(inside)[order[first][nearer]]
            vn = vert_nrm[source_idx[winners]]
            n = (
                w0[winners, None] * vn[:, 0]
                + w1[winners, None] * vn[:, 1]
                + w2[winners, None] * vn[:, 2]
            )
            nlen = np.linalg.norm(n, axis=1, keepdims=True)
            n = n / np.where(nlen == 0, 1.0, nlen)
            col = shade(n)
            flat_z[target] = z_u[nearer]
            flat_img[target] = np.clip(base_color * col, 0, 255).astype(np.uint8)

    return candidates


def _pixel_batches(
    x0: IntArray,
    y0: IntArray,
    widths: IntArray,
    heights: IntArray,
    areas: IntArray,
    budget: RasterBudget | None,
) -> Iterator[tuple[IntArray, IntArray, IntArray, IntArray, IntArray, int]]:
    """Bound allocations independently from the cumulative work allowance.

    Large faces span several pixel batches. Only an insufficient total work
    budget selects a centered partial tile; an allocation chunk never crops a
    face that the caller can afford to render completely.
    """
    import numpy as np

    cumulative = np.cumsum(areas)
    start = 0
    while start < len(areas):
        remaining = budget.limit - budget.used if budget is not None else None
        available = (
            _CHUNK_PIXEL_BUDGET
            if remaining is None
            else min(_CHUNK_PIXEL_BUDGET, remaining)
        )
        if available <= 0:
            break
        if int(areas[start]) > available:
            width, height = int(widths[start]), int(heights[start])
            left, top = int(x0[start]), int(y0[start])
            if remaining is not None and int(areas[start]) > remaining:
                width = min(width, remaining)
                height = min(height, max(1, remaining // width))
                left += (int(widths[start]) - width) // 2
                top += (int(heights[start]) - height) // 2
            total = width * height
            for offset in range(0, total, _CHUNK_PIXEL_BUDGET):
                count = min(_CHUNK_PIXEL_BUDGET, total - offset)
                if budget is not None:
                    budget.used += count
                yield (
                    np.array([start], dtype=np.int64),
                    np.array([left], dtype=np.int64),
                    np.array([top], dtype=np.int64),
                    np.array([width], dtype=np.int64),
                    np.array([count], dtype=np.int64),
                    offset,
                )
            start += 1
            continue
        consumed = int(cumulative[start - 1]) if start else 0
        end = int(np.searchsorted(cumulative, consumed + available, side="right"))
        counts = areas[start:end]
        if budget is not None:
            budget.used += int(counts.sum())
        yield (
            np.arange(start, end),
            x0[start:end],
            y0[start:end],
            widths[start:end],
            counts,
            0,
        )
        start = end


def _triangle_pixel_batches(
    v0: FloatArray,
    v1: FloatArray,
    v2: FloatArray,
    x0: IntArray,
    y0: IntArray,
    widths: IntArray,
    heights: IntArray,
    areas: IntArray,
    budget: RasterBudget | None,
) -> Iterator[tuple[IntArray, IntArray, IntArray, IntArray, IntArray, int]]:
    """Trim wasteful bounding boxes to bounded scanline rectangles.

    Keep source order and the exact barycentric/depth tests. Broad or tiny faces
    retain the cheaper box path. Explicit fallback budgets retain their existing
    centered-tile semantics and accounting.
    """
    import numpy as np

    twice_area = np.abs(
        (v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
        - (v1[:, 1] - v0[:, 1]) * (v2[:, 0] - v0[:, 0])
    )
    split = (areas > 256) & (twice_area < areas * 0.25)
    if budget is not None or not split.any():
        yield from _pixel_batches(x0, y0, widths, heights, areas, budget)
        return

    row_counts = np.where(split, heights, 1)
    zeros = np.zeros_like(row_counts)
    ones = np.ones_like(row_counts)
    for faces, _, _, _, counts, offset in _pixel_batches(
        zeros, zeros, row_counts, ones, row_counts, None
    ):
        local_faces = np.repeat(np.arange(len(faces)), counts)
        source = faces[local_faces]
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
        row = offset + np.arange(int(counts.sum())) - np.repeat(starts, counts)
        left = x0[source].copy()
        top = y0[source].copy()
        span = widths[source].copy()
        depth = heights[source].copy()
        scan = split[source]
        selected = source[scan]
        fy = y0[selected] + row[scan] + 0.5
        low = np.full(len(selected), np.inf)
        high = np.full(len(selected), -np.inf)
        epsilon = np.finfo(v0.dtype).eps
        for a, b in ((v0, v1), (v1, v2), (v2, v0)):
            ax, ay = a[selected, 0], a[selected, 1]
            bx, by = b[selected, 0], b[selected, 1]
            dy = by - ay
            # The barycentric subtraction can round a pixel onto an edge just
            # outside its geometric extent. Include those adjacent scanlines;
            # the unchanged inside test determines whether they are painted.
            slack = 8 * epsilon * np.maximum(1, np.maximum(np.abs(ay), np.abs(by)))
            crosses = (
                (dy != 0)
                & (fy >= np.minimum(ay, by) - slack)
                & (fy <= np.maximum(ay, by) + slack)
            )
            x = ax + (fy - ay) * (bx - ax) / np.where(dy == 0, 1, dy)
            low = np.minimum(low, np.where(crosses, x, np.inf))
            high = np.maximum(high, np.where(crosses, x, -np.inf))
        valid = np.isfinite(low) & np.isfinite(high)
        # Expand to adjacent pixel centers; the original barycentric test decides
        # edge membership, including float32 roundoff at shared triangle edges.
        scan_left = np.maximum(
            x0[selected], np.floor(np.where(valid, low, 0) - 0.5).astype(np.int64)
        )
        scan_right = np.minimum(
            x0[selected] + widths[selected] - 1,
            np.ceil(np.where(valid, high, 0) - 0.5).astype(np.int64),
        )
        left[scan] = scan_left
        top[scan] += row[scan]
        span[scan] = np.where(valid, np.maximum(0, scan_right - scan_left + 1), 0)
        depth[scan] = 1
        for rectangles, bx, by, bw, pixels, pixel_offset in _pixel_batches(
            left, top, span, depth, span * depth, None
        ):
            yield source[rectangles], bx, by, bw, pixels, pixel_offset
