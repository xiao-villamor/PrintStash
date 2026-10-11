"""Bounded immutable array frames with distinct canonical/deferred normal tags."""

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
from printstash_core.mesh.render_geometry import PreparedRender, PreparedRenderGeometry

from .gpu_normals import NORMAL_MODE, DeferredRender, RenderInput

MAX_GEOMETRY_BYTES = 48 * 1024**2
MAX_FACES = 2_000_000
MAX_VERTICES = 6_000_000
CANONICAL_MODE: Final = "canonical"
NormalMode = Literal["canonical", "gpu-angle-csr-v1"]


@dataclass(frozen=True)
class GeometryLayout:
    counts: tuple[int, int, int]
    normal_mode: NormalMode = CANONICAL_MODE

    def __iter__(self):
        return iter(self.counts)


def layout_for(prepared: RenderInput) -> GeometryLayout:
    return GeometryLayout(
        (
            len(prepared.vertices),
            prepared.face_count,
            prepared.position_count
            if isinstance(prepared, DeferredRender)
            else len(prepared.smooth_normals),
        ),
        NORMAL_MODE if isinstance(prepared, DeferredRender) else CANONICAL_MODE,
    )


def body_size(layout: GeometryLayout | tuple[int, int, int]) -> int:
    layout = _layout(layout)
    n, f, k = layout
    return (
        n * 16 + f * 12
        if layout.normal_mode == NORMAL_MODE
        else n * 20 + f * 24 + k * 24
    )


def _layout(value: GeometryLayout | tuple[int, int, int]) -> GeometryLayout:
    return value if isinstance(value, GeometryLayout) else GeometryLayout(value)


def _validate_layout(layout: GeometryLayout) -> None:
    n, f, k = layout
    if (
        layout.normal_mode not in (CANONICAL_MODE, NORMAL_MODE)
        or not all(type(v) is int and v > 0 for v in (n, f, k))
        or n > MAX_VERTICES
        or f > MAX_FACES
        or k > n
    ):
        raise ValueError("compute_geometry_shape")
    # A smaller deferred encoding must not silently enlarge source admission.
    if (
        n * 20 + f * 24 + k * 24 > MAX_GEOMETRY_BYTES
        or body_size(layout) > MAX_GEOMETRY_BYTES
    ):
        raise ValueError("compute_geometry_budget")


def encode(prepared: RenderInput, width: int, height: int, views, matte: bool) -> bytes:
    layout = layout_for(prepared)
    _validate_layout(layout)
    deferred = isinstance(prepared, DeferredRender)
    faces = np.empty((prepared.face_count, 3), dtype="<u4" if deferred else "<i8")
    offset = 0
    for chunk in prepared.face_chunks(64_000):
        end = offset + len(chunk)
        if (
            chunk.ndim != 2
            or chunk.shape[1] != 3
            or chunk.dtype.kind not in "iu"
            or end > len(faces)
            or not len(chunk)
            or chunk.min() < 0
            or chunk.max() >= len(prepared.vertices)
        ):
            raise ValueError("compute_geometry_invalid")
        faces[offset:end] = chunk
        offset = end
    if offset != len(faces):
        raise ValueError("compute_geometry_size")
    arrays = [
        np.ascontiguousarray(prepared.vertices, dtype="<f4"),
        faces,
        np.ascontiguousarray(prepared.position_ids, dtype="<u4" if deferred else "<i8"),
    ]
    if not deferred:
        assert isinstance(prepared, PreparedRender)
        arrays.append(np.ascontiguousarray(prepared.smooth_normals, dtype="<f8"))
    n, f, k = layout
    header = {
        "vertices": n,
        "faces": f,
        "normals": k,
        "width": width,
        "height": height,
        "views": [
            None if view is None else np.asarray(view).tolist() for view in views
        ],
        "matte": matte,
    }
    if deferred:
        header["normal_mode"] = NORMAL_MODE
    encoded_header = json.dumps(header, allow_nan=False, separators=(",", ":")).encode()
    return (
        struct.pack("!I", len(encoded_header))
        + encoded_header
        + b"".join(array.tobytes() for array in arrays)
    )


def decode(payload: bytes):
    if len(payload) < 4 or len(payload) > MAX_GEOMETRY_BYTES + 4096:
        raise ValueError("compute_geometry_budget")
    length = struct.unpack("!I", payload[:4])[0]
    if length > 4096:
        raise ValueError("compute_geometry_header")
    header = payload[4 : 4 + length]
    counts, width, height, views, matte = decode_header(header)
    prepared = decode_arrays(memoryview(payload)[4 + length :], counts)
    return prepared, width, height, views, matte


def decode_header(header: bytes):
    if len(header) > 4096:
        raise ValueError("compute_geometry_header")
    descriptor = json.loads(header)
    fields = {"vertices", "faces", "normals", "width", "height", "views", "matte"}
    if not isinstance(descriptor, dict) or set(descriptor) not in (
        fields,
        fields | {"normal_mode"},
    ):
        raise ValueError("compute_geometry_header")
    if "normal_mode" in descriptor and descriptor["normal_mode"] != NORMAL_MODE:
        raise ValueError("compute_geometry_normal_mode")
    normal_mode: NormalMode = (
        NORMAL_MODE if "normal_mode" in descriptor else CANONICAL_MODE
    )
    counts = GeometryLayout(
        tuple(descriptor[name] for name in ("vertices", "faces", "normals")),
        normal_mode,
    )
    _validate_layout(counts)
    if any(
        type(descriptor[name]) is not int or not 1 <= descriptor[name] <= 1280
        for name in ("width", "height")
    ):
        raise ValueError("compute_frame_budget")
    if (
        type(descriptor["matte"]) is not bool
        or not isinstance(descriptor["views"], list)
        or not 1 <= len(descriptor["views"]) <= 8
    ):
        raise ValueError("compute_camera_budget")
    views = []
    for value in descriptor["views"]:
        try:
            view = None if value is None else np.asarray(value, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError("compute_camera_invalid") from exc
        if view is not None and (
            view.shape != (3, 3)
            or not np.isfinite(view).all()
            or not np.allclose(view @ view.T, np.eye(3), atol=1e-6)
        ):
            raise ValueError("compute_camera_invalid")
        views.append(view)
    return counts, descriptor["width"], descriptor["height"], views, descriptor["matte"]


def decode_arrays(
    body: bytes | memoryview, counts: GeometryLayout | tuple[int, int, int]
) -> RenderInput:
    body = memoryview(body)
    if not body.readonly or not isinstance(body.obj, bytes):
        raise ValueError("compute_geometry_mutable")
    layout = _layout(counts)
    _validate_layout(layout)
    n, f, k = layout
    deferred = layout.normal_mode == NORMAL_MODE
    sizes = (n * 12, f * 12, n * 4) if deferred else (n * 12, f * 24, n * 8, k * 24)
    dtypes = ("<f4", "<u4", "<u4") if deferred else ("<f4", "<i8", "<i8", "<f8")
    shapes = ((n, 3), (f, 3), (n,)) if deferred else ((n, 3), (f, 3), (n,), (k, 3))
    if sum(sizes) != len(body):
        raise ValueError("compute_geometry_size")
    arrays = []
    offset = 0
    for size, dtype, shape in zip(sizes, dtypes, shapes, strict=True):
        arrays.append(
            np.frombuffer(body[offset : offset + size], dtype=dtype).reshape(shape)
        )
        offset += size
    vertices, faces, identities = arrays[:3]
    if (
        not np.isfinite(vertices).all()
        or faces.min() < 0
        or faces.max() >= n
        or identities.min() < 0
        or identities.max() >= k
    ):
        raise ValueError("compute_geometry_invalid")

    def chunks(size):
        if type(size) is not int or size < 1:
            raise ValueError("invalid_render_chunk")
        for start in range(0, f, size):
            yield faces[start : start + size]

    if deferred:
        if identities.max() != k - 1:
            raise ValueError("compute_geometry_invalid")
        return DeferredRender(
            PreparedRenderGeometry(vertices, f, chunks), identities, k
        )
    normals = arrays[3]
    if not np.isfinite(normals).all():
        raise ValueError("compute_geometry_invalid")
    return PreparedRender(vertices, f, chunks, identities, normals)


def identity(
    counts: GeometryLayout | tuple[int, int, int], body: bytes | memoryview
) -> str:
    from printstash_core.mesh.preview_profile import RASTERIZER_RECIPE

    layout = _layout(counts)
    prefix = RASTERIZER_RECIPE.encode()
    if layout.normal_mode != CANONICAL_MODE:
        prefix += b"\x00" + layout.normal_mode.encode() + b"\x00"
    digest = hashlib.sha256(prefix + struct.pack("!III", *layout.counts))
    digest.update(body)
    return digest.hexdigest()


def split(payload: bytes) -> tuple[str, str, bytes]:
    length = struct.unpack("!I", payload[:4])[0]
    header = payload[4 : 4 + length]
    counts, *_ = decode_header(header)
    body = payload[4 + length :]
    return identity(counts, body), header.decode(), body
