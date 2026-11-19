"""Shape-only render inputs and shared corner-normal preparation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal

from printstash_core.mesh.render_geometry import (
    PreparedRender,
    PreparedRenderGeometry,
    prepare_normals,
    weld_positions,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    IndexArray = NDArray[np.int64] | NDArray[np.uint32]

from .compute_geometry_wire import (
    NORMAL_MODE,
    VISUAL_NORMAL_MODE,
    GeometryLayout,
    _validate_layout,
)

GPU_NORMALS_MIN_FACES = 20_000
FACE_CHUNK = 64_000
# Each edge is bounded by 2C, each cross component by 8C², and its
# squared three-component norm by 192C⁴. Leave margin for rounding.
MAX_ABS_COORDINATE = (3.402823466e38 / 256.0) ** 0.25


@dataclass(frozen=True)
class DeferredRender:
    """Owned render geometry with precomputed position identities."""

    geometry: PreparedRenderGeometry
    position_ids: IndexArray
    position_count: int
    normal_mode: ClassVar[Literal["gpu-angle-csr-v1"]] = NORMAL_MODE
    _canonical: PreparedRender | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def vertices(self):
        return self.geometry.vertices

    @property
    def face_count(self):
        return self.geometry.face_count

    @property
    def face_chunks(self):
        return self.geometry.face_chunks


@dataclass(frozen=True)
class VisualRender:
    """Unwelded visual geometry with no normal payload."""

    geometry: PreparedRenderGeometry
    normal_mode: ClassVar[Literal["gpu-visual-v2"]] = VISUAL_NORMAL_MODE
    _canonical: PreparedRender | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def vertices(self):
        return self.geometry.vertices

    @property
    def face_count(self):
        return self.geometry.face_count

    @property
    def face_chunks(self):
        return self.geometry.face_chunks

    @property
    def position_count(self):
        return len(self.vertices)


RenderInput = PreparedRender | DeferredRender | VisualRender


def defer(geometry: PreparedRenderGeometry) -> DeferredRender:
    identities = weld_positions(geometry)
    return DeferredRender(geometry, identities, int(identities.max()) + 1)


def eligible(prepared: DeferredRender | VisualRender) -> bool:
    if (
        isinstance(prepared, DeferredRender)
        and prepared.face_count < GPU_NORMALS_MIN_FACES
    ):
        return False
    try:
        _validate_layout(
            GeometryLayout(
                (len(prepared.vertices), prepared.face_count, prepared.position_count),
                prepared.normal_mode,
            )
        )
    except ValueError:
        return False
    return True


def canonical(
    prepared: RenderInput, *, cache: bool = False, face_chunk_size: int = FACE_CHUNK
) -> PreparedRender:
    """Materialize shared corner normals for CPU rasterization or device upload.

    Only the parser facade opts into memoization, while its native RAM permit
    still owns the source and normal arrays. Broker fallback is temporary.
    """
    import numpy as np

    if isinstance(prepared, PreparedRender):
        return prepared
    if cache and prepared._canonical is not None:
        return prepared._canonical
    result = prepare_normals(
        prepared.geometry,
        position_ids=(
            np.asarray(prepared.position_ids, dtype=np.int64)
            if isinstance(prepared, DeferredRender)
            else None
        ),
        face_chunk_size=face_chunk_size,
    )
    if cache:
        object.__setattr__(prepared, "_canonical", result)
    return result


def host_scratch_bytes(prepared: RenderInput) -> int:
    """Bound welding, adjacency and one chunk of corner-normal preparation."""
    if isinstance(prepared, PreparedRender):
        return 0
    return 160 * prepared.face_count + 80 * len(prepared.vertices) + 32 * 1024**2
