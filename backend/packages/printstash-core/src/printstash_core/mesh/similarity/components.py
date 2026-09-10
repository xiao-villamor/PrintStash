"""Bounded component extraction and scene expansion, independent of file parsers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .fingerprint import (
    FingerprintBudget,
    GeometryError,
    clean_mesh,
    validate_mesh_arrays,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]
    IntArray = NDArray[np.int64]


@dataclass(frozen=True)
class MeshResource:
    resource_id: str
    vertices: FloatArray
    faces: IntArray


@dataclass(frozen=True)
class Instance:
    resource_id: str
    transform: FloatArray


@dataclass(frozen=True)
class Assembly:
    resource_id: str
    children: tuple[Instance, ...]


@dataclass(frozen=True)
class ExpandedScene:
    resources: tuple[MeshResource, ...]
    instances: tuple[Instance, ...]


def split_components(
    vertices: FloatArray,
    faces: IntArray,
    *,
    max_components: int = 256,
) -> tuple[MeshResource, ...]:
    """Edge-connected pieces, ordered by content, never parser arrival order.

    Absolute positions distinguish identical pieces on a plate. Re-exported
    vertex/face order cannot change an index. Indices only identify a component
    inside this source extraction, not a component in a different source hash.
    """
    import numpy as np

    if type(max_components) is not int or not 1 <= max_components <= 2048:
        raise GeometryError("invalid_component_budget")
    validate_mesh_arrays(vertices, faces, FingerprintBudget())
    # _clean translates relative to the smallest referenced point; restore it so
    # component instance coordinates remain in the original physical frame.
    used = vertices[np.unique(faces)]
    origin = used[np.lexsort(used.T[::-1])[0]]
    verts, tris = clean_mesh(vertices, faces)
    verts += origin
    parents = np.arange(len(tris))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = int(parents[index])
        return index

    edges = np.sort(tris[:, ((0, 1), (1, 2), (2, 0))].reshape((-1, 2)), axis=1)
    order = np.lexsort(edges.T[::-1])
    neighbors = np.flatnonzero(np.all(edges[order[1:]] == edges[order[:-1]], axis=1))
    for offset in neighbors:
        a, b = root(int(order[offset] // 3)), root(int(order[offset + 1] // 3))
        if a != b:
            parents[max(a, b)] = min(a, b)
    labels = np.array([root(i) for i in range(len(tris))])
    groups, counts = np.unique(labels, return_counts=True)
    if len(groups) > max_components:
        raise GeometryError("component_resource_limit")
    pieces: list[tuple[str, FloatArray, IntArray]] = []
    ordered_faces = np.argsort(labels, kind="stable")
    for indices in np.split(ordered_faces, np.cumsum(counts)[:-1]):
        referenced, remap = np.unique(tris[indices], return_inverse=True)
        points = verts[referenced]
        triangles = remap.reshape((-1, 3))
        payload = points.astype("<f8").tobytes() + triangles.astype("<i8").tobytes()
        pieces.append((hashlib.sha256(payload).hexdigest(), points, triangles))
    pieces.sort(key=lambda item: item[0])
    return tuple(
        MeshResource(str(index), points, triangles)
        for index, (_, points, triangles) in enumerate(pieces, 1)
    )


def expand_scene(
    objects: tuple[MeshResource | Assembly, ...],
    build: tuple[Instance, ...],
    *,
    max_instances: int = 2048,
    max_depth: int = 64,
    max_faces: int = 200_000,
) -> ExpandedScene:
    """Resolve nested resources once while preserving every placed instance.

    Explicit traversal avoids parser recursion. Admission includes intermediate
    references, even empty assemblies, so exponential expansions fail early.
    """
    import numpy as np

    for value, ceiling in (
        (max_instances, 2048),
        (max_depth, 64),
        (max_faces, 200_000),
    ):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise GeometryError("invalid_scene_budget")
    if len(objects) > 4096 or len(build) > max_instances:
        raise GeometryError("scene_resource_limit")
    by_id = {obj.resource_id: obj for obj in objects}
    if len(by_id) != len(objects) or any(not obj.resource_id for obj in objects):
        raise GeometryError("duplicate_resource")
    admitted: dict[str, MeshResource] = {}
    output: list[Instance] = []
    stack: list[tuple[Instance, FloatArray, tuple[str, ...]]] = [
        (instance, np.eye(4), ()) for instance in reversed(build)
    ]
    visited = 0
    face_count = 0
    vertex_count = 0
    while stack:
        instance, parent, ancestors = stack.pop()
        visited += 1
        if visited > max_instances * 2 or len(stack) > max_instances * 2:
            raise GeometryError("scene_resource_limit")
        if instance.resource_id in ancestors:
            raise GeometryError("cyclic_resource")
        if len(ancestors) >= max_depth:
            raise GeometryError("scene_depth_limit")
        obj = by_id.get(instance.resource_id)
        if obj is None:
            raise GeometryError("missing_resource")
        validate_transform(instance.transform)
        transform = parent @ instance.transform
        validate_transform(transform)
        if isinstance(obj, MeshResource):
            face_count += len(obj.faces)
            vertex_count += len(obj.vertices)
            if (
                len(output) >= max_instances
                or face_count > max_faces
                or vertex_count > 600_000
            ):
                raise GeometryError("scene_resource_limit")
            if obj.resource_id not in admitted:
                validate_mesh_arrays(obj.vertices, obj.faces, FingerprintBudget())
                admitted[obj.resource_id] = obj
            output.append(Instance(obj.resource_id, transform))
        else:
            if len(obj.children) + len(stack) > max_instances * 2:
                raise GeometryError("scene_resource_limit")
            chain = (*ancestors, instance.resource_id)
            stack.extend((child, transform, chain) for child in reversed(obj.children))
    if not output:
        raise GeometryError("empty_scene")
    return ExpandedScene(
        tuple(admitted[key] for key in sorted(admitted)), tuple(output)
    )


def validate_transform(transform: FloatArray) -> None:
    import numpy as np

    if (
        transform.shape != (4, 4)
        or not np.isfinite(transform).all()
        or not np.array_equal(transform[3], [0, 0, 0, 1])
    ):
        raise GeometryError("invalid_transform")
    # Relative singular values admit real small-unit conversion but reject a
    # flattened object or a matrix that cannot safely be inverted for alignment.
    singular = np.linalg.svd(transform[:3, :3], compute_uv=False)
    if singular[-1] <= singular[0] * 1e-12 or singular[0] == 0:
        raise GeometryError("degenerate_transform")


def compose_scene(scene: ExpandedScene) -> tuple[FloatArray, IntArray]:
    """Concatenate already-admitted instances, applying their full transforms."""
    import numpy as np

    by_id = {resource.resource_id: resource for resource in scene.resources}
    vertices, faces = [], []
    offset = 0
    for instance in scene.instances:
        resource = by_id[instance.resource_id]
        transform = instance.transform
        vertices.append(resource.vertices @ transform[:3, :3].T + transform[:3, 3])
        winding = (
            resource.faces[:, ::-1]
            if np.linalg.det(transform[:3, :3]) < 0
            else resource.faces
        )
        faces.append(winding + offset)
        offset += len(resource.vertices)
    return np.vstack(vertices), np.vstack(faces)
