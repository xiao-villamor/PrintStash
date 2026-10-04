"""Measurements retain source precision and explicit volume evidence. Signed integrals stay stable across bounded batches without changing source buffers or hiding winding failures."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
)

from app.modules.media import (
    mesh_measurements,
)


@pytest.fixture
def oriented_tetrahedra():
    vertices = np.array([[0, 0, 0], [3, 0, 0], [0, 2, 0], [0, 0, 1]], dtype=np.float64)
    faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])
    offsets = np.zeros((64, 3))
    offsets[:, 0] = 1e15
    offsets[::2, 0] = -1e15
    offsets[:, 0] += np.arange(64) * 8
    all_vertices = (vertices[None, :, :] + offsets[:, None, :]).reshape(-1, 3)
    all_faces = (faces[None, :, :] + 4 * np.arange(64)[:, None, None]).reshape(-1, 3)
    all_faces[:40] = all_faces[:40, ::-1]
    return trimesh.Trimesh(vertices=all_vertices, faces=all_faces, process=False)


class TestGeometryFromMesh:
    @pytest.mark.parametrize("edge", [0.001, 0.123456789, 123.456789])
    def test_preserves_measurement_precision(self, edge):
        mesh = trimesh.creation.box(extents=[edge, edge, edge])

        geometry = mesh_measurements.geometry_from_mesh(mesh).geometry

        for axis in ("x", "y", "z"):
            assert geometry[f"bbox_{axis}_mm"] == pytest.approx(edge, rel=1e-12, abs=0)
        assert geometry["volume_mm3"] == pytest.approx(edge**3, rel=1e-12, abs=0)

    @pytest.mark.parametrize(
        "volume", [float("inf"), float("nan")], ids=["infinite", "nan"]
    )
    def test_refuses_nonfinite_volume(self, monkeypatch, volume):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        monkeypatch.setattr(
            trimesh.triangles,
            "mass_properties",
            lambda *a, **kw: SimpleNamespace(volume=volume),
        )

        geometry = mesh_measurements.geometry_from_mesh(mesh).geometry

        assert geometry["volume_mm3"] is None

    def test_geometry_from_mesh_handles_non_watertight_volume_error(
        self, monkeypatch
    ) -> None:
        mesh = trimesh.creation.box(extents=[1, 1, 1])
        mesh.unmerge_vertices()
        monkeypatch.setattr(
            trimesh.triangles,
            "mass_properties",
            lambda *a, **kw: (_ for _ in ()).throw(ValueError("non-watertight")),
        )
        geometry = mesh_measurements.geometry_from_mesh(mesh).geometry
        assert geometry["volume_mm3"] is None
        assert geometry["bbox_x_mm"] == 1.0

    def test_reports_inconsistent_winding_volume_evidence(self):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.faces[0] = mesh.faces[0][::-1]

        result = mesh_measurements.geometry_from_mesh(mesh)

        assert result.volume == VolumeUnavailable(
            VolumeUnavailableCause.INCONSISTENT_WINDING
        )

    def test_reports_open_surface_volume_evidence(self):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.faces = mesh.faces[:-1]

        result = mesh_measurements.geometry_from_mesh(mesh)

        assert result.volume == VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT)

    def test_reports_negative_integral_volume_evidence(self):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.invert()

        result = mesh_measurements.geometry_from_mesh(mesh)

        assert result.volume == VolumeUnavailable(
            VolumeUnavailableCause.NON_POSITIVE_INTEGRAL
        )

    @pytest.mark.parametrize("volume", [float("inf"), float("nan")], ids=str)
    def test_reports_nonfinite_integral_volume_evidence(self, monkeypatch, volume):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        monkeypatch.setattr(
            trimesh.triangles,
            "mass_properties",
            lambda *a, **kw: SimpleNamespace(volume=volume),
        )

        result = mesh_measurements.geometry_from_mesh(mesh)

        assert result.volume == VolumeUnavailable(
            VolumeUnavailableCause.NONFINITE_INTEGRAL
        )

    def test_reports_zero_integral_volume_evidence(self, monkeypatch):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        monkeypatch.setattr(
            trimesh.triangles,
            "mass_properties",
            lambda *a, **kw: SimpleNamespace(volume=0.0),
        )

        result = mesh_measurements.geometry_from_mesh(mesh)

        assert result.volume == VolumeUnavailable(
            VolumeUnavailableCause.NON_POSITIVE_INTEGRAL
        )

    def test_reports_small_measured_volume_evidence(self):
        mesh = trimesh.creation.box(extents=[0.001, 0.001, 0.001])

        result = mesh_measurements.geometry_from_mesh(mesh)

        assert isinstance(result.volume, VolumeMeasured)
        assert result.volume.value_mm3 == pytest.approx(1e-9, rel=1e-12, abs=0)

    def test_reports_missing_geometry_volume_evidence(self):
        result = mesh_measurements.geometry_from_mesh(None)

        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
        )

    def test_unexpected_volume_failure_retains_independent_measurements(
        self, monkeypatch, caplog
    ):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        vertices, faces = mesh.vertices.copy(), mesh.faces.copy()

        def fail_volume(*_args, **_kwargs):
            raise RuntimeError("integral kernel failed")

        monkeypatch.setattr(trimesh.triangles, "mass_properties", fail_volume)
        result = mesh_measurements.geometry_from_mesh(mesh)
        assert result.volume == VolumeUnavailable(
            VolumeUnavailableCause.MEASUREMENT_FAILED
        )
        assert result.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 10.0,
            "bbox_z_mm": 10.0,
            "volume_mm3": None,
            "triangle_count": 12,
        }
        record = next(
            record
            for record in caplog.records
            if record.getMessage() == "mesh volume measurement failed"
        )
        assert record.exc_info[0] is RuntimeError
        assert str(record.exc_info[1]) == "integral kernel failed"
        np.testing.assert_array_equal(mesh.vertices, vertices)
        np.testing.assert_array_equal(mesh.faces, faces)


class TestVolumeIntegralBatching:
    @pytest.mark.parametrize("batch_faces", [5, 12, 17])
    def test_preserves_signed_integrals_across_facet_batch_boundaries(
        self, monkeypatch, oriented_tetrahedra, batch_faces
    ):
        monkeypatch.setattr(
            mesh_measurements, "_VOLUME_INTEGRAL_BATCH_FACES", batch_faces
        )
        vertices, faces = (
            oriented_tetrahedra.vertices.copy(),
            oriented_tetrahedra.faces.copy(),
        )
        result = mesh_measurements.geometry_from_mesh(oriented_tetrahedra)
        # Each tetrahedron has signed volume +/- (3 * 2 * 1 / 6).
        assert result.volume == VolumeMeasured(64 - 2 * 10)
        assert result.geometry["triangle_count"] == 256
        np.testing.assert_array_equal(oriented_tetrahedra.vertices, vertices)
        np.testing.assert_array_equal(oriented_tetrahedra.faces, faces)

    def test_measures_closed_source_without_whole_mesh_geometry_copies(
        self, monkeypatch, oriented_tetrahedra
    ):
        def forbid_allocation(*_args, **_kwargs):
            raise AssertionError("full source geometry allocation")

        monkeypatch.setattr(trimesh.Trimesh, "copy", forbid_allocation)
        monkeypatch.setattr(trimesh.Trimesh, "triangles", property(forbid_allocation))
        result = mesh_measurements.geometry_from_mesh(oriented_tetrahedra)
        assert result.volume == VolumeMeasured(44.0)
