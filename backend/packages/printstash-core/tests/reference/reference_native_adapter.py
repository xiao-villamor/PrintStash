"""Frozen pre-migration stages, used only to compare native results in tests."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from reference_rasterizer import (
        FloatArray,
        IntArray,
        PreparedMesh,
        Shade,
        UInt8Array,
    )


# Bound interpolation and lighting temporaries independently of preview size.
_SHADE_BATCH_PIXELS = 16_384


class FrameKernel(Protocol):
    def draw_phong(
        self,
        triangles: bytes,
        itemsize: int,
        normals: bytes,
        normal_itemsize: int,
        lighting: tuple[float, ...],
    ) -> int: ...

    def draw_flat(
        self, triangles: bytes, itemsize: int, color: tuple[int, ...]
    ) -> int: ...

    def rgba(self) -> bytes: ...


class Kernel(Protocol):
    def render_preview(
        self,
        vertices: bytes,
        faces: bytes,
        width: int,
        height: int,
        chunk: int,
        format: str,
        recipe: tuple[float, ...],
        supersampling: tuple[int, int, int],
        rotation: list[list[float]] | None,
        matte: bool,
    ) -> tuple[bytes, tuple[float, ...]]: ...

    def render_views(self, *args: Any) -> list[bytes]: ...

    def render_stl_fallback(self, *args: Any) -> tuple[Any, ...]: ...

    def render_stl_streaming(self, *args: Any) -> tuple[Any, ...]: ...

    def PreparedPreview(
        self, vertices: bytes, faces: bytes, chunk: int
    ) -> PreparedMesh: ...

    def NativeFrame(self, width: int, height: int) -> FrameKernel: ...

    def smooth_normals(
        self,
        vertices: bytes,
        faces: bytes,
        positions: bytes,
        position_count: int,
        chunk_size: int,
    ) -> bytes: ...

    def rasterize(
        self, triangles: bytes, itemsize: int, depth: bytes, width: int, height: int
    ) -> tuple[bytes, int]: ...

    def shade_fragments(
        self,
        records: bytes,
        triangles: bytes,
        itemsize: int,
        normals: bytes,
        normal_itemsize: int,
        width: int,
        height: int,
        lighting: tuple[float, ...],
    ) -> bytes: ...

    def rasterize_phong(
        self,
        triangles: bytes,
        itemsize: int,
        depth: bytes,
        normals: bytes,
        normal_itemsize: int,
        width: int,
        height: int,
        lighting: tuple[float, ...],
    ) -> tuple[bytes, int]: ...


def kernel() -> Kernel:
    return cast(Kernel, importlib.import_module("printstash_mesh_native"))


class NativeFrame:
    """Keep frame storage native until the final RGBA transfer."""

    def __init__(self, width: int, height: int) -> None:
        native = kernel()
        self._frame = native.NativeFrame(width, height)

    def draw(
        self, tri: FloatArray, normals: FloatArray, shade: Shade, base_color: FloatArray
    ) -> None:
        import numpy as np
        from reference_rasterizer import PhongShader

        if isinstance(shade, PhongShader):
            self._frame.draw_phong(
                tri.tobytes(),
                tri.dtype.itemsize,
                normals.tobytes(),
                normals.dtype.itemsize,
                shade.parameters + tuple(float(v) for v in base_color),
            )
        else:
            # The core's silhouette fallback has a constant material color.
            color = np.clip(base_color * shade(np.zeros((1, 3))), 0, 255)
            self._frame.draw_flat(
                tri.tobytes(), tri.dtype.itemsize, tuple(int(v) for v in color.ravel())
            )

    def rgba(self) -> bytes:
        return self._frame.rgba()


def rasterise_triangles(
    img: UInt8Array,
    zbuf: FloatArray,
    tri: FloatArray,
    vert_nrm: FloatArray,
    shade: Shade,
    base_color: FloatArray,
    width: int,
    height: int,
) -> int:
    import numpy as np
    from reference_rasterizer import PhongShader

    native = kernel()
    if (
        tri.ndim != 3
        or tri.shape[1:] != (3, 3)
        or tri.dtype not in (np.dtype(np.float32), np.dtype(np.float64))
        or vert_nrm.shape != tri.shape
        or img.shape != (height, width, 3)
        or zbuf.shape != (height, width)
        or zbuf.dtype != np.dtype(np.float64)
    ):
        raise ValueError("invalid native rasterizer arrays")
    triangles = tri.tobytes()
    if isinstance(shade, PhongShader) and vert_nrm.dtype in (
        np.dtype(np.float32),
        np.dtype(np.float64),
    ):
        payload, candidates = native.rasterize_phong(
            triangles,
            tri.dtype.itemsize,
            zbuf.tobytes(),
            vert_nrm.tobytes(),
            vert_nrm.dtype.itemsize,
            width,
            height,
            shade.parameters + tuple(float(value) for value in base_color),
        )
        # No face IDs or per-pixel normal arrays cross the native boundary.
        fragments = np.frombuffer(
            payload,
            dtype=np.dtype(
                [
                    ("pixel", "=u4"),
                    ("depth", "=f8"),
                    ("color", "u1", (3,)),
                ]
            ),
        )
        for start in range(0, len(fragments), _SHADE_BATCH_PIXELS):
            batch = fragments[start : start + _SHADE_BATCH_PIXELS]
            y, x = batch["pixel"] // width, batch["pixel"] % width
            zbuf[y, x] = batch["depth"]
            img[y, x] = batch["color"]
        return candidates
    payload, candidates = native.rasterize(
        triangles, tri.dtype.itemsize, zbuf.tobytes(), width, height
    )
    records = np.frombuffer(payload, dtype=np.uint64).reshape(-1, 3)
    if len(records) == 0:
        return candidates
    # Only the final RGB bytes scale with visible pixels. Interpolation and the
    # lighting callback work on bounded batches, with no whole-image gathers.
    if isinstance(shade, PhongShader) and vert_nrm.dtype in (
        np.dtype(np.float32),
        np.dtype(np.float64),
    ):
        # Rust allocates only the final RGB bytes, with constant per-pixel scratch.
        # The Python callback remains authoritative for custom shaders/old kernels.
        encoded = native.shade_fragments(
            payload,
            triangles,
            tri.dtype.itemsize,
            vert_nrm.tobytes(),
            vert_nrm.dtype.itemsize,
            width,
            height,
            shade.parameters + tuple(float(value) for value in base_color),
        )
        colors = np.frombuffer(encoded, dtype=np.uint8).reshape(-1, 3)
    else:
        colors = np.empty((len(records), 3), dtype=np.uint8)
        for start in range(0, len(records), _SHADE_BATCH_PIXELS):
            batch = records[start : start + _SHADE_BATCH_PIXELS]
            target, source = batch[:, 0], batch[:, 1]
            a, b, c = tri[source, 0], tri[source, 1], tri[source, 2]
            fx = target % width + 0.5
            fy = target // width + 0.5
            denom = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (
                a[:, 1] - c[:, 1]
            )
            w0 = (
                (b[:, 1] - c[:, 1]) * (fx - c[:, 0])
                + (c[:, 0] - b[:, 0]) * (fy - c[:, 1])
            ) / denom
            w1 = (
                (c[:, 1] - a[:, 1]) * (fx - c[:, 0])
                + (a[:, 0] - c[:, 0]) * (fy - c[:, 1])
            ) / denom
            w2 = 1.0 - w0 - w1
            vn = vert_nrm[source]
            n = w0[:, None] * vn[:, 0]
            n += w1[:, None] * vn[:, 1]
            n += w2[:, None] * vn[:, 2]
            nlen = np.linalg.norm(n, axis=1, keepdims=True)
            n /= np.where(nlen == 0, 1.0, nlen)
            colors[start : start + len(batch)] = np.clip(
                base_color * shade(n), 0, 255
            ).astype(np.uint8)
    # Commit only after every shading batch succeeds, including the last one.
    # Indexed writes also preserve non-contiguous framebuffer views.
    for start in range(0, len(records), _SHADE_BATCH_PIXELS):
        batch = records[start : start + _SHADE_BATCH_PIXELS]
        y, x = batch[:, 0] // width, batch[:, 0] % width
        zbuf[y, x] = batch[:, 2].view(np.float64)
        img[y, x] = colors[start : start + len(batch)]
    return candidates


def prepare_normals(
    vertices: FloatArray,
    faces: IntArray,
    positions: IntArray,
    position_count: int,
    chunk_size: int,
) -> FloatArray:
    import numpy as np

    native = kernel()
    payload = native.smooth_normals(
        vertices.tobytes(),
        faces.tobytes(),
        positions.astype(np.int64).tobytes(),
        position_count,
        chunk_size,
    )
    return np.frombuffer(payload, dtype=np.float64).reshape(-1, 3)


def prepare_mesh(
    vertices: FloatArray, faces: IntArray, chunk_size: int
) -> PreparedMesh:
    """Transfer the mesh once; Rust owns preparation and every drawing batch."""
    native = kernel()
    return native.PreparedPreview(vertices.tobytes(), faces.tobytes(), chunk_size)


def encode_preview(
    rgba: bytes, width: int, height: int, supersample: int, output_format: str
) -> bytes:
    """Resize and encode the owned preview through the optional native extension."""
    return kernel().process_image(
        rgba,
        width * supersample,
        height * supersample,
        width,
        height,
        "lanczos",
        True,
        True,
        output_format,
    )


def render_preview(
    mesh: Any,
    *,
    width: int = 640,
    height: int = 480,
    chunk: int = 64000,
    output_format: str = "PNG",
    view_rotation: Any = None,
    matte: bool = False,
) -> bytes:
    """Transport mesh buffers into one Rust job; return the encoded preview."""
    import numpy as np

    from printstash_core.mesh.preview_profile import PREVIEW_PROFILE as p

    native = kernel()
    image, _seconds = native.render_preview(
        np.asarray(mesh.vertices, dtype=np.float32).tobytes(),
        np.asarray(mesh.faces, dtype=np.int64).tobytes(),
        width,
        height,
        max(int(chunk), 1),
        output_format,
        (
            p.margin_fraction,
            p.hero_azimuth_degrees,
            p.hero_elevation_degrees,
            p.flat_tilt_degrees,
            p.flat_thickness_ratio,
            *p.material_albedo,
        ),
        (
            p.supersample_max_output_width,
            p.supersample_small_factor,
            p.supersample_large_factor,
        ),
        None
        if view_rotation is None
        else np.asarray(view_rotation, dtype=np.float64).tolist(),
        matte,
    )
    return image


def render_views(mesh: Any, width: int, height: int, frames: Any) -> list[bytes]:
    """Transfer one mesh for all inference views; Rust shares preparation."""
    import numpy as np

    from printstash_core.mesh.preview_profile import PREVIEW_PROFILE as p

    native = kernel()
    return native.render_views(
        np.asarray(mesh.vertices, dtype=np.float32).tobytes(),
        np.asarray(mesh.faces, dtype=np.int64).tobytes(),
        width,
        height,
        64000,
        (
            p.margin_fraction,
            p.hero_azimuth_degrees,
            p.hero_elevation_degrees,
            p.flat_tilt_degrees,
            p.flat_thickness_ratio,
            *p.material_albedo,
        ),
        (
            p.supersample_max_output_width,
            p.supersample_small_factor,
            p.supersample_large_factor,
        ),
        frames,
    )
