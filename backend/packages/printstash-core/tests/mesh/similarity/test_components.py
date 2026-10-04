"""Resource lineage and quantities survive export order and nested placement."""

from __future__ import annotations

import numpy as np
import pytest

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.components import (
    Assembly,
    ExpandedScene,
    Instance,
    MeshResource,
    compose_scene,
    expand_scene,
    face_components,
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


def _reference_partition(triangles: np.ndarray) -> list[tuple[int, ...]]:
    """Edge-connected faces by the original one-edge-at-a-time union-find."""
    parents = list(range(len(triangles)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    owners: dict[tuple[int, int], int] = {}
    for face, (a, b, c) in enumerate(triangles.tolist()):
        for edge in ((a, b), (b, c), (c, a)):
            key = (min(edge), max(edge))
            if key in owners:
                first, second = root(owners[key]), root(face)
                if first != second:
                    parents[max(first, second)] = min(first, second)
            else:
                owners[key] = face
    groups: dict[int, list[int]] = {}
    for face in range(len(triangles)):
        groups.setdefault(root(face), []).append(face)
    return sorted(tuple(members) for members in groups.values())


def _partition(labels: np.ndarray) -> list[tuple[int, ...]]:
    groups: dict[int, list[int]] = {}
    for face, label in enumerate(labels.tolist()):
        groups.setdefault(label, []).append(face)
    return sorted(tuple(members) for members in groups.values())


class TestFaceComponents:
    @pytest.mark.parametrize("seed", range(25))
    def test_matches_the_reference_partition_on_random_soups(self, seed):
        """Sparse random triangles make islands, chains and shared-edge fans.

        A small vertex pool relative to the face count makes edge sharing (and
        edges shared by more than two faces) common, which is where a vectorised
        merge is most likely to disagree with the sequential one.
        """
        rng = np.random.default_rng(seed)
        faces = int(rng.integers(1, 400))
        pool = int(rng.integers(4, 3 * faces + 4))
        triangles = np.stack(
            [rng.choice(pool, size=3, replace=False) for _ in range(faces)]
        )

        assert _partition(face_components(triangles)) == _reference_partition(triangles)

    def test_joins_a_long_chain_that_needs_many_merge_rounds(self):
        """A strip of N faces is the worst case for naive label propagation."""
        count = 2000
        base = np.arange(count)
        triangles = np.stack([base, base + 1, base + 2], axis=1)

        labels = face_components(triangles)

        assert len(set(labels.tolist())) == 1

    def test_keeps_faces_that_only_touch_at_a_vertex_apart(self):
        triangles = np.array([[0, 1, 2], [2, 3, 4]])

        assert _partition(face_components(triangles)) == [(0,), (1,)]

    def test_labels_every_face_of_an_empty_mesh_as_nothing(self):
        assert face_components(np.empty((0, 3), dtype=np.int64)).shape == (0,)


class TestComposeScene:
    @pytest.mark.parametrize(
        "scale,winding",
        [([1, 1, 1], [0, 1, 2]), ([-2, 2, 2], [2, 1, 0])],
    )
    def test_materializes_64_placements_without_concatenation(
        self, tetra, monkeypatch, scale, winding
    ):
        shifts = np.arange(64)[:, None] * [50, 100, 150]
        transforms = np.tile(np.diag([*scale, 1]), (64, 1, 1)).astype(np.float64)
        transforms[:, :3, 3] = shifts
        resource = MeshResource("part", *tetra)
        scene = ExpandedScene(
            (resource,), tuple(Instance("part", transform) for transform in transforms)
        )
        originals = tetra[0].tobytes(), tetra[1].tobytes(), transforms.tobytes()
        expected_points = (tetra[0][None, :, :] * scale + shifts[:, None, :]).reshape(
            (-1, 3)
        )
        expected_faces = (
            tetra[1][None, :, winding] + np.arange(64)[:, None, None] * 4
        ).reshape((-1, 3))

        def concatenate(*_args, **_kwargs):
            raise AssertionError("placed geometry must not concatenate partial buffers")

        monkeypatch.setattr(np, "vstack", concatenate)
        points, faces = compose_scene(scene)

        np.testing.assert_array_equal(points, expected_points)
        np.testing.assert_array_equal(faces, expected_faces)
        assert points.dtype == np.float64
        assert faces.dtype == np.int64
        assert (
            tetra[0].tobytes(),
            tetra[1].tobytes(),
            transforms.tobytes(),
        ) == originals
        assert not np.shares_memory(points, tetra[0])
        assert not np.shares_memory(faces, tetra[1])

    @pytest.mark.parametrize("limits", [{"max_faces": 255}, {"max_vertices": 255}])
    def test_rejects_expanded_budget_before_output_allocation(
        self, tetra, monkeypatch, limits
    ):
        scene = ExpandedScene(
            (MeshResource("part", *tetra),), (Instance("part", np.eye(4)),) * 64
        )

        def allocate(*_args, **_kwargs):
            raise AssertionError("refused geometry allocated an output buffer")

        monkeypatch.setattr(np, "empty", allocate)
        with pytest.raises(GeometryError, match="scene_resource_limit"):
            compose_scene(scene, **limits)

    def test_accepts_exact_materialization_budget(self, tetra):
        scene = ExpandedScene(
            (MeshResource("part", *tetra),), (Instance("part", np.eye(4)),) * 64
        )

        points, faces = compose_scene(scene, max_faces=256, max_vertices=256)

        assert points.shape == faces.shape == (256, 3)
        np.testing.assert_array_equal(points[-4:], tetra[0])
        np.testing.assert_array_equal(faces[-4:], tetra[1] + 252)

    @pytest.mark.parametrize(
        "name,value",
        [
            ("max_faces", 0),
            ("max_faces", True),
            ("max_faces", 1.0),
            ("max_faces", 2_000_001),
            ("max_vertices", 0),
            ("max_vertices", True),
            ("max_vertices", 1.0),
            ("max_vertices", 6_000_001),
        ],
    )
    def test_rejects_invalid_materialization_budget(self, name, value):
        with pytest.raises(GeometryError, match="invalid_scene_budget"):
            compose_scene(ExpandedScene((), ()), **{name: value})

    def test_rejects_empty_scene(self):
        with pytest.raises(GeometryError, match="empty_scene"):
            compose_scene(ExpandedScene((), ()))

    @pytest.mark.parametrize("ids", [("part", "part"), ("",)])
    def test_rejects_invalid_resource_identity(self, tetra, ids):
        scene = ExpandedScene(
            tuple(MeshResource(key, *tetra) for key in ids),
            (Instance(ids[0], np.eye(4)),),
        )

        with pytest.raises(GeometryError, match="duplicate_resource"):
            compose_scene(scene)

    def test_rejects_missing_resource(self):
        with pytest.raises(GeometryError, match="missing_resource"):
            compose_scene(ExpandedScene((), (Instance("absent", np.eye(4)),)))

    def test_ignores_unreferenced_invalid_arrays(self, tetra):
        scene = ExpandedScene(
            (
                MeshResource("part", *tetra),
                MeshResource("unused", np.full((1, 3), np.nan), np.ones((1, 3))),
            ),
            (Instance("part", np.eye(4)),),
        )

        points, faces = compose_scene(scene)

        np.testing.assert_array_equal(points, tetra[0])
        np.testing.assert_array_equal(faces, tetra[1])

    @pytest.mark.parametrize(
        "points,faces,reason",
        [
            (np.full((3, 3), np.nan), np.array([[0, 1, 2]]), "nonfinite_geometry"),
            (np.eye(3), np.array([[0, 1, 3]]), "invalid_faces"),
            (np.eye(3), np.array([[0, 1, -1]]), "invalid_faces"),
            (np.eye(3), np.full((1, 3), 2**64 - 1, dtype=np.uint64), "invalid_faces"),
            (np.eye(3), np.empty((0, 3), dtype=np.int64), "degenerate_surface"),
        ],
    )
    def test_rejects_invalid_source_before_output_allocation(
        self, points, faces, reason, monkeypatch
    ):
        scene = ExpandedScene(
            (MeshResource("part", points, faces),), (Instance("part", np.eye(4)),)
        )

        def allocate(*_args, **_kwargs):
            raise AssertionError("invalid source allocated an output buffer")

        monkeypatch.setattr(np, "empty", allocate)
        with pytest.raises(GeometryError, match=reason):
            compose_scene(scene)

    @pytest.mark.parametrize(
        "transform,reason",
        [
            (np.eye(3), "invalid_transform"),
            (np.full((4, 4), np.nan), "invalid_transform"),
            (np.diag([1.0, 1, 0, 1]), "degenerate_transform"),
        ],
    )
    def test_rejects_invalid_placement_before_output_allocation(
        self, tetra, transform, reason, monkeypatch
    ):
        scene = ExpandedScene(
            (MeshResource("part", *tetra),), (Instance("part", transform),)
        )

        def allocate(*_args, **_kwargs):
            raise AssertionError("invalid placement allocated an output buffer")

        monkeypatch.setattr(np, "empty", allocate)
        with pytest.raises(GeometryError, match=reason):
            compose_scene(scene)

    @pytest.mark.parametrize("resource_count,instance_count", [(4097, 1), (1, 2049)])
    def test_rejects_oversized_scene_before_output_allocation(
        self, tetra, monkeypatch, resource_count, instance_count
    ):
        scene = ExpandedScene(
            tuple(MeshResource(str(index), *tetra) for index in range(resource_count)),
            (Instance("0", np.eye(4)),) * instance_count,
        )

        def allocate(*_args, **_kwargs):
            raise AssertionError("oversized scene allocated an output buffer")

        monkeypatch.setattr(np, "empty", allocate)
        with pytest.raises(GeometryError, match="scene_resource_limit"):
            compose_scene(scene)

    @pytest.mark.parametrize(
        "vertex_dtype,face_dtype", [(np.float32, np.int32), (np.int64, np.uint64)]
    )
    def test_normalizes_valid_source_dtypes(self, tetra, vertex_dtype, face_dtype):
        scene = ExpandedScene(
            (
                MeshResource(
                    "part", tetra[0].astype(vertex_dtype), tetra[1].astype(face_dtype)
                ),
            ),
            (Instance("part", np.eye(4)),),
        )

        points, faces = compose_scene(scene)

        assert points.dtype == np.float64
        assert faces.dtype == np.int64
        np.testing.assert_array_equal(points, tetra[0])
        np.testing.assert_array_equal(faces, tetra[1])

    def test_rejects_transformed_numeric_overflow(self, tetra):
        scene = ExpandedScene(
            (MeshResource("part", *tetra),),
            (Instance("part", np.diag([1e308, 1e308, 1e308, 1])),),
        )

        with pytest.raises(GeometryError, match="numeric_range"):
            compose_scene(scene)
