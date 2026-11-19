"""Allocation formulas shared by CPU-only admission and the GPU owner.

These helpers import no array or GPU libraries. Counts describe validated render
arrays; they never infer authoritative geometry from a visual allocation.
"""

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


def view_bounds_sizes(vertex_count: int) -> tuple[int, ...]:
    """Final view bounds followed by one reusable buffer per reduction level."""
    return (BOUND_BYTES, *bounds_scratch_sizes(vertex_count)) if vertex_count else ()


def geometry_sizes(vertices: int, faces: int) -> tuple[int, ...]:
    return vertices * 12, faces * 12, faces * 36, BOUND_BYTES


def upload_bytes(vertices: int, faces: int) -> int:
    return sum(geometry_sizes(vertices, faces)[:-1])


def device_geometry_bytes(vertices: int, faces: int) -> int:
    """New geometry, transient bounds scratch and its share of grouped staging."""
    return (
        sum(geometry_sizes(vertices, faces))
        + sum(bounds_scratch_sizes(vertices))
        + upload_bytes(vertices, faces)
    )
