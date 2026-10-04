"""The materialized STL adapter preserves canonical source refusal categories."""

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import mesh_loading, mesh_policy


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
