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
    DetachedSceneMesh,
    PreparedMesh,
    PreparedScene,
    detach_scene_mesh,
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


class TestResourceEvidenceBoundaries:
    @pytest.mark.parametrize(
        "invalid", ["mesh", "scene", "geometry"], ids=["mesh", "scene", "geometry"]
    )
    def test_refuses_untyped_preparation_identity(self, invalid):
        mesh = trimesh.creation.box()
        prepared = prepare_loaded_mesh(mesh, file_type="stl")
        with pytest.raises(TypeError, match="invalid_prepared_"):
            PreparedMesh(
                None if invalid == "mesh" else mesh,
                None if invalid == "scene" else prepared.scene,
                None if invalid == "geometry" else CompleteGeometry(),
            )

    @pytest.mark.parametrize(
        "invalid",
        ["duplicate", "missing"],
        ids=["duplicate-resource", "missing-instance-resource"],
    )
    def test_refuses_incoherent_resource_identity(self, invalid):
        mesh = trimesh.creation.box()
        resource = MeshResource("part", mesh.vertices, mesh.faces)
        scene = ExpandedScene(
            (resource, resource) if invalid == "duplicate" else (resource,),
            (Instance("missing" if invalid == "missing" else "part", np.eye(4)),),
        )
        with pytest.raises(
            ValueError, match="invalid_complete_geometry_resource_identity"
        ):
            PreparedMesh(mesh, scene, CompleteGeometry())

    @pytest.mark.parametrize(
        "invalid", ["repeated", "translated"], ids=["repeated", "translated"]
    )
    def test_refuses_whole_identity_for_placed_instances(self, invalid):
        mesh = trimesh.creation.box()
        resource = MeshResource("part", mesh.vertices, mesh.faces)
        transform = np.eye(4)
        if invalid == "translated":
            transform[0, 3] = 10
        instances = (Instance("part", transform),) * (2 if invalid == "repeated" else 1)
        with pytest.raises(ValueError, match="invalid_whole_resource_identity"):
            PreparedMesh(
                mesh,
                ExpandedScene((resource,), instances),
                CompleteGeometry(),
                whole_resource_id="part",
            )

    @pytest.mark.parametrize(
        "invalid",
        [
            "vertex-type",
            "face-type",
            "vertex-dtype",
            "face-dtype",
            "vertex-ndim",
            "face-ndim",
            "vertex-shape",
            "face-shape",
            "mutable-vertices",
            "mutable-faces",
        ],
        ids=str,
    )
    def test_refuses_invalid_detached_buffers(self, invalid):
        mesh = trimesh.creation.box()
        prepared = prepare_loaded_mesh(mesh, file_type="stl")
        vertices = np.array(mesh.vertices, dtype=np.float64)
        faces = np.array(mesh.faces, dtype=np.int64)
        if invalid == "vertex-dtype":
            vertices = vertices.astype(np.float32)
        if invalid == "face-dtype":
            faces = faces.astype(np.int32)
        if invalid == "vertex-ndim":
            vertices = vertices.ravel()
        if invalid == "face-ndim":
            faces = faces.ravel()
        if invalid == "vertex-shape":
            vertices = vertices[:, :2]
        if invalid == "face-shape":
            faces = faces[:, :2]
        vertices.flags.writeable = invalid == "mutable-vertices"
        faces.flags.writeable = invalid == "mutable-faces"
        before = mesh.vertices.tobytes(), mesh.faces.tobytes()
        with pytest.raises(ValueError, match="invalid_detached_scene_buffers"):
            DetachedSceneMesh(
                vertices.tolist() if invalid == "vertex-type" else vertices,
                faces.tolist() if invalid == "face-type" else faces,
                prepared.scene,
                CompleteGeometry(),
                prepared.whole_resource_id,
            )
        assert (mesh.vertices.tobytes(), mesh.faces.tobytes()) == before

    @pytest.mark.parametrize(
        "invalid", ["scene", "geometry"], ids=["scene", "geometry"]
    )
    def test_refuses_untyped_detached_identity(self, invalid):
        prepared = prepare_loaded_mesh(trimesh.creation.box(), file_type="stl")
        detached = detach_scene_mesh(prepared, triangle_cap=100)
        with pytest.raises(TypeError, match="invalid_detached_scene_identity"):
            DetachedSceneMesh(
                detached.vertices,
                detached.faces,
                None if invalid == "scene" else detached.scene,
                None if invalid == "geometry" else CompleteGeometry(),
                detached.whole_resource_id,
            )

    @pytest.mark.parametrize(
        "cap",
        [True, 99, 2000001, 100.0],
        ids=["boolean", "below-min", "above-max", "float"],
    )
    def test_refuses_invalid_detach_budget(self, cap):
        prepared = prepare_loaded_mesh(trimesh.creation.box(), file_type="stl")
        with pytest.raises(ValueError, match="invalid_triangle_cap"):
            detach_scene_mesh(prepared, triangle_cap=cap)

    def test_refuses_detach_above_face_budget(self):
        mesh = trimesh.creation.icosphere(subdivisions=2)
        assert len(mesh.faces) > 100
        prepared = prepare_loaded_mesh(mesh, file_type="stl")
        with pytest.raises(GeometryError, match="geometry_work_limit"):
            detach_scene_mesh(prepared, triangle_cap=100)
        assert prepared.whole_mesh is mesh

    @pytest.mark.parametrize("invalid", ["sample", "brep"], ids=["sample", "brep"])
    def test_refuses_detach_of_incomplete_evidence(self, invalid):
        mesh = trimesh.creation.box()
        if invalid == "sample":
            prepared = PreparedMesh(
                mesh,
                ExpandedScene((), ()),
                SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
            )
        else:
            mesh.metadata["brep"] = {"source": "cad"}
            prepared = prepare_loaded_mesh(mesh, file_type="stl")
        with pytest.raises(ValueError, match="invalid_detached_scene_identity"):
            detach_scene_mesh(prepared, triangle_cap=100)

    def test_normalizes_native_materialization_failure(self, monkeypatch):
        mesh = trimesh.creation.box()
        scene = ExpandedScene(
            (MeshResource("part", mesh.vertices, mesh.faces),),
            (Instance("part", np.eye(4)),),
        )
        before = mesh.vertices.tobytes(), mesh.faces.tobytes()

        def failed_allocator(*args, **kwargs):
            raise OSError("native allocation failed")

        monkeypatch.setattr(trimesh, "Trimesh", failed_allocator)
        with pytest.raises(GeometryError, match="invalid_3mf"):
            materialize_scene(scene)
        assert (mesh.vertices.tobytes(), mesh.faces.tobytes()) == before
