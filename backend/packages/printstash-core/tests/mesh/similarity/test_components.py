"""Resource lineage and quantities survive export order and nested placement."""

from __future__ import annotations

import numpy as np
import pytest

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.components import (
    Assembly,
    Instance,
    MeshResource,
    compose_scene,
    expand_scene,
    split_components,
    validate_transform,
)


class TestSplitComponents:
    def test_splits_six_copies_without_changing_source(self, tetra):
        vertices, faces = tetra
        points = np.vstack([vertices + [i * 50, 0, 0] for i in range(6)])
        triangles = np.vstack([faces + i * 4 for i in range(6)])
        original = points.tobytes(), triangles.tobytes()

        result = split_components(points, triangles)

        assert len(result) == 6
        assert [r.resource_id for r in result] == [str(i) for i in range(1, 7)]
        assert all(len(r.vertices) == len(r.faces) == 4 for r in result)
        assert sorted(float(r.vertices[:, 0].min()) for r in result) == list(
            range(0, 300, 50)
        )
        assert (points.tobytes(), triangles.tobytes()) == original

    def test_component_indices_survive_parser_order(self, tetra):
        vertices, faces = tetra
        points = np.vstack((vertices, vertices + 100))
        triangles = np.vstack((faces, faces + 4))

        a = split_components(points, triangles)
        b = split_components(points[::-1], 7 - triangles[::-1])

        assert len(a) == len(b) == 2
        for first, second in zip(a, b, strict=True):
            assert first.resource_id == second.resource_id
            np.testing.assert_array_equal(first.vertices, second.vertices)
            np.testing.assert_array_equal(first.faces, second.faces)

    def test_point_touching_pieces_remain_separate(self):
        vertices = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]])

        result = split_components(vertices, np.array([[0, 1, 2], [0, 3, 4]]))

        assert len(result) == 2

    def test_rejects_too_many_components(self, tetra):
        vertices, faces = tetra

        with pytest.raises(GeometryError, match="component_resource_limit"):
            split_components(
                np.vstack((vertices, vertices + 100)),
                np.vstack((faces, faces + 4)),
                max_components=1,
            )

    @pytest.mark.parametrize("budget", [0, 2049, True])
    def test_rejects_invalid_budget(self, tetra, budget):
        with pytest.raises(GeometryError, match="invalid_component_budget"):
            split_components(*tetra, max_components=budget)


class TestExpandScene:
    def test_nested_instances_preserve_placed_geometry(self, tetra):
        mirror = np.diag([-2.0, 2, 2, 1])
        mirror[:3, 3] = [3, 5, 7]
        placement = np.eye(4)
        placement[:3, 3] = [100, 200, 300]
        resource = MeshResource("part", *tetra)
        assembly = Assembly(
            "group", (Instance("part", mirror), Instance("part", np.eye(4)))
        )

        scene = expand_scene((resource, assembly), (Instance("group", placement),))
        vertices, faces = compose_scene(scene)

        assert scene.resources == (resource,)
        assert len(scene.instances) == 2
        np.testing.assert_allclose(scene.instances[0].transform, placement @ mirror)
        np.testing.assert_allclose(
            vertices[:4], tetra[0] * [-2, 2, 2] + [103, 205, 307]
        )
        np.testing.assert_allclose(vertices[4:], tetra[0] + [100, 200, 300])
        np.testing.assert_array_equal(faces[:4], tetra[1][:, ::-1])
        np.testing.assert_array_equal(faces[4:], tetra[1] + 4)

    def test_repeated_resources_retain_multiplicity(self, tetra):
        resources = (MeshResource("part", *tetra),)

        scene = expand_scene(resources, (Instance("part", np.eye(4)),) * 6)

        assert len(scene.resources) == 1
        assert len(scene.instances) == 6

    def test_rejects_cycle_without_recursing(self):
        objects = (
            Assembly("a", (Instance("b", np.eye(4)),)),
            Assembly("b", (Instance("a", np.eye(4)),)),
        )

        with pytest.raises(GeometryError, match="cyclic_resource"):
            expand_scene(objects, (Instance("a", np.eye(4)),))

    def test_rejects_exponential_empty_assemblies(self):
        objects = [Assembly("0", ())]
        for level in range(1, 30):
            objects.append(
                Assembly(str(level), (Instance(str(level - 1), np.eye(4)),) * 2)
            )

        with pytest.raises(GeometryError, match="scene_resource_limit"):
            expand_scene(tuple(objects), (Instance("29", np.eye(4)),), max_instances=16)

    def test_rejects_excessive_depth(self, tetra):
        objects = (
            MeshResource("part", *tetra),
            Assembly("parent", (Instance("part", np.eye(4)),)),
        )

        with pytest.raises(GeometryError, match="scene_depth_limit"):
            expand_scene(objects, (Instance("parent", np.eye(4)),), max_depth=1)

    def test_rejects_expanded_face_budget(self, tetra):
        with pytest.raises(GeometryError, match="scene_resource_limit"):
            expand_scene(
                (MeshResource("part", *tetra),),
                (Instance("part", np.eye(4)),) * 2,
                max_faces=4,
            )

    def test_rejects_expanded_instance_budget(self, tetra):
        objects = (
            MeshResource("part", *tetra),
            Assembly("parent", (Instance("part", np.eye(4)),) * 3),
        )

        with pytest.raises(GeometryError, match="scene_resource_limit"):
            expand_scene(objects, (Instance("parent", np.eye(4)),), max_instances=2)

    def test_rejects_wide_child_list_before_traversal(self):
        objects = (Assembly("parent", (Instance("missing", np.eye(4)),) * 5),)

        with pytest.raises(GeometryError, match="scene_resource_limit"):
            expand_scene(objects, (Instance("parent", np.eye(4)),), max_instances=2)

    @pytest.mark.parametrize("shape", ["objects", "build"])
    def test_rejects_oversized_document(self, shape):
        objects = (
            tuple(Assembly(str(i), ()) for i in range(4097))
            if shape == "objects"
            else ()
        )
        build = (Instance("missing", np.eye(4)),) * (2049 if shape == "build" else 1)

        with pytest.raises(GeometryError, match="scene_resource_limit"):
            expand_scene(objects, build)

    @pytest.mark.parametrize("ids", [("a", "a"), ("",)])
    def test_rejects_duplicate_or_empty_resource_ids(self, ids):
        with pytest.raises(GeometryError, match="duplicate_resource"):
            expand_scene(tuple(Assembly(key, ()) for key in ids), ())

    def test_rejects_missing_reference(self):
        with pytest.raises(GeometryError, match="missing_resource"):
            expand_scene((), (Instance("missing", np.eye(4)),))

    def test_rejects_empty_build(self):
        with pytest.raises(GeometryError, match="empty_scene"):
            expand_scene((), ())

    @pytest.mark.parametrize(
        "name,value",
        [("max_depth", 0), ("max_faces", 2_000_001), ("max_instances", True)],
    )
    def test_rejects_invalid_budget(self, name, value):
        with pytest.raises(GeometryError, match="invalid_scene_budget"):
            expand_scene((), (), **{name: value})


class TestTransform:
    @pytest.mark.parametrize(
        "transform", [np.eye(3), np.full((4, 4), np.nan), np.ones((4, 4))]
    )
    def test_rejects_invalid_affine_transform(self, transform):
        with pytest.raises(GeometryError, match="invalid_transform"):
            validate_transform(transform)

    @pytest.mark.parametrize(
        "transform", [np.diag([1.0, 1, 0, 1]), np.diag([0.0, 0, 0, 1])]
    )
    def test_rejects_flattened_geometry(self, transform):
        with pytest.raises(GeometryError, match="degenerate_transform"):
            validate_transform(transform)

    def test_accepts_small_unit_conversion(self):
        validate_transform(np.diag([1e-6, 1e-6, 1e-6, 1]))
