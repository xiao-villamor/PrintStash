"""Lightweight framing shared by the parser supervisor and the compute owner.

Array validation stays in the owner. This module imports no native numerical
libraries, so inspecting an owned staging packet does not enlarge the API.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Final, Literal, cast

MAX_GEOMETRY_BYTES = 48 * 1024**2
MAX_FACES = 2_000_000
MAX_VERTICES = 6_000_000
CANONICAL_MODE: Final = "canonical"
NORMAL_MODE: Final = "gpu-angle-csr-v1"
VISUAL_NORMAL_MODE: Final = "gpu-visual-v2"
NormalMode = Literal["canonical", "gpu-angle-csr-v1", "gpu-visual-v2"]


@dataclass(frozen=True)
class GeometryLayout:
    counts: tuple[int, int, int]
    normal_mode: NormalMode = CANONICAL_MODE

    def __iter__(self):
        return iter(self.counts)


def body_size(layout: GeometryLayout | tuple[int, int, int]) -> int:
    layout = _layout(layout)
    n, f, k = layout
    if layout.normal_mode == VISUAL_NORMAL_MODE:
        return n * 12 + f * 12
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
        layout.normal_mode not in (CANONICAL_MODE, NORMAL_MODE, VISUAL_NORMAL_MODE)
        or not all(type(v) is int and v > 0 for v in (n, f, k))
        or n > MAX_VERTICES
        or f > MAX_FACES
        or k > n
        or (layout.normal_mode == VISUAL_NORMAL_MODE and k != n)
    ):
        raise ValueError("compute_geometry_shape")
    # A smaller deferred encoding must not silently enlarge source admission.
    if (
        n * 20 + f * 24 + k * 24 > MAX_GEOMETRY_BYTES
        or body_size(layout) > MAX_GEOMETRY_BYTES
    ):
        raise ValueError("compute_geometry_budget")


def descriptor(header: bytes) -> tuple[dict, GeometryLayout]:
    if len(header) > 4096:
        raise ValueError("compute_geometry_header")
    value = json.loads(header)
    fields = {"vertices", "faces", "normals", "width", "height", "views", "matte"}
    if not isinstance(value, dict) or set(value) not in (
        fields,
        fields | {"normal_mode"},
    ):
        raise ValueError("compute_geometry_header")
    if "normal_mode" in value and value["normal_mode"] not in (
        NORMAL_MODE,
        VISUAL_NORMAL_MODE,
    ):
        raise ValueError("compute_geometry_normal_mode")
    counts = GeometryLayout(
        (value["vertices"], value["faces"], value["normals"]),
        cast(NormalMode, value.get("normal_mode", CANONICAL_MODE)),
    )
    _validate_layout(counts)
    if any(
        type(value[name]) is not int or not 1 <= value[name] <= 1280
        for name in ("width", "height")
    ):
        raise ValueError("compute_frame_budget")
    if (
        type(value["matte"]) is not bool
        or not isinstance(value["views"], list)
        or not 1 <= len(value["views"]) <= 8
    ):
        raise ValueError("compute_camera_budget")
    return value, counts


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


@dataclass(frozen=True)
class ThumbnailWire:
    geometry_key: str
    header: str
    body: memoryview
    width: int
    height: int
    face_count: int


def split_thumbnail(payload: bytes | memoryview) -> ThumbnailWire:
    """Validate a single default-camera staging frame without copying its arrays.

    The caller owns the sealed file/buffer until the exchange completes. The
    receiving owner independently validates every coordinate and array index.
    """
    view = memoryview(payload)
    if view.ndim != 1 or view.itemsize != 1 or not view.c_contiguous:
        raise ValueError("compute_geometry_invalid")
    if len(view) < 4 or len(view) > MAX_GEOMETRY_BYTES + 4096:
        raise ValueError("compute_geometry_budget")
    length = struct.unpack_from("!I", view)[0]
    if length > 4096 or length + 4 > len(view):
        raise ValueError("compute_geometry_header")
    header = bytes(view[4 : 4 + length])
    value, layout = descriptor(header)
    if value["views"] != [None] or value["matte"]:
        raise ValueError("compute_camera_invalid")
    body = view[4 + length :]
    if len(body) != body_size(layout):
        raise ValueError("compute_geometry_size")
    return ThumbnailWire(
        identity(layout, body),
        header.decode(),
        body,
        value["width"],
        value["height"],
        layout.counts[1],
    )
