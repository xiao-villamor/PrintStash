"""Retained scenes preserve measured geometry and source-precision signed volume evidence.

Bounds/counts describe referenced placed surfaces. Signed volume is additive
for raw closed resources, while unresolved welding is an explicit later phase.
"""

from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeUnavailable,
    VolumeUnavailableCause,
)
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.components import (
    ExpandedScene,
    Instance,
    MeshResource,
)

from app.modules.media.mesh_measurements import geometry_from_mesh
from app.modules.media.mesh_resources import materialize_scene
from app.modules.media.scene_measurements import (
    SceneMeasurements,
    VolumeTopologyRequired,
    measure_scene,
)
from tests.factories.geometry import tetrahedron


@pytest.fixture
def resource() -> MeshResource:
    mesh = tetrahedron()
    return MeshResource("part", np.asarray(mesh.vertices), np.asarray(mesh.faces))


class TestMeasureScene:
    def test_measures_64_placements_from_unique_source(self, resource, monkeypatch):
        transforms = np.tile(np.eye(4), (64, 1, 1))
        transforms[:, 0, 3] = np.arange(64) * 50
        scene = ExpandedScene(
            (resource,), tuple(Instance("part", t) for t in transforms)
        )
        originals = (
            resource.vertices.tobytes(),
            resource.faces.tobytes(),
            transforms.tobytes(),
        )
        constructor = trimesh.Trimesh
        constructed_faces = []

        def unique_mesh(*args, **kwargs):
            constructed_faces.append(len(kwargs["faces"]))
            return constructor(*args, **kwargs)

        monkeypatch.setattr(trimesh, "Trimesh", unique_mesh)
        measured = measure_scene(scene)

        assert measured.geometry == {
            "bbox_x_mm": 3160.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 256,
            "volume_mm3": 64000.0,
        }
        assert constructed_faces == [4]
        assert (
            resource.vertices.tobytes(),
            resource.faces.tobytes(),
            transforms.tobytes(),
        ) == originals

    @pytest.mark.parametrize(
        "linear,expected_volume",
        [
            (np.diag([-2.0, 3, 4]), 24000.0),
            (np.array([[2.0, 1, 0], [0, 3, 1], [0, 0, 4]]), 24000.0),
        ],
        ids=["reflection", "shear"],
    )
    def test_preserves_affine_signed_integral(self, resource, linear, expected_volume):
        transform = np.eye(4)
        transform[:3, :3] = linear
        transform[:3, 3] = [100, -200, 300]
        scene = ExpandedScene((resource,), (Instance("part", transform),))
        legacy = geometry_from_mesh(materialize_scene(scene).whole_mesh)

        measured = measure_scene(scene)

        assert isinstance(measured.volume, VolumeMeasured)
        assert measured.volume.value_mm3 == pytest.approx(expected_volume)
        assert measured.geometry == pytest.approx(legacy.geometry)

    @pytest.mark.parametrize(
        "source_scale,placement_scale",
        [
            pytest.param(1e-100, 1e110, id="determinant-overflow"),
            pytest.param(1e100, 1e-110, id="determinant-underflow"),
            pytest.param(1e-110, 1e120, id="source-integral-underflow"),
            pytest.param(1e110, 1e-100, id="source-integral-overflow"),
        ],
    )
    def test_preserves_compensated_affine_volume(
        self, resource, source_scale, placement_scale
    ):
        vertices = resource.vertices * source_scale
        transform = np.diag([placement_scale, placement_scale, placement_scale, 1.0])
        scene = ExpandedScene(
            (MeshResource("part", vertices, resource.faces),),
            (Instance("part", transform),),
        )
        originals = vertices.tobytes(), resource.faces.tobytes(), transform.tobytes()
        expected = 1000.0 * (source_scale * placement_scale) ** 3
        materialized = geometry_from_mesh(materialize_scene(scene).whole_mesh)

        measured = measure_scene(scene)

        assert isinstance(materialized.volume, VolumeMeasured)
        assert materialized.volume.value_mm3 == pytest.approx(
            expected, rel=1e-12, abs=0
        )
        assert isinstance(measured.volume, VolumeMeasured)
        assert measured.volume.value_mm3 == pytest.approx(expected, rel=1e-12, abs=0)
        assert measured.geometry == pytest.approx(
            materialized.geometry, rel=1e-12, abs=0
        )
        assert (
            vertices.tobytes(),
            resource.faces.tobytes(),
            transform.tobytes(),
        ) == originals

    @pytest.mark.parametrize(
        "source_scale,placement_scale",
        [
            pytest.param(1e-100, 1e110, id="negative-determinant-overflow"),
            pytest.param(1e-110, 1e120, id="negative-source-integral-underflow"),
        ],
    )
    def test_preserves_compensated_negative_orientation(
        self, resource, source_scale, placement_scale
    ):
        vertices = resource.vertices * source_scale
        faces = resource.faces[:, ::-1].copy()
        transform = np.diag([placement_scale, placement_scale, placement_scale, 1.0])
        scene = ExpandedScene(
            (MeshResource("part", vertices, faces),), (Instance("part", transform),)
        )
        originals = vertices.tobytes(), faces.tobytes(), transform.tobytes()
        reference = geometry_from_mesh(materialize_scene(scene).whole_mesh)

        measured = measure_scene(scene)

        expected_volume = VolumeUnavailable(
            VolumeUnavailableCause.NON_POSITIVE_INTEGRAL
        )
        assert reference.volume == expected_volume
        assert measured.volume == expected_volume
        assert measured.geometry == pytest.approx(reference.geometry, rel=1e-12, abs=0)
        assert measured.geometry["triangle_count"] == 4
        assert measured.geometry["bbox_x_mm"] == pytest.approx(
            10.0 * source_scale * placement_scale, rel=1e-12, abs=0
        )
        assert measured.geometry["bbox_y_mm"] == pytest.approx(
            20.0 * source_scale * placement_scale, rel=1e-12, abs=0
        )
        assert measured.geometry["bbox_z_mm"] == pytest.approx(
            30.0 * source_scale * placement_scale, rel=1e-12, abs=0
        )
        assert (vertices.tobytes(), faces.tobytes(), transform.tobytes()) == originals

    @pytest.mark.parametrize("shift", [0.0, 10.0], ids=["coincident", "overlap"])
    def test_preserves_additive_closed_overlap_volume(self, resource, shift):
        transform = np.eye(4)
        transform[0, 3] = shift
        scene = ExpandedScene(
            (resource,), (Instance("part", np.eye(4)), Instance("part", transform))
        )
        whole = materialize_scene(scene).whole_mesh
        legacy = geometry_from_mesh(whole)

        measured = measure_scene(scene)

        assert whole.is_watertight
        assert whole.is_winding_consistent
        assert legacy.geometry["volume_mm3"] == pytest.approx(2000.0)
        assert measured.geometry == pytest.approx(legacy.geometry)
        assert isinstance(measured.volume, VolumeMeasured)

    def test_preserves_adjacent_closed_solid_volume(self):
        cube = trimesh.creation.box(extents=[2, 2, 2])
        resource = MeshResource(
            "cube", np.asarray(cube.vertices), np.asarray(cube.faces)
        )
        adjoining = np.eye(4)
        adjoining[0, 3] = 2
        scene = ExpandedScene(
            (resource,), (Instance("cube", np.eye(4)), Instance("cube", adjoining))
        )
        legacy = geometry_from_mesh(materialize_scene(scene).whole_mesh)

        measured = measure_scene(scene)

        assert legacy.geometry["volume_mm3"] == pytest.approx(16.0)
        assert measured.geometry == pytest.approx(legacy.geometry)

    def test_preserves_inverted_surface_volume_refusal(self, resource):
        scene = ExpandedScene(
            (MeshResource("part", resource.vertices, resource.faces[:, ::-1]),),
            (Instance("part", np.eye(4)),),
        )
        legacy = geometry_from_mesh(materialize_scene(scene).whole_mesh)

        measured = measure_scene(scene)

        assert legacy.geometry["volume_mm3"] is None
        assert measured.geometry == legacy.geometry
        assert measured.volume == VolumeUnavailable(
            VolumeUnavailableCause.NON_POSITIVE_INTEGRAL
        )

    def test_refuses_volume_for_inconsistent_closed_winding(self, resource):
        faces = resource.faces.copy()
        faces[0] = faces[0, ::-1]
        scene = ExpandedScene(
            (MeshResource("part", resource.vertices, faces),),
            (Instance("part", np.eye(4)),),
        )
        legacy = geometry_from_mesh(materialize_scene(scene).whole_mesh)

        measured = measure_scene(scene)

        assert legacy.geometry["volume_mm3"] is None
        assert measured.geometry == legacy.geometry
        assert measured.volume == VolumeUnavailable(
            VolumeUnavailableCause.INCONSISTENT_WINDING
        )

    def test_preserves_finite_measurements_when_volume_overflows(self, resource):
        transform = np.eye(4)
        transform[:3, :3] *= 1e110
        scene = ExpandedScene((resource,), (Instance("part", transform),))

        measured = measure_scene(scene)

        assert measured.geometry == pytest.approx(
            {
                "bbox_x_mm": 1e111,
                "bbox_y_mm": 2e111,
                "bbox_z_mm": 3e111,
                "triangle_count": 4,
                "volume_mm3": None,
            }
        )
        assert measured.volume == VolumeUnavailable(
            VolumeUnavailableCause.NONFINITE_INTEGRAL
        )

    def test_requires_whole_topology_for_closing_halves(self, resource):
        scene = ExpandedScene(
            (
                MeshResource("a", resource.vertices, resource.faces[:2]),
                MeshResource("b", resource.vertices, resource.faces[2:]),
            ),
            (Instance("a", np.eye(4)), Instance("b", np.eye(4))),
        )
        legacy = geometry_from_mesh(materialize_scene(scene).whole_mesh)

        measured = measure_scene(scene)

        assert legacy.geometry["volume_mm3"] == pytest.approx(1000.0)
        assert measured.geometry["volume_mm3"] is None
        assert measured.geometry["triangle_count"] == 4
        assert isinstance(measured.volume, VolumeTopologyRequired)

    def test_requires_whole_topology_for_coincident_unwelded_copies(self, resource):
        independent = MeshResource(
            "part",
            resource.vertices[resource.faces].reshape((-1, 3)),
            np.arange(12).reshape((-1, 3)),
        )
        scene = ExpandedScene((independent,), (Instance("part", np.eye(4)),) * 2)
        legacy = geometry_from_mesh(materialize_scene(scene).whole_mesh)

        measured = measure_scene(scene)

        assert legacy.geometry["volume_mm3"] is None
        assert measured.geometry["triangle_count"] == 8
        assert isinstance(measured.volume, VolumeTopologyRequired)

    def test_ignores_unused_source_coordinates(self, resource):
        source = MeshResource(
            "part", np.vstack((resource.vertices, [1e9, -1e9, 1e9])), resource.faces
        )
        scene = ExpandedScene((source,), (Instance("part", np.eye(4)),))

        measured = measure_scene(scene)

        assert measured.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 4,
            "volume_mm3": 1000.0,
        }

    def test_preserves_physical_measurements_at_large_placement(self, resource):
        transform = np.eye(4)
        transform[:3, 3] = [1e9, -1e9, 1e9]
        scene = ExpandedScene((resource,), (Instance("part", transform),))

        measured = measure_scene(scene)

        assert measured.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 4,
            "volume_mm3": 1000.0,
        }

    @pytest.mark.parametrize(
        "chunk",
        [
            pytest.param(1, id="one"),
            pytest.param(3, id="partial-final"),
            pytest.param(4, id="exact"),
        ],
    )
    def test_preserves_measurements_across_point_chunks(
        self, resource, monkeypatch, chunk
    ):
        from app.modules.media import scene_measurements

        monkeypatch.setattr(scene_measurements, "_POINT_CHUNK_SIZE", chunk)
        scene = ExpandedScene((resource,), (Instance("part", np.eye(4)),))
        measured = measure_scene(scene)
        assert measured.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 4,
            "volume_mm3": 1000.0,
        }

    def test_preserves_disconnected_resource_precision(self):
        positive = trimesh.creation.box(extents=[1, 2, 3])
        positive.apply_translation([1e15, 1e15, 1e15])
        negative = trimesh.creation.box(extents=[1, 2, 3])
        negative.apply_translation([-1e15, -1e15, -1e15])
        source = trimesh.util.concatenate([positive, negative])
        original = source.vertices.tobytes(), source.faces.tobytes()
        scene = ExpandedScene(
            (MeshResource("part", source.vertices, source.faces),),
            (Instance("part", np.eye(4)),),
        )
        measured = measure_scene(scene)
        assert measured.volume == VolumeMeasured(12.0)
        assert measured.geometry["triangle_count"] == 24
        assert (source.vertices.tobytes(), source.faces.tobytes()) == original

    def test_preserves_signed_cavity_contribution(self):
        outer = trimesh.creation.box(extents=[4, 4, 4])
        inner = trimesh.creation.box(extents=[2, 2, 2])
        scene = ExpandedScene(
            (
                MeshResource("outer", outer.vertices, outer.faces),
                MeshResource("inner", inner.vertices, inner.faces[:, ::-1]),
            ),
            (Instance("outer", np.eye(4)), Instance("inner", np.eye(4))),
        )
        measured = measure_scene(scene)
        assert measured.volume == VolumeMeasured(56.0)
        assert measured.measurements == geometry_from_mesh(
            materialize_scene(scene).whole_mesh
        )

    def test_preserves_dimensions_when_volume_kernel_fails(self, resource, monkeypatch):
        def broken_integral(*args, **kwargs):
            raise ArithmeticError("kernel_failed")

        monkeypatch.setattr(trimesh.triangles, "mass_properties", broken_integral)
        scene = ExpandedScene((resource,), (Instance("part", np.eye(4)),))
        measured = measure_scene(scene)
        assert measured.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 4,
            "volume_mm3": None,
        }
        assert measured.volume == VolumeUnavailable(
            VolumeUnavailableCause.MEASUREMENT_FAILED
        )

    def test_preserves_dimensions_when_volume_allocation_fails(
        self, resource, monkeypatch
    ):
        def unavailable_integral(*args, **kwargs):
            raise MemoryError("volume_budget")

        monkeypatch.setattr(trimesh.triangles, "mass_properties", unavailable_integral)
        scene = ExpandedScene((resource,), (Instance("part", np.eye(4)),))
        measured = measure_scene(scene)
        assert measured.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 4,
            "volume_mm3": None,
        }
        assert measured.volume == VolumeUnavailable(
            VolumeUnavailableCause.MEASUREMENT_FAILED
        )

    def test_rejects_nonfinite_placed_dimensions(self, resource):
        first = np.eye(4)
        first[0, 3] = -1e308
        second = np.eye(4)
        second[0, 3] = 1e308
        scene = ExpandedScene(
            (resource,), (Instance("part", first), Instance("part", second))
        )
        with pytest.raises(GeometryError, match="numeric_range"):
            measure_scene(scene)

    def test_requires_topology_resolution_before_projection(self):
        measured = SceneMeasurements(
            (0.0, 0.0, 0.0), (1.0, 2.0, 3.0), 4, VolumeTopologyRequired()
        )
        with pytest.raises(ValueError, match="topology_resolution_required"):
            _ = measured.measurements

    @pytest.mark.parametrize("shape", ["origin", "coplanar"], ids=str)
    def test_preserves_collapsed_source_dimensions(self, resource, shape):
        vertices = resource.vertices.copy()
        if shape == "origin":
            vertices[:] = 0
            expected_extents = (0.0, 0.0, 0.0)
        else:
            vertices[:, 2] = 0
            expected_extents = (10.0, 20.0, 0.0)
        scene = ExpandedScene(
            (MeshResource("part", vertices, resource.faces),),
            (Instance("part", np.eye(4)),),
        )
        original = vertices.tobytes(), resource.faces.tobytes()

        measured = measure_scene(scene)

        assert measured.geometry == {
            "bbox_x_mm": expected_extents[0],
            "bbox_y_mm": expected_extents[1],
            "bbox_z_mm": expected_extents[2],
            "triangle_count": 4,
            "volume_mm3": None,
        }
        assert measured.volume == VolumeUnavailable(
            VolumeUnavailableCause.NON_POSITIVE_INTEGRAL
        )
        assert (vertices.tobytes(), resource.faces.tobytes()) == original

    def test_preserves_dimensions_when_signed_total_overflows(self, resource):
        vertices = resource.vertices * 4e101
        scene = ExpandedScene(
            (MeshResource("part", vertices, resource.faces),),
            tuple(Instance("part", np.eye(4)) for _ in range(5)),
        )
        original = vertices.tobytes(), resource.faces.tobytes()

        individual = measure_scene(ExpandedScene(scene.resources, scene.instances[:1]))
        assert isinstance(individual.volume, VolumeMeasured)
        assert individual.volume.value_mm3 == pytest.approx(6.4e307, rel=1e-12)

        measured = measure_scene(scene)

        assert measured.geometry == pytest.approx(
            {
                "bbox_x_mm": 4e102,
                "bbox_y_mm": 8e102,
                "bbox_z_mm": 12e102,
                "triangle_count": 20,
                "volume_mm3": None,
            }
        )
        assert measured.volume == VolumeUnavailable(
            VolumeUnavailableCause.NONFINITE_INTEGRAL
        )
        assert (vertices.tobytes(), resource.faces.tobytes()) == original

    def test_preserves_dimensions_when_kernel_returns_nonfinite_integral(
        self, resource, monkeypatch
    ):
        def nonfinite_integral(*args, **kwargs):
            return SimpleNamespace(volume=float("nan"))

        monkeypatch.setattr(trimesh.triangles, "mass_properties", nonfinite_integral)
        scene = ExpandedScene((resource,), (Instance("part", np.eye(4)),))
        original = resource.vertices.tobytes(), resource.faces.tobytes()

        measured = measure_scene(scene)

        assert measured.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 4,
            "volume_mm3": None,
        }
        assert measured.volume == VolumeUnavailable(
            VolumeUnavailableCause.NONFINITE_INTEGRAL
        )
        assert (resource.vertices.tobytes(), resource.faces.tobytes()) == original

    def test_rejects_overflow_during_placement(self, resource):
        vertices = resource.vertices * 1e306
        transform = np.diag([100.0, 100.0, 100.0, 1.0])
        scene = ExpandedScene(
            (MeshResource("part", vertices, resource.faces),),
            (Instance("part", transform),),
        )
        original = vertices.tobytes(), resource.faces.tobytes(), transform.tobytes()

        with pytest.raises(GeometryError, match="numeric_range"):
            measure_scene(scene)

        assert (
            vertices.tobytes(),
            resource.faces.tobytes(),
            transform.tobytes(),
        ) == original


class TestSceneMeasurements:
    @pytest.mark.parametrize(
        "count",
        [0, -1, True, 1.0, 2_000_001],
        ids=["zero", "negative", "boolean", "float", "over-budget"],
    )
    def test_rejects_invalid_triangle_count(self, count):
        with pytest.raises(ValueError, match="invalid_scene_triangle_count"):
            SceneMeasurements(
                (0.0, 0.0, 0.0),
                (10.0, 20.0, 30.0),
                count,
                VolumeUnavailable(VolumeUnavailableCause.MEASUREMENT_FAILED),
            )

    def test_rejects_unknown_volume_outcome(self):
        with pytest.raises(TypeError, match="invalid_scene_volume_outcome"):
            SceneMeasurements((0.0, 0.0, 0.0), (10.0, 20.0, 30.0), 4, "unknown")

    @pytest.mark.parametrize(
        "minimum,maximum",
        [
            ((0.0, 0.0), (10.0, 20.0, 30.0)),
            ((0.0, 0.0, 0.0), (10.0, 20.0, float("inf"))),
            ((10.0, 0.0, 0.0), (0.0, 20.0, 30.0)),
            ((-1e308, 0.0, 0.0), (1e308, 20.0, 30.0)),
            ((False, 0.0, 0.0), (10.0, 20.0, 30.0)),
        ],
        ids=["short", "nonfinite", "inverted", "overflow", "boolean"],
    )
    def test_rejects_invalid_bounds(self, minimum, maximum):
        with pytest.raises(ValueError, match="invalid_scene_bounds"):
            SceneMeasurements(
                minimum,
                maximum,
                4,
                VolumeUnavailable(VolumeUnavailableCause.MEASUREMENT_FAILED),
            )
