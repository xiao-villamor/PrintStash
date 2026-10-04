"""Prepared geometry describes complete resources or an explicit source sample."""

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.components import (
    ExpandedScene,
    Instance,
    MeshResource,
)

from app.modules.media.mesh_facts import (
    CompleteGeometry,
    FingerprintFailureCode,
    SampledGeometry,
)
from app.modules.media.mesh_resources import (
    PreparedMesh,
    PreparedScene,
    materialize_scene,
    prepare_loaded_mesh,
)


class TestPreparedMesh:
    def test_preserves_complete_resource_identity(self):
        mesh = trimesh.creation.box()
        prepared = prepare_loaded_mesh(mesh, file_type="stl")
        assert prepared.whole_mesh is mesh
        assert isinstance(prepared.geometry, CompleteGeometry)
        assert prepared.complete is True
        assert prepared.failure_code is None
        assert prepared.whole_resource_id == prepared.scene.resources[0].resource_id

    def test_retains_explicit_sampled_geometry(self):
        mesh = trimesh.creation.box()
        geometry = SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE)
        prepared = PreparedMesh(mesh, ExpandedScene((), ()), geometry=geometry)
        assert prepared.geometry is geometry
        assert prepared.complete is False
        assert prepared.failure_code is FingerprintFailureCode.SAMPLED_SOURCE

    def test_rejects_sample_with_resource_claims(self):
        mesh = trimesh.creation.box()
        scene = prepare_loaded_mesh(mesh, file_type="stl").scene
        with pytest.raises(ValueError, match="sampled_geometry"):
            PreparedMesh(
                mesh,
                scene,
                geometry=SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
            )

    def test_rejects_complete_geometry_without_resources(self):
        mesh = trimesh.creation.box()
        with pytest.raises(ValueError, match="complete_geometry"):
            PreparedMesh(mesh, ExpandedScene((), ()), geometry=CompleteGeometry())

    def test_rejects_unknown_whole_resource_identity(self):
        mesh = trimesh.creation.box()
        scene = prepare_loaded_mesh(mesh, file_type="stl").scene
        with pytest.raises(ValueError, match="whole_resource"):
            PreparedMesh(
                mesh,
                scene,
                geometry=CompleteGeometry(),
                whole_resource_id="missing",
            )

    def test_preserves_source_arrays(self):
        mesh = trimesh.creation.box()
        before_vertices = mesh.vertices.copy()
        before_faces = mesh.faces.copy()
        prepare_loaded_mesh(mesh, file_type="stl")
        np.testing.assert_array_equal(mesh.vertices, before_vertices)
        np.testing.assert_array_equal(mesh.faces, before_faces)


class TestPreparedScene:
    def test_preserves_unique_arrays_for_repeated_instances(self):
        mesh = trimesh.creation.box()
        resource = MeshResource("part", mesh.vertices, mesh.faces)
        scene = ExpandedScene((resource,), (Instance("part", np.eye(4)),) * 64)
        prepared = PreparedScene(scene)
        assert prepared.triangle_count == 768
        assert prepared.scene.resources[0] is resource
        assert prepared.scene.resources[0].vertices is mesh.vertices
        assert not hasattr(prepared, "whole_mesh")

    @pytest.mark.parametrize(
        "scene,code",
        [
            pytest.param(ExpandedScene((), ()), "empty_scene", id="empty"),
            pytest.param(
                ExpandedScene((), (Instance("missing", np.eye(4)),)),
                "missing_resource",
                id="missing",
            ),
        ],
    )
    def test_rejects_invalid_resource_identity(self, scene, code):
        with pytest.raises(GeometryError, match=code):
            PreparedScene(scene)

    def test_rejects_expanded_face_budget_before_materialization(self):
        mesh = trimesh.creation.box()
        resource = MeshResource("part", mesh.vertices, np.tile(mesh.faces, (84, 1)))
        scene = ExpandedScene((resource,), (Instance("part", np.eye(4)),) * 2048)
        with pytest.raises(GeometryError, match="scene_resource_limit"):
            PreparedScene(scene)

    def test_rejects_unknown_scene_type(self):
        with pytest.raises(TypeError, match="invalid_prepared_scene"):
            PreparedScene("invalid")


class TestMaterializeScene:
    def test_preserves_complete_source_identity(self):
        mesh = trimesh.creation.box()
        resource = MeshResource("part", mesh.vertices, mesh.faces)
        scene = ExpandedScene((resource,), (Instance("part", np.eye(4)),))
        prepared = materialize_scene(scene)
        assert isinstance(prepared.geometry, CompleteGeometry)
        assert prepared.whole_resource_id == "part"
        np.testing.assert_array_equal(prepared.whole_mesh.vertices, mesh.vertices)
        np.testing.assert_array_equal(prepared.whole_mesh.faces, mesh.faces)

    def test_preserves_reflected_source_arrays(self):
        mesh = trimesh.creation.box(extents=[1, 2, 3])
        transform = np.diag([-2.0, 3.0, 4.0, 1.0])
        before_vertices, before_faces = mesh.vertices.copy(), mesh.faces.copy()
        scene = ExpandedScene(
            (MeshResource("part", mesh.vertices, mesh.faces),),
            (Instance("part", transform),),
        )
        prepared = materialize_scene(scene)
        assert prepared.whole_resource_id is None
        assert prepared.whole_mesh.is_winding_consistent
        assert prepared.whole_mesh.volume == pytest.approx(144.0)
        np.testing.assert_array_equal(mesh.vertices, before_vertices)
        np.testing.assert_array_equal(mesh.faces, before_faces)
