"""Signed resource integrals retain orientation until the scene aggregate."""

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeUnavailable,
    VolumeUnavailableCause,
)

from app.modules.media.mesh_measurements import geometry_from_mesh, signed_mesh_integral


class TestSignedMeshIntegral:
    def test_preserves_negative_closed_integral(self):
        mesh = trimesh.creation.box(extents=[1, 2, 3])
        mesh.faces = mesh.faces[:, ::-1]
        assert signed_mesh_integral(mesh) == -6.0
        assert geometry_from_mesh(mesh).volume == VolumeUnavailable(
            VolumeUnavailableCause.NON_POSITIVE_INTEGRAL
        )

    def test_preserves_zero_signed_aggregate(self):
        outer = trimesh.creation.box()
        inner = outer.copy()
        inner.faces = inner.faces[:, ::-1]
        mesh = trimesh.util.concatenate([outer, inner])
        assert signed_mesh_integral(mesh) == 0.0
        assert geometry_from_mesh(mesh).volume == VolumeUnavailable(
            VolumeUnavailableCause.NON_POSITIVE_INTEGRAL
        )

    def test_preserves_large_offset_component_precision(self):
        first = trimesh.creation.box(extents=[1, 2, 3])
        first.apply_translation([1e15, 1e15, 1e15])
        second = trimesh.creation.box(extents=[1, 2, 3])
        second.apply_translation([-1e15, -1e15, -1e15])
        mesh = trimesh.util.concatenate([first, second])
        assert signed_mesh_integral(mesh) == 12.0
        assert geometry_from_mesh(mesh).volume == VolumeMeasured(12.0)

    @pytest.mark.parametrize(
        "integral",
        [
            pytest.param(float("nan"), id="nan"),
            pytest.param(float("inf"), id="infinite"),
        ],
    )
    def test_refuses_nonfinite_library_integral(self, monkeypatch, integral):
        from types import SimpleNamespace

        def nonfinite(*args, **kwargs):
            return SimpleNamespace(volume=integral)

        monkeypatch.setattr(trimesh.triangles, "mass_properties", nonfinite)
        mesh = trimesh.creation.box()
        assert signed_mesh_integral(mesh) == VolumeUnavailable(
            VolumeUnavailableCause.NONFINITE_INTEGRAL
        )

    def test_preserves_source_buffers(self):
        mesh = trimesh.creation.icosphere(subdivisions=4)
        vertices, faces = mesh.vertices.copy(), mesh.faces.copy()
        assert isinstance(signed_mesh_integral(mesh), float)
        np.testing.assert_array_equal(mesh.vertices, vertices)
        np.testing.assert_array_equal(mesh.faces, faces)

    def test_preserves_signed_integral_across_facet_batches(self, monkeypatch):
        cube = trimesh.creation.box(extents=[1, 2, 3])
        meshes = [cube] * 1025
        source = trimesh.util.concatenate(meshes)
        original = trimesh.triangles.mass_properties
        lengths = []

        def bounded_integral(triangles, **kwargs):
            lengths.append(len(triangles))
            return original(triangles, **kwargs)

        monkeypatch.setattr(trimesh.triangles, "mass_properties", bounded_integral)
        assert signed_mesh_integral(source) == 6150.0
        assert max(lengths) <= 4096
        assert len(lengths) == 4
