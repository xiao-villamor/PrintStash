"""Allocation formulas shared by CPU-only admission and the GPU owner.

These helpers import no array or GPU libraries. Counts describe validated render
arrays; they never infer authoritative geometry from a visual allocation.
"""

from .compute_geometry_wire import CANONICAL_MODE, NORMAL_MODE, VISUAL_NORMAL_MODE

FRAME_BYTES = 112
FRAME_STRIDE = 256
MAX_FRAMES = 8
VIEW_WORKSPACE_BYTES = 2 * FRAME_STRIDE * MAX_FRAMES
BOUND_BYTES = 32


def bounds_scratch_sizes(vertex_count: int) -> tuple[int, ...]:
    sizes = []
    count = vertex_count
    while count > 64:
        count = (count + 63) // 64
        sizes.append(count * BOUND_BYTES)
    return tuple(sizes)


def geometry_sizes(vertices: int, faces: int, positions: int) -> tuple[int, ...]:
    return vertices * 12, faces * 12, vertices * 4, positions * 12, BOUND_BYTES


def upload_bytes(vertices: int, faces: int, positions: int, mode: str) -> int:
    size = vertices * 12 + faces * 12
    if mode != VISUAL_NORMAL_MODE:
        size += vertices * 4
        if mode == CANONICAL_MODE:
            size += positions * 12
    return size


def normal_scratch_sizes(
    vertices: int, faces: int, positions: int, mode: str
) -> tuple[int, ...]:
    if mode == VISUAL_NORMAL_MODE:
        table = 1 << (2 * vertices - 1).bit_length()
        return table * 4, table * 12, table * 4, 4
    if mode == NORMAL_MODE:
        # Legacy provider owns a temporary smooth output, copied into the cache.
        return (positions + 1) * 4, faces * 12, faces * 24, positions * 12, 4
    return ()


def transient_sizes(
    vertices: int, faces: int, positions: int, mode: str
) -> tuple[int, ...]:
    return (
        *bounds_scratch_sizes(vertices),
        *normal_scratch_sizes(vertices, faces, positions, mode),
    )


def device_geometry_bytes(vertices: int, faces: int, positions: int, mode: str) -> int:
    """New geometry, transient scratch and its share of grouped staging.

    Add frame_count*FRAME_STRIDE staging, VIEW_WORKSPACE_BYTES fixed workspace,
    color/depth/postprocess/readback, and one four-byte owner fence per batch.
    """
    return (
        sum(geometry_sizes(vertices, faces, positions))
        + sum(transient_sizes(vertices, faces, positions, mode))
        + upload_bytes(vertices, faces, positions, mode)
    )
