"""Bounded immutable array frames with distinct canonical/deferred normal tags."""

import json
import struct

import numpy as np
from printstash_core.mesh.render_geometry import PreparedRender, PreparedRenderGeometry

from .compute_geometry_wire import (
    CANONICAL_MODE,
    MAX_GEOMETRY_BYTES,
    NORMAL_MODE,
    VISUAL_NORMAL_MODE,
    GeometryLayout,
    _layout,
    _validate_layout,
    descriptor,
    identity,
)
from .compute_geometry_wire import (
    body_size as body_size,
)
from .gpu_normals import MAX_ABS_COORDINATE, DeferredRender, RenderInput, VisualRender


def layout_for(prepared: RenderInput) -> GeometryLayout:
    return GeometryLayout(
        (
            len(prepared.vertices),
            prepared.face_count,
            prepared.position_count
            if isinstance(prepared, (DeferredRender, VisualRender))
            else len(prepared.smooth_normals),
        ),
        prepared.normal_mode
        if isinstance(prepared, (DeferredRender, VisualRender))
        else CANONICAL_MODE,
    )


def encode(prepared: RenderInput, width: int, height: int, views, matte: bool) -> bytes:
    layout = layout_for(prepared)
    _validate_layout(layout)
    deferred = isinstance(prepared, (DeferredRender, VisualRender))
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
    ]
    if not isinstance(prepared, VisualRender):
        arrays.append(
            np.ascontiguousarray(
                prepared.position_ids, dtype="<u4" if deferred else "<i8"
            )
        )
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
        header["normal_mode"] = prepared.normal_mode
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
    descriptor_value, counts = descriptor(header)
    views = []
    for value in descriptor_value["views"]:
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
    return (
        counts,
        descriptor_value["width"],
        descriptor_value["height"],
        views,
        descriptor_value["matte"],
    )


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
    if layout.normal_mode == VISUAL_NORMAL_MODE:
        sizes, dtypes, shapes = (n * 12, f * 12), ("<f4", "<u4"), ((n, 3), (f, 3))
    if sum(sizes) != len(body):
        raise ValueError("compute_geometry_size")
    arrays = []
    offset = 0
    for size, dtype, shape in zip(sizes, dtypes, shapes, strict=True):
        arrays.append(
            np.frombuffer(body[offset : offset + size], dtype=dtype).reshape(shape)
        )
        offset += size
    vertices, faces = arrays[:2]
    identities = arrays[2] if len(arrays) > 2 else None
    if (
        not np.isfinite(vertices).all()
        or faces.min() < 0
        or faces.max() >= n
        or (identities is not None and (identities.min() < 0 or identities.max() >= k))
    ):
        raise ValueError("compute_geometry_invalid")

    def chunks(size):
        if type(size) is not int or size < 1:
            raise ValueError("invalid_render_chunk")
        for start in range(0, f, size):
            yield faces[start : start + size]

    if layout.normal_mode == VISUAL_NORMAL_MODE:
        if np.max(np.abs(vertices)) > MAX_ABS_COORDINATE:
            raise ValueError("compute_normals_numeric_range")
        return VisualRender(PreparedRenderGeometry(vertices, f, chunks))
    assert identities is not None
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


def split(payload: bytes) -> tuple[str, str, bytes]:
    length = struct.unpack("!I", payload[:4])[0]
    header = payload[4 : 4 + length]
    counts, *_ = decode_header(header)
    body = payload[4 + length :]
    return identity(counts, body), header.decode(), body
