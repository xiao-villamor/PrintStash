"""The materialized STL adapter preserves canonical source refusal categories."""

import numpy as np
import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import mesh_loading, mesh_policy
from tests.factories.content import binary_stl


class TestSTLLoading:
    @pytest.mark.parametrize("consumer", ["materialize"])
    @pytest.mark.parametrize("failure", ["invalid", "budget", "changed"])
    def test_retains_canonical_refusal_category(
        self, monkeypatch, canonical_stl_refusal
    ):
        source, limits, expected = canonical_stl_refusal
        monkeypatch.setattr(
            mesh_policy, "load_face_budget", lambda _suffix: limits.max_triangles
        )

        with pytest.raises(GeometryError) as caught:
            mesh_loading.load_mesh(source, file_type="stl")

        assert caught.value.code == expected.value

    def test_reports_unavailable_source(self, tmp_path):
        with pytest.raises(GeometryError, match="source_unavailable"):
            mesh_loading.load_mesh(tmp_path / "missing.stl", file_type="stl")


class TestSTLBounds:
    def test_reuses_canonical_stl_bounds(self, tmp_path):
        source = tmp_path / "mesh.stl"
        source.write_bytes(binary_stl(triangles=2))

        mesh = mesh_loading.load_mesh(source)

        np.testing.assert_array_equal(
            mesh.bounds, [mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)]
        )
        assert "referenced_vertices" not in mesh._cache.cache

    def test_invalidates_bounds_after_vertex_edits(self, tmp_path):
        source = tmp_path / "mesh.stl"
        source.write_bytes(binary_stl(triangles=2))
        mesh = mesh_loading.load_mesh(source)
        original = mesh.bounds.copy()

        mesh.vertices += [10, 20, 30]

        np.testing.assert_array_equal(mesh.bounds, original + [10, 20, 30])
