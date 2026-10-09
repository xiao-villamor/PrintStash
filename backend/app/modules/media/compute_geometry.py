"""Bounded immutable array frames for prepared geometry; no archive or pickle codec."""

import json
import struct

import numpy as np
from printstash_core.mesh.render_geometry import PreparedRender

MAX_GEOMETRY_BYTES = 48 * 1024**2
MAX_FACES = 2_000_000
MAX_VERTICES = 6_000_000


def encode(
    prepared: PreparedRender, width: int, height: int, views, matte: bool
) -> bytes:
    sizes = (
        prepared.vertices.nbytes,
        prepared.face_count * 3 * 8,
        prepared.position_ids.nbytes,
        prepared.smooth_normals.nbytes,
    )
    if sum(sizes) > MAX_GEOMETRY_BYTES:
        raise ValueError("compute_geometry_budget")
    faces = np.concatenate(tuple(prepared.face_chunks(64000)))
    arrays = (
        np.ascontiguousarray(prepared.vertices, dtype="<f4"),
        np.ascontiguousarray(faces, dtype="<i8"),
        np.ascontiguousarray(prepared.position_ids, dtype="<i8"),
        np.ascontiguousarray(prepared.smooth_normals, dtype="<f8"),
    )
    header = json.dumps(
        {
            "vertices": len(arrays[0]),
            "faces": len(faces),
            "normals": len(arrays[3]),
            "width": width,
            "height": height,
            "views": [
                None if view is None else np.asarray(view).tolist() for view in views
            ],
            "matte": matte,
        },
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    return (
        struct.pack("!I", len(header))
        + header
        + b"".join(array.tobytes() for array in arrays)
    )


def decode(payload: bytes):
    if len(payload) < 4 or len(payload) > MAX_GEOMETRY_BYTES + 4096:
        raise ValueError("compute_geometry_budget")
    length = struct.unpack("!I", payload[:4])[0]
    if length > 4096:
        raise ValueError("compute_geometry_header")
    header = json.loads(payload[4 : 4 + length])
    if set(header) != {
        "vertices",
        "faces",
        "normals",
        "width",
        "height",
        "views",
        "matte",
    }:
        raise ValueError("compute_geometry_header")
    n, f, k = (header[name] for name in ("vertices", "faces", "normals"))
    if (
        not all(type(v) is int and v > 0 for v in (n, f, k))
        or n > MAX_VERTICES
        or f > MAX_FACES
        or k > n
    ):
        raise ValueError("compute_geometry_shape")
    if any(
        type(header[name]) is not int or not 1 <= header[name] <= 1280
        for name in ("width", "height")
    ):
        raise ValueError("compute_frame_budget")
    if (
        type(header["matte"]) is not bool
        or not isinstance(header["views"], list)
        or not 1 <= len(header["views"]) <= 8
    ):
        raise ValueError("compute_camera_budget")
    body = memoryview(payload)[4 + length :]
    sizes = (n * 12, f * 24, n * 8, k * 24)
    if sum(sizes) != len(body):
        raise ValueError("compute_geometry_size")
    arrays = []
    offset = 0
    for size, dtype, shape in zip(
        sizes, ("<f4", "<i8", "<i8", "<f8"), ((n, 3), (f, 3), (n,), (k, 3)), strict=True
    ):
        arrays.append(
            np.frombuffer(body[offset : offset + size], dtype=dtype).reshape(shape)
        )
        offset += size
    vertices, faces, identities, normals = arrays
    if (
        not np.isfinite(vertices).all()
        or not np.isfinite(normals).all()
        or faces.min() < 0
        or faces.max() >= n
        or identities.min() < 0
        or identities.max() >= k
    ):
        raise ValueError("compute_geometry_invalid")
    views = []
    for value in header["views"]:
        view = None if value is None else np.asarray(value, dtype=np.float64)
        if view is not None and (
            view.shape != (3, 3)
            or not np.isfinite(view).all()
            or not np.allclose(view @ view.T, np.eye(3), atol=1e-6)
        ):
            raise ValueError("compute_camera_invalid")
        views.append(view)

    def chunks(size):
        for start in range(0, f, size):
            yield faces[start : start + size]

    prepared = PreparedRender(vertices, f, chunks, identities, normals)
    return prepared, header["width"], header["height"], views, header["matte"]
