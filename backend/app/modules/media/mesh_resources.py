"""Materialized mesh preparation with explicit ownership of its source scene."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from printstash_core.mesh.similarity.components import (
    ExpandedScene,
    Instance,
    compose_scene,
    expand_scene,
    split_components,
)

from app.modules.media.three_mf_scene import read_scene

from .mesh_facts import (
    CompleteGeometry,
    FingerprintFailureCode,
    PreparedGeometry,
    SampledGeometry,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray
    from trimesh import Trimesh


@dataclass(frozen=True)
class PreparedScene:
    """An admitted source scene, without materialized placed geometry.

    Admission preserves each unique resource's buffers and checks placed counts
    before any whole-scene allocation. It does not claim materialized coverage.
    """

    scene: ExpandedScene

    def __post_init__(self) -> None:
        if not isinstance(self.scene, ExpandedScene):
            raise TypeError("invalid_prepared_scene")
        admitted = expand_scene(self.scene.resources, self.scene.instances)
        object.__setattr__(self, "scene", admitted)

    @property
    def triangle_count(self) -> int:
        counts = {
            resource.resource_id: len(resource.faces)
            for resource in self.scene.resources
        }
        return sum(counts[instance.resource_id] for instance in self.scene.instances)


@dataclass(frozen=True)
class PreparedMesh:
    whole_mesh: Trimesh
    scene: ExpandedScene
    geometry: PreparedGeometry
    brep: dict[str, Any] | None = None
    whole_resource_id: str | None = None

    def __post_init__(self) -> None:
        import numpy as np
        from trimesh import Trimesh

        if not isinstance(self.whole_mesh, Trimesh):
            raise TypeError("invalid_prepared_mesh")
        if not isinstance(self.scene, ExpandedScene):
            raise TypeError("invalid_prepared_scene")
        if isinstance(self.geometry, SampledGeometry):
            if (
                self.scene.resources
                or self.scene.instances
                or self.whole_resource_id is not None
                or self.brep is not None
            ):
                raise ValueError("invalid_sampled_geometry_resource_claim")
        elif isinstance(self.geometry, CompleteGeometry):
            resources = self.scene.resources
            instances = self.scene.instances
            if not resources or not instances:
                raise ValueError("invalid_complete_geometry_resources")
            identifiers = {resource.resource_id for resource in resources}
            if len(identifiers) != len(resources) or any(
                instance.resource_id not in identifiers for instance in instances
            ):
                raise ValueError("invalid_complete_geometry_resource_identity")
            if self.whole_resource_id is not None and (
                self.whole_resource_id not in identifiers
                or len(resources) != 1
                or len(instances) != 1
                or not np.array_equal(instances[0].transform, np.eye(4))
            ):
                raise ValueError("invalid_whole_resource_identity")
        else:
            raise TypeError("invalid_prepared_geometry")

    @property
    def complete(self) -> bool:
        """Prepared-only bridge for verify_paths/_component/embedding consumers.

        These readers migrate to geometry variants during their owner extraction;
        source scan and preview completeness never use this property.
        """
        return isinstance(self.geometry, CompleteGeometry)

    @property
    def failure_code(self) -> FingerprintFailureCode | None:
        return (
            self.geometry.reason if isinstance(self.geometry, SampledGeometry) else None
        )


@dataclass(frozen=True)
class DetachedSceneMesh:
    """Admitted placed buffers retained across preview without native caches.

    The whole-scene arrays remain resident until optional analysis completes;
    source resources and placement identity stay independent from that mesh.
    """

    vertices: NDArray[np.float64]
    faces: NDArray[np.int64]
    scene: ExpandedScene
    geometry: CompleteGeometry
    whole_resource_id: str | None

    def __post_init__(self) -> None:
        import numpy as np

        if (
            not isinstance(self.vertices, np.ndarray)
            or not isinstance(self.faces, np.ndarray)
            or self.vertices.dtype != np.dtype(np.float64)
            or self.faces.dtype != np.dtype(np.int64)
            or self.vertices.ndim != 2
            or self.faces.ndim != 2
            or self.vertices.shape[1] != 3
            or self.faces.shape[1] != 3
            or self.vertices.flags.writeable
            or self.faces.flags.writeable
        ):
            raise ValueError("invalid_detached_scene_buffers")
        if not isinstance(self.scene, ExpandedScene) or not isinstance(
            self.geometry, CompleteGeometry
        ):
            raise TypeError("invalid_detached_scene_identity")


def detach_scene_mesh(
    prepared: PreparedMesh, *, triangle_cap: int
) -> DetachedSceneMesh:
    """Retain admitted arrays, never the Trimesh object or its topology caches."""
    import numpy as np

    if type(triangle_cap) is not int or not 100 <= triangle_cap <= MAX_ANALYSIS_FACES:
        raise ValueError("invalid_triangle_cap")
    if len(prepared.whole_mesh.faces) > triangle_cap:
        raise GeometryError("geometry_work_limit")
    if not isinstance(prepared.geometry, CompleteGeometry) or prepared.brep is not None:
        raise ValueError("invalid_detached_scene_identity")
    vertices = np.asarray(prepared.whole_mesh.vertices).view()
    faces = np.asarray(prepared.whole_mesh.faces).view()
    vertices.flags.writeable = False
    faces.flags.writeable = False
    return DetachedSceneMesh(
        vertices, faces, prepared.scene, prepared.geometry, prepared.whole_resource_id
    )


def restore_scene_mesh(detached: DetachedSceneMesh) -> PreparedMesh:
    """Wrap retained arrays for analysis without composing the scene again."""
    import trimesh

    mesh = trimesh.Trimesh(
        vertices=detached.vertices, faces=detached.faces, process=False
    )
    return PreparedMesh(
        mesh,
        detached.scene,
        geometry=detached.geometry,
        whole_resource_id=detached.whole_resource_id,
    )


def prepare_loaded_mesh(mesh: Trimesh, *, file_type: str) -> PreparedMesh:
    import numpy as np

    resources = split_components(np.asarray(mesh.vertices), np.asarray(mesh.faces))
    scene = ExpandedScene(
        resources,
        tuple(Instance(resource.resource_id, np.eye(4)) for resource in resources),
    )
    return PreparedMesh(
        mesh,
        scene,
        geometry=CompleteGeometry(),
        brep=mesh.metadata.get("brep"),
        whole_resource_id=resources[0].resource_id if len(resources) == 1 else None,
    )


def load_3mf(path: Path, *, max_faces: int = MAX_ANALYSIS_FACES) -> PreparedMesh:
    """Read a bounded source scene, then explicitly materialize its placed mesh."""
    return materialize_scene(read_scene(path, max_faces=max_faces))


def materialize_scene(scene: ExpandedScene) -> PreparedMesh:
    """Allocate placed arrays only after complete source-scene admission."""
    import numpy as np
    import trimesh

    scene = PreparedScene(scene).scene
    try:
        vertices, faces = compose_scene(scene)
        if not np.isfinite(vertices).all():
            raise GeometryError("nonfinite_geometry")
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        same_resource = len(scene.resources) == len(
            scene.instances
        ) == 1 and np.array_equal(scene.instances[0].transform, np.eye(4))
        return PreparedMesh(
            mesh,
            scene,
            geometry=CompleteGeometry(),
            whole_resource_id=(
                scene.resources[0].resource_id if same_resource else None
            ),
        )
    except GeometryError:
        raise
    except (OSError, ValueError, KeyError, OverflowError) as exc:
        raise GeometryError("invalid_3mf") from exc
