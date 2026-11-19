"""Framework-neutral software triangle rasteriser for mesh thumbnails.

Pure NumPy + Pillow — no GL, display, database, storage, or application state.

Compatibility contract: the same code path supports the OSS profile
(NumPy 2 / Trimesh 5 / Pillow 12 / Cascadio 0.1.1) and the second consumer's
profile (NumPy 1 / Trimesh 4 / Pillow 10 / Cascadio 0.0.14). Meshes are accepted
through the structural ``vertices`` / ``faces`` interface, so Trimesh is not
imported. Cascadio is also deliberately absent: STEP tessellation remains a
caller concern and has no place in the rasterizer's dependency graph.

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

Memory shape: per-face projection and shading temporaries are built and freed one
``mesh_render_face_chunk_size`` chunk at a time. The camera and geometry arrays
(projected vertices and prepared corner normals) are shared across chunks.

The backend invokes ``render_mesh_thumbnail`` in a disposable mesh worker through
its bounded isolation seam. This function takes an already-loaded mesh and
returns PNG bytes; process admission, deadlines, memory limits and embedded
preview selection belong to backend owners. An OOM therefore terminates the
worker rather than the API process.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, runtime_checkable

from .preview_profile import (
    MATTE_LIGHTING,
    PREVIEW_PROFILE,
    REFERENCE_LIGHTING,
    REFERENCE_THUMBNAIL_PROFILE,
    STUDIO_LIGHTING,
    PreviewLighting,
    PreviewProfile,
    Vector,
)
from .render_geometry import (
    MeshRenderInput,
    PreparedRender,
    SceneRenderInput,
    prepare_mesh_render,
    prepare_scene_render,
)
from .similarity.components import ExpandedScene

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
# Smaller batches leave room for candidate and winner-normal temporaries inside
# a 512 MiB worker. This bounds allocation, without reducing cumulative work,
# sampling faces or cropping coverage; all triangles use the same z-buffer.
_CHUNK_PIXEL_BUDGET = 125_000


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


@runtime_checkable
class DeferredRasteriser(Rasteriser, Protocol):
    """Publish a private frame once, after all chunks and silhouette recovery.

    Deferred implementations keep drawing state outside the CPU image arrays.
    ``finish`` must complete pending drawing and populate both arrays before
    common alpha, downsampling and vignette processing. The caller owns backend
    resources; a failed chunk never publishes a partial frame. Plain rasterizer
    callbacks retain their existing immediate-write contract.
    """

    def finish(self, img: UInt8Array, zbuf: FloatArray) -> None: ...


@runtime_checkable
class PreparedRasteriser(DeferredRasteriser, Protocol):
    """Optional persistent backend accepting one camera-independent geometry set.

    Framing and final encoding stay canonical. Native allocation, ownership and
    qualification belong to the caller; core never imports a device runtime.
    """

    def draw_prepared(
        self,
        geometry: PreparedRender,
        rotation: FloatArray,
        screen: FloatArray,
        width: int,
        height: int,
        matte: bool,
    ) -> None: ...


class RGBBackground(str, Enum):
    IGNORE_ALPHA = "ignore_alpha"
    WHITE = "white"


@dataclass(frozen=True)
class RenderedPixels:
    width: int
    height: int
    rgba: bytes

    def __post_init__(self) -> None:
        if (
            type(self.width) is not int
            or self.width < 1
            or type(self.height) is not int
            or self.height < 1
            or type(self.rgba) is not bytes
            or len(self.rgba) != self.width * self.height * 4
        ):
            raise ValueError("invalid_rendered_pixels")

    def rgb(self, background: RGBBackground) -> bytes:
        from PIL import Image  # pyright: ignore[reportMissingTypeStubs]

        if type(background) is not RGBBackground:
            raise TypeError("invalid_rgb_background")
        image = Image.frombytes("RGBA", (self.width, self.height), self.rgba)
        if background is RGBBackground.WHITE:
            image = Image.alpha_composite(
                Image.new("RGBA", image.size, (255, 255, 255, 255)), image
            )
        return image.convert("RGB").tobytes()


def encode_rendered_pixels(
    pixels: RenderedPixels,
    *,
    output_format: Literal["PNG", "WEBP"] = "PNG",
    profile: PreviewProfile = PREVIEW_PROFILE,
) -> bytes:
    """Encode a completed frame with the versioned thumbnail compression policy."""
    if output_format not in ("PNG", "WEBP"):
        raise ValueError("invalid_thumbnail_format")
    from PIL import Image

    image = Image.frombytes("RGBA", (pixels.width, pixels.height), pixels.rgba)
    buffer = io.BytesIO()
    if output_format == "WEBP":
        image.save(
            buffer,
            format="WEBP",
            lossless=profile.encoding_lossless,
            exact=profile.encoding_exact,
            alpha_quality=100,
            method=profile.encoding_method,
            quality=profile.encoding_quality,
        )
    else:
        image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _render_thumbnail(
    source: MeshRenderInput | SceneRenderInput,
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
) -> bytes | None:
    try:
        # Keep optional dependency refusal ahead of source allocations.
        from PIL import Image  # pyright: ignore[reportMissingTypeStubs]

        del Image
        prepared = (
            prepare_scene_render(source.scene, face_chunk_size=face_chunk_size)
            if isinstance(source, SceneRenderInput)
            else prepare_mesh_render(source.mesh, face_chunk_size=face_chunk_size)
        )
        if prepared is None:
            if logger is not None:
                logger.warning("mesh_render: empty mesh for %s", name)
            return None
    except ImportError:
        if logger is not None:
            logger.error(
                "mesh_render: numpy/Pillow unavailable; cannot render thumbnail"
            )
        return None
    except Exception:
        if logger is not None:
            logger.warning(
                "mesh_render: render_thumbnail failed for %s", name, exc_info=True
            )
        return None
    return render_prepared_thumbnail(
        prepared,
        name,
        width,
        height,
        face_chunk_size=face_chunk_size,
        logger=logger,
        rasterise_triangles=rasterise_triangles,
        output_format=output_format,
        view_rotation=view_rotation,
        matte=matte,
    )


def render_prepared_thumbnail(
    prepared: PreparedRender,
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
    reference_thumbnail: bool = False,
) -> bytes | None:
    pixels = render_prepared_pixels(
        prepared,
        name,
        width,
        height,
        face_chunk_size=face_chunk_size,
        logger=logger,
        rasterise_triangles=rasterise_triangles,
        view_rotation=view_rotation,
        matte=matte,
        reference_thumbnail=reference_thumbnail,
    )
    if pixels is None:
        return None
    try:
        return encode_rendered_pixels(
            pixels,
            output_format=output_format,
            profile=REFERENCE_THUMBNAIL_PROFILE
            if reference_thumbnail
            else PREVIEW_PROFILE,
        )
    except Exception:
        if logger is not None:
            logger.warning(
                "mesh_render: render_thumbnail failed for %s", name, exc_info=True
            )
        return None


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
) -> bytes | None:
    """Render a loaded structural mesh through the shared prepared geometry path."""
    return _render_thumbnail(
        MeshRenderInput(mesh),
        name,
        width,
        height,
        face_chunk_size=face_chunk_size,
        logger=logger,
        rasterise_triangles=rasterise_triangles,
        output_format=output_format,
        view_rotation=view_rotation,
        matte=matte,
    )


def render_scene_thumbnail(
    scene: ExpandedScene,
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
) -> bytes | None:
    """Render retained scene resources without source materialization."""
    return _render_thumbnail(
        SceneRenderInput(scene),
        name,
        width,
        height,
        face_chunk_size=face_chunk_size,
        logger=logger,
        rasterise_triangles=rasterise_triangles,
        output_format=output_format,
        view_rotation=view_rotation,
        matte=matte,
    )


def make_preview_shade(albedo: Vector, lighting: PreviewLighting) -> Shade:
    """Build the diffuse, Fresnel rim and Blinn-Phong shader for unit normals."""
    import numpy as np

    def normalise(direction: FloatArray) -> FloatArray:
        return direction / np.linalg.norm(direction)

    body = np.asarray(albedo)
    key_dir = normalise(np.asarray(lighting.key.direction))
    fill_dir = normalise(np.asarray(lighting.fill.direction))
    half = normalise(key_dir + np.array([0.0, 0.0, 1.0]))
    key_colour = np.asarray(lighting.key.colour)
    fill_colour = np.asarray(lighting.fill.colour)
    rim_colour = np.asarray(lighting.rim_colour)

    def shade(n: FloatArray) -> FloatArray:
        diff_k = np.clip(n @ key_dir, 0.0, 1.0)[..., None]
        diff_f = np.clip(n @ fill_dir, 0.0, 1.0)[..., None]
        fresnel = (1.0 - np.clip(n[..., 2:3], 0.0, 1.0)) ** lighting.rim_power
        specular = np.clip(n @ half, 0.0, 1.0)[..., None] ** lighting.specular_power
        diffuse = (
            lighting.ambient
            + lighting.key.strength * diff_k * key_colour
            + lighting.fill.strength * diff_f * fill_colour
        ) * body
        rgb = (
            diffuse
            + lighting.rim_strength * fresnel * rim_colour
            + lighting.specular_strength * specular
        )
        return np.clip(rgb, 0.0, 1.0)

    return shade


def render_prepared_pixels(
    prepared: PreparedRender,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    face_chunk_size: int = 64_000,
    logger: LogSink | None = None,
    rasterise_triangles: Rasteriser | None = None,
    view_rotation: FloatArray | None = None,
    matte: bool = False,
    reference_thumbnail: bool = False,
) -> RenderedPixels | None:
    """Render final RGBA pixels using cached camera-independent geometry.

    Downsampling, vignette and raster math match the encoded-thumbnail path.
    No image serialization or decoding occurs here.
    """
    try:
        import numpy as np

        # Preserve the early optional-dependency failure before geometry work.
        __import__("PIL", fromlist=["Image"])
    except ImportError:
        if logger is not None:
            logger.error(
                "mesh_render: numpy/Pillow unavailable; cannot render thumbnail"
            )
        return None

    try:
        geometry = prepared
        verts = geometry.vertices

        profile = (
            REFERENCE_THUMBNAIL_PROFILE if reference_thumbnail else PREVIEW_PROFILE
        )
        supersample = profile.supersample_for(width)
        ss_width = width * supersample
        ss_height = height * supersample

        # ------------------------------------------------------------------
        # 1. Centre and normalise the mesh to a unit-ish bounding sphere.
        # ------------------------------------------------------------------
        # Preparation already removed the shared float64 world origin before
        # allocating the relative float32 positions used throughout rendering.

        # ------------------------------------------------------------------
        # 2. Pick a camera view.
        #
        #    Thin display-like prints (badges, signs, character fronts) are
        #    most recognisable from their broad face, matching the web viewer's
        #    first-open camera. Chunkier parts keep the older isometric angle.
        # ------------------------------------------------------------------
        rotation = (
            _select_view_rotation(verts, reference=reference_thumbnail)
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
        margin = profile.margin_fraction
        scale = min(
            (ss_width * (1 - 2 * margin)) / extent_x,
            (ss_height * (1 - 2 * margin)) / extent_y,
        )
        px = (xs - (x_min + x_max) * 0.5) * scale + ss_width * 0.5
        py = ss_height * 0.5 - (ys - (y_min + y_max) * 0.5) * scale  # flip Y
        pz = view[:, 2]
        screen = np.stack([px, py, pz], axis=1)  # (N, 3)

        # ------------------------------------------------------------------
        # 4. Reuse crease-aware object-space corner normals across cameras.
        # ------------------------------------------------------------------
        rot_T = rotation.T
        chunk = max(int(face_chunk_size), 1)
        rasterise = rasterise_triangles or _rasterise_triangles

        # 5. Shade interpolated normals with an albedo and lights in view-space.
        studio = not (matte or reference_thumbnail)
        lighting = (
            STUDIO_LIGHTING
            if studio
            else MATTE_LIGHTING
            if matte
            else REFERENCE_LIGHTING
        )
        albedo = (
            PREVIEW_PROFILE.material_albedo
            if studio
            else REFERENCE_THUMBNAIL_PROFILE.material_albedo
        )
        shade = make_preview_shade(albedo, lighting)

        # ------------------------------------------------------------------
        # 6. Rasterise (z-buffered — no painter's sort needed), one face chunk at
        #    a time into the shared image/z-buffer. The z-buffer makes chunk order
        #    irrelevant, so the result is identical to a single-pass render.
        # ------------------------------------------------------------------
        # The shader applies the albedo before adding the rim and specular terms,
        # so the rasteriser multiply is pure white — it just scales the absolute
        # [0, 1] colour up to 8-bit.
        base_color = np.array([255, 255, 255], dtype=np.float32)

        # Transparent background — floats cleanly on any card colour.
        img = np.zeros((ss_height, ss_width, 3), dtype=np.uint8)
        zbuf = np.full((ss_height, ss_width), np.inf, dtype=np.float64)

        if isinstance(rasterise, PreparedRasteriser):
            rasterise.draw_prepared(
                geometry, rotation, screen, ss_width, ss_height, matte
            )
        else:
            visible_total = start = 0
            for fc in geometry.face_chunks(chunk):
                end = start + len(fc)
                corner_n = geometry.corner_normals[start:end]
                start = end
                tri = screen[fc]  # (c, 3, 3) screen-space
                view_tri = view[fc]  # (c, 3, 3) view-space

                edge1 = view_tri[:, 1] - view_tri[:, 0]
                edge2 = view_tri[:, 2] - view_tri[:, 0]
                # Back-face cull: in right-handed view-space visible faces have raw
                # z<0; a negative-determinant (front-on flat) view flips that sign.
                raw_normals = np.cross(edge1, edge2)  # (c, 3)
                norm_len = np.linalg.norm(raw_normals, axis=1)  # (c,)

                # The depth camera looks along +Z. Orient normals toward it,
                # then reflect only Z into the lighting frame (camera at +Z).
                # Negating the whole vector here reverses left/right and up/down
                # lighting, making upward faces dark under an overhead key.
                # Per-corner normals are interpolated per pixel in the rasteriser (Phong); the
                # Fresnel rim, diffuse and specular are all evaluated there from the
                # interpolated normal, so there is no separate per-vertex colour pass.
                cvn = corner_n @ rot_T  # (c, 3, 3)
                cvn = np.where(cvn[..., 2:3] >= 0, cvn, -cvn)
                if studio:
                    cvn[..., :2] *= -1

                front = (raw_normals[:, 2] * view_handedness) < 0.0
                valid = front & (norm_len > 1e-8)
                tri = tri[valid]
                cvn = cvn[valid]
                visible_total += int(tri.shape[0])

                rasterise(img, zbuf, tri, cvn, shade, base_color, ss_width, ss_height)
                # Free this chunk's temporaries before the next one so only one
                # chunk's worth of per-face arrays is ever live. No gc.collect() here:
                # there are no reference cycles in the hot loop, and the per-file
                # The application mesh policy returns retired arenas to the OS.
                del view_tri, edge1, edge2, raw_normals, norm_len
                del corner_n, cvn, tri, valid

            if visible_total == 0:
                # Degenerate / fully back-facing mesh: paint every face flat so the
                # silhouette still reads, matching the single-pass fallback.
                if logger is not None:
                    logger.warning(
                        "mesh_render: no visible triangles for %s — using silhouette",
                        name,
                    )
                flat_color = (
                    shade(np.array([0.0, 0.0, 1.0]))
                    if studio
                    else np.asarray(albedo) * 0.6
                )

                def _flat_shade(
                    n: FloatArray,
                    _c: FloatArray = flat_color,
                ) -> FloatArray:
                    return np.broadcast_to(_c, n.shape)

                for fc in geometry.face_chunks(chunk):
                    tri = screen[fc]
                    nrm = np.zeros((tri.shape[0], 3, 3), dtype=np.float32)
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

        if isinstance(rasterise, DeferredRasteriser):
            rasterise.finish(img, zbuf)

        # Alpha = 255 wherever a triangle was painted, 0 elsewhere.
        alpha = np.where(zbuf < np.inf, np.uint8(255), np.uint8(0)).astype(np.uint8)
        rgba = np.dstack([img, alpha])

        # ------------------------------------------------------------------
        # 7. Post-process: Lanczos downsample (anti-aliasing) + subtle vignette.
        # ------------------------------------------------------------------
        return postprocess_rgba(rgba.tobytes(), width, height, profile=profile)

    except Exception:
        if logger is not None:
            logger.warning(
                "mesh_render: render_thumbnail failed for %s", name, exc_info=True
            )
        return None


def postprocess_rgba(
    rgba: bytes, width: int, height: int, *, profile: PreviewProfile = PREVIEW_PROFILE
) -> RenderedPixels:
    """Apply the same canonical downsample/vignette to CPU or GPU raster pixels."""
    import numpy as np
    from PIL import Image  # pyright: ignore[reportMissingTypeStubs]

    supersample = profile.supersample_for(width)
    ss_width, ss_height = width * supersample, height * supersample
    pil = Image.frombytes("RGBA", (ss_width, ss_height), rgba)
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

    return RenderedPixels(width, height, pil.tobytes())


# ---------------------------------------------------------------------------
# View selection helpers
# ---------------------------------------------------------------------------


def _select_view_rotation(verts: FloatArray, *, reference: bool = False) -> FloatArray:
    """Return an original-space -> view-space matrix for thumbnail rendering."""
    import numpy as np

    extents = verts.max(axis=0) - verts.min(axis=0)
    thin_axis = int(np.argmin(extents))
    thin_extent = float(extents[thin_axis])
    other_extents = np.delete(extents, thin_axis)
    broad_extent = float(np.max(other_extents) if reference else np.min(other_extents))
    profile = REFERENCE_THUMBNAIL_PROFILE if reference else PREVIEW_PROFILE

    # A plate is thin relative to both other axes; rods are not plates.
    if (
        broad_extent > 1e-6
        and thin_extent / broad_extent <= profile.flat_thickness_ratio
    ):
        return _front_rotation_for_thin_axis(thin_axis, reference=reference)

    # "Hero" 3/4 view for a solid model. 3D-print models are Z-up (they sit
    # flat-based on the bed), so we keep Z as screen-up and look from the
    # front-left tilted 30° down — the way the interactive 3D viewer frames a
    # model. The old view stared 30° *down the Z axis*, which showed the top of
    # an upright model (e.g. the gathered top of a dumpling) instead of its face.
    azim = np.radians(profile.hero_azimuth_degrees)
    # View-space rotation is opposite the positive camera elevation.
    tilt = np.radians(-profile.hero_elevation_degrees)
    ca, sa = np.cos(azim), np.sin(azim)
    ct, st = np.cos(tilt), np.sin(tilt)
    spin_z = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]], dtype=np.float64)
    # Base: camera on -Y looking toward +Y, with object Z mapped to screen-up.
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    tilt_x = np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]], dtype=np.float64)
    return tilt_x @ base @ spin_z


def _front_rotation_for_thin_axis(
    thin_axis: int, *, reference: bool = False
) -> FloatArray:
    """View a flat mesh mostly face-on but tilted 25° to reveal depth in recesses."""
    import numpy as np

    # 25° tilt rotation around the screen-X axis (tips the model top toward camera).
    profile = REFERENCE_THUMBNAIL_PROFILE if reference else PREVIEW_PROFILE
    tilt = np.radians(profile.flat_tilt_degrees)
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
        # Keep flat parts on the same diagonal as the hero view without
        # tipping standing signs or lithophanes sideways.
        if not reference:
            azimuth = np.radians(PREVIEW_PROFILE.hero_azimuth_degrees)
            ca, sa = np.cos(azimuth), np.sin(azimuth)
            base = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]]) @ base

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
    [0, 1]. Each triangle's candidate pixels are the spans its rows cover (its
    clipped bounding box under a ``budget``); the three corner normals are
    interpolated by the same barycentric weights used for the inside test,
    renormalised, then lit per
    fragment — so the surface shades smoothly without the Gouraud colour banding
    and pole "fan" streaks that interpolating a precomputed colour produced.
    Depth interpolation and per-pixel z-buffer resolution run as whole-array
    numpy operations.
    """
    import numpy as np

    if tri.shape[0] == 0:
        return 0

    if budget is None and len(tri) > 1:
        # Identical oriented triangles cover the same pixels at identical depths.
        # Depth ties already keep the first fragment, including its normals. Keep
        # that input order while avoiding repeated candidate expansion. This is
        # local to the caller's face chunk; budgeted fallback retains its existing
        # candidate accounting and coverage policy.
        _, first = np.unique(tri.reshape(len(tri), -1), axis=0, return_index=True)
        if len(first) != len(tri):
            first.sort()
            tri, vert_nrm = tri[first], vert_nrm[first]

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
    batches = (
        _span_batches(x0, x1, y0, y1, v0, v1, v2)
        if budget is None
        else _box_batches(x0, y0, bbox_w, bbox_h, areas, budget)
    )
    for source_idx, pix_x, pix_y in batches:
        candidates += len(source_idx)
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


def _box_batches(
    x0: IntArray,
    y0: IntArray,
    widths: IntArray,
    heights: IntArray,
    areas: IntArray,
    budget: RasterBudget | None,
) -> Iterator[tuple[IntArray, IntArray, IntArray]]:
    """Every pixel of each face's bounding box, as (face, x, y) candidates."""
    import numpy as np

    for (
        source_faces,
        candidate_x0,
        candidate_y0,
        candidate_width,
        counts,
        pixel_offset,
    ) in _pixel_batches(x0, y0, widths, heights, areas, budget):
        tri_idx = np.repeat(np.arange(len(source_faces)), counts)
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
        offsets = (
            pixel_offset + np.arange(int(counts.sum())) - np.repeat(starts, counts)
        )
        w_per_tri = candidate_width[tri_idx]
        yield (
            source_faces[tri_idx],
            candidate_x0[tri_idx] + offsets % w_per_tri,
            candidate_y0[tri_idx] + offsets // w_per_tri,
        )


# Rows of faces handled at once while finding spans; bounds the row arrays.
_SPAN_ROW_BUDGET = 125_000


def _span_batches(
    x0: IntArray,
    x1: IntArray,
    y0: IntArray,
    y1: IntArray,
    v0: FloatArray,
    v1: FloatArray,
    v2: FloatArray,
) -> Iterator[tuple[IntArray, IntArray, IntArray]]:
    """Candidate pixels of each face, limited to the spans its rows cover.

    A face's bounding box holds every pixel it can cover, but a long thin face
    covers a sliver of it. Each row's span is where the row's centre line
    crosses the face, widened by a pixel on each side so the barycentric inside
    test still decides every fragment: the fragments are the bounding box's,
    in the same face and row order, without generating the rejected ones.
    """
    import numpy as np

    heights = y1 - y0 + 1
    row_ends = np.cumsum(heights)
    face_start = 0
    while face_start < len(heights):
        consumed = int(row_ends[face_start - 1]) if face_start else 0
        face_end = max(
            face_start + 1,
            int(np.searchsorted(row_ends, consumed + _SPAN_ROW_BUDGET, side="right")),
        )
        face_heights = heights[face_start:face_end]
        face = np.repeat(np.arange(face_start, face_end), face_heights)
        first_rows = np.repeat(np.cumsum(face_heights) - face_heights, face_heights)
        row = y0[face] + np.arange(len(face)) - first_rows
        centre = row + 0.5
        low = np.full(len(face), np.inf)
        high = np.full(len(face), -np.inf)
        for start, end in ((v0, v1), (v1, v2), (v2, v0)):
            ax, ay = start[face, 0], start[face, 1]
            bx, by = end[face, 0], end[face, 1]
            rise = by - ay
            sloped = rise != 0
            t = (centre - ay) / np.where(sloped, rise, 1.0)
            crosses = sloped & (t >= 0) & (t <= 1)
            x = ax + t * (bx - ax)
            low = np.where(crosses, np.minimum(low, x), low)
            high = np.where(crosses, np.maximum(high, x), high)
            level = ~sloped & (ay == centre)
            low = np.where(level, np.minimum(low, np.minimum(ax, bx)), low)
            high = np.where(level, np.maximum(high, np.maximum(ax, bx)), high)
        covered = np.isfinite(low)
        left = np.maximum(
            np.ceil(np.where(covered, low, 0.0) - 0.5).astype(np.int64) - 1, x0[face]
        )
        right = np.minimum(
            np.floor(np.where(covered, high, 0.0) - 0.5).astype(np.int64) + 1, x1[face]
        )
        counts = np.where(covered, np.maximum(right - left + 1, 0), 0)
        pixel_ends = np.cumsum(counts)
        row_start = 0
        while row_start < len(counts):
            done = int(pixel_ends[row_start - 1]) if row_start else 0
            row_end = max(
                row_start + 1,
                int(
                    np.searchsorted(
                        pixel_ends, done + _CHUNK_PIXEL_BUDGET, side="right"
                    )
                ),
            )
            chunk = counts[row_start:row_end]
            rows = np.repeat(np.arange(row_start, row_end), chunk)
            if len(rows):
                firsts = np.repeat(np.cumsum(chunk) - chunk, chunk)
                yield (
                    face[rows],
                    left[rows] + np.arange(len(rows)) - firsts,
                    row[rows],
                )
            row_start = row_end
        face_start = face_end


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
