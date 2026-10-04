"""Owned relative render positions with repeatable, bounded triangle chunks.

Scene preparation retains unique source geometry and affine placements. The
renderer still needs O(expanded referenced vertices) float32 positions for global
normal welding and projection; it never builds a placed float64 source mesh or
an entire placed triangle array. No parser or Trimesh dependency belongs here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .similarity.components import ExpandedScene, expand_scene
from .similarity.fingerprint import GeometryError

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]
    RenderPositions = NDArray[np.float32]
    IntArray = NDArray[np.int64]

_POINT_CHUNK_SIZE = 64_000


class RenderableMesh(Protocol):
    """Source arrays required by the existing structural mesh entry point."""

    @property
    def vertices(self) -> FloatArray: ...

    @property
    def faces(self) -> IntArray | None: ...


@dataclass(frozen=True)
class MeshRenderInput:
    mesh: RenderableMesh | None


@dataclass(frozen=True)
class SceneRenderInput:
    scene: ExpandedScene


@dataclass(frozen=True)
class PreparedRenderGeometry:
    vertices: RenderPositions
    face_count: int
    face_chunks: Callable[[int], Iterator[IntArray]]


def prepare_mesh(mesh: RenderableMesh | None) -> PreparedRenderGeometry | None:
    """Preserve the structural mesh API while framing referenced vertices only."""
    import numpy as np

    if (
        mesh is None
        or len(mesh.vertices) == 0
        or mesh.faces is None
        or len(mesh.faces) == 0
    ):
        return None
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    if faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in "iu":
        raise ValueError("invalid triangle indices")
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError("triangle index outside vertex array")
    faces = faces.astype(np.int64, copy=True)
    faces.flags.writeable = False
    referenced = np.zeros(len(vertices), dtype=bool)
    referenced[faces] = True
    remap = None
    if not referenced.all():
        remap = np.cumsum(referenced, dtype=np.int64) - 1
        vertices = vertices[referenced]
    center = (vertices.max(axis=0) + vertices.min(axis=0)) * 0.5
    relative = (vertices - center).astype(np.float32)

    def chunks(size: int) -> Iterator[IntArray]:
        _validate_chunk(size)
        for start in range(0, len(faces), size):
            selected = faces[start : start + size]
            output = remap[selected] if remap is not None else selected
            output.flags.writeable = False
            yield output

    relative.flags.writeable = False
    return PreparedRenderGeometry(relative, len(faces), chunks)


def prepare_scene(scene: ExpandedScene) -> PreparedRenderGeometry:
    """Admit first, center in float64, then prepare render positions only."""
    import numpy as np

    scene = expand_scene(scene.resources, scene.instances)
    by_id = {resource.resource_id: resource for resource in scene.resources}
    points: dict[str, FloatArray] = {}
    remaps: dict[str, IntArray] = {}
    owned_faces: dict[str, IntArray] = {}
    for resource in scene.resources:
        referenced = np.zeros(len(resource.vertices), dtype=bool)
        referenced[resource.faces] = True
        points[resource.resource_id] = resource.vertices[referenced]
        faces = resource.faces.copy()
        faces.flags.writeable = False
        owned_faces[resource.resource_id] = faces
        # Only referenced indices can appear in admitted faces. Unused entries
        # in this source-to-render index map are never read.
        remaps[resource.resource_id] = np.cumsum(referenced, dtype=np.int64) - 1

    minimum = np.full(3, np.inf)
    maximum = np.full(3, -np.inf)
    vertex_count = face_count = 0
    try:
        with np.errstate(over="raise", invalid="raise"):
            for instance in scene.instances:
                source = points[instance.resource_id]
                vertex_count += len(source)
                face_count += len(by_id[instance.resource_id].faces)
                for start in range(0, len(source), _POINT_CHUNK_SIZE):
                    placed = (
                        source[start : start + _POINT_CHUNK_SIZE]
                        @ instance.transform[:3, :3].T
                        + instance.transform[:3, 3]
                    )
                    minimum = np.minimum(minimum, placed.min(axis=0))
                    maximum = np.maximum(maximum, placed.max(axis=0))
            center = (minimum + maximum) * 0.5
            vertices = np.empty((vertex_count, 3), dtype=np.float32)
            layout: list[tuple[IntArray, IntArray, int]] = []
            offset = 0
            for instance in scene.instances:
                source = points[instance.resource_id]
                transform = instance.transform
                for start in range(0, len(source), _POINT_CHUNK_SIZE):
                    placed = (
                        source[start : start + _POINT_CHUNK_SIZE] @ transform[:3, :3].T
                        + transform[:3, 3]
                    )
                    vertices[offset + start : offset + start + len(placed)] = (
                        placed - center
                    ).astype(np.float32)
                resource = by_id[instance.resource_id]
                winding = (
                    owned_faces[resource.resource_id][:, ::-1]
                    if np.linalg.slogdet(transform[:3, :3])[0] < 0
                    else owned_faces[resource.resource_id]
                )
                layout.append((winding, remaps[instance.resource_id], offset))
                offset += len(source)
            if not np.isfinite(vertices).all():
                raise GeometryError("numeric_range")
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise GeometryError("numeric_range") from exc

    def chunks(size: int) -> Iterator[IntArray]:
        _validate_chunk(size)
        placement = source_offset = emitted = 0
        while emitted < face_count:
            count = min(size, face_count - emitted)
            output = np.empty((count, 3), dtype=np.int64)
            target = 0
            while target < count:
                faces, remap, vertex_offset = layout[placement]
                take = min(count - target, len(faces) - source_offset)
                np.add(
                    remap[faces[source_offset : source_offset + take]],
                    vertex_offset,
                    out=output[target : target + take],
                )
                target += take
                source_offset += take
                if source_offset == len(faces):
                    placement += 1
                    source_offset = 0
            emitted += count
            output.flags.writeable = False
            yield output

    vertices.flags.writeable = False
    return PreparedRenderGeometry(vertices, face_count, chunks)


def _validate_chunk(size: int) -> None:
    if type(size) is not int or size < 1:
        raise ValueError("invalid_render_chunk")


@dataclass(frozen=True)
class PreparedRender:
    """Owned camera-independent positions, identities and angle-weighted normals.

    Scene face storage is proportional to unique source resources; expanded
    positions and welded normals remain whole. Iterators expose read-only,
    bounded indices in the original placement order.
    """

    vertices: RenderPositions
    face_count: int
    face_chunks: Callable[[int], Iterator[IntArray]]
    position_ids: IntArray
    smooth_normals: FloatArray


def prepare_mesh_render(
    mesh: RenderableMesh | None, *, face_chunk_size: int = 64_000
) -> PreparedRender | None:
    """Snapshot source arrays and prepare shared normals once for any camera."""
    _validate_chunk(face_chunk_size)
    geometry = prepare_mesh(mesh)
    return None if geometry is None else _prepare_normals(geometry, face_chunk_size)


def prepare_scene_render(
    scene: ExpandedScene, *, face_chunk_size: int = 64_000
) -> PreparedRender:
    """Admit retained resources before allocating owned render preparation."""
    _validate_chunk(face_chunk_size)
    return _prepare_normals(prepare_scene(scene), face_chunk_size)


def _prepare_normals(
    geometry: PreparedRenderGeometry, face_chunk_size: int
) -> PreparedRender:
    import numpy as np

    verts = geometry.vertices
    extent = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0
    q = np.round(verts / (extent * 1e-5)).astype(np.int64)
    q -= q.min(axis=0)
    span = q.max(axis=0) + 1
    key = (q[:, 0] * span[1] + q[:, 1]) * span[2] + q[:, 2]
    _, pos_id = np.unique(key, return_inverse=True)
    n_pos = int(pos_id.max()) + 1
    del q, key

    # Angle-weighted object-space normals are shared across every camera.
    # Accumulate bounded facet chunks into the whole welded-position table.
    vacc = np.zeros((n_pos, 3), dtype=np.float64)
    for fc in geometry.face_chunks(face_chunk_size):
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
        ang = np.arccos(np.clip(np.sum(e_ab * e_ac, axis=2), -1.0, 1.0))  # (c,3)
        flat_pos = pos_id[fc].ravel()  # (3c,) welded id per face corner
        # Each corner contributes its face normal scaled by that corner angle.
        fn_per_corner = np.repeat(fn, 3, axis=0) * ang.ravel()[:, None]  # (3c,3)
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

    pos_id.flags.writeable = False
    vsm.flags.writeable = False
    return PreparedRender(verts, geometry.face_count, geometry.face_chunks, pos_id, vsm)
