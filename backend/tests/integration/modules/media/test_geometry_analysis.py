"""Path-based comparison contains malformed, partial and unavailable media inputs."""

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import geometry_analysis
from tests.factories.geometry import tetrahedron


@pytest.fixture
def mesh_path(tmp_path):
    path = tmp_path / "part.stl"
    path.write_bytes(tetrahedron().export(file_type="stl"))
    return path


class TestVerifyPaths:
    def test_verifies_connected_component_geometry(self, mesh_path):
        result = geometry_analysis.verify_paths(
            mesh_path,
            mesh_path,
            first_type="stl",
            second_type="stl",
            first_component=1,
            second_component=1,
            sample_points=256,
        )

        assert result.exact_equivalence is True
        assert result.evidence_class == "identical_geometry"

    @pytest.mark.parametrize("component", [-1, 2])
    def test_refuses_missing_components(self, mesh_path, component):
        with pytest.raises(GeometryError, match="component_unavailable"):
            geometry_analysis.verify_paths(
                mesh_path,
                mesh_path,
                first_type="stl",
                second_type="stl",
                first_component=component,
                sample_points=256,
            )

    @pytest.mark.parametrize("cap", [99, 200001])
    def test_refuses_invalid_triangle_caps(self, mesh_path, cap):
        with pytest.raises(GeometryError, match="invalid_triangle_cap"):
            geometry_analysis.verify_paths(
                mesh_path,
                mesh_path,
                first_type="stl",
                second_type="stl",
                triangle_cap=cap,
            )

    def test_refuses_malformed_mesh(self, mesh_path):
        mesh_path.write_bytes(b"not a mesh")

        with pytest.raises(GeometryError, match="invalid_geometry"):
            geometry_analysis.verify_paths(
                mesh_path, mesh_path, first_type="stl", second_type="stl"
            )

    def test_does_not_prove_partial_geometry(self, mesh_path):
        mesh_path.write_bytes(
            trimesh.creation.icosphere(subdivisions=2).export(file_type="stl")
        )

        result = geometry_analysis.verify_paths(
            mesh_path,
            mesh_path,
            first_type="stl",
            second_type="stl",
            triangle_cap=100,
            sample_points=256,
        )

        assert result.exact_equivalence is False
        assert result.evidence_class == "similar_shape"
        assert result.confidence < 1.0

    def test_refuses_oversized_non_stl(self, tmp_path):
        path = tmp_path / "part.obj"
        path.write_text(
            trimesh.creation.icosphere(subdivisions=2).export(file_type="obj")
        )

        with pytest.raises(GeometryError, match="geometry_work_limit"):
            geometry_analysis.verify_paths(
                path, path, first_type="obj", second_type="obj", triangle_cap=100
            )


class TestEmbeddingViews:
    def test_produces_distinct_opaque_rgb_views(self, mesh_path):
        views = geometry_analysis.embedding_views(
            mesh_path,
            file_type="stl",
            component_index=1,
            image_size=32,
            triangle_cap=100,
        )

        assert len(views) == 6
        assert len({view.rgb for view in views}) >= 4
        assert all(
            view.width == view.height == 32 and len(view.rgb) == 32 * 32 * 3
            for view in views
        )
        assert np.frombuffer(views[0].rgb, dtype=np.uint8).max() == 255

    @pytest.mark.parametrize("size", [31, 513])
    def test_refuses_invalid_render_dimensions(self, mesh_path, size):
        with pytest.raises(GeometryError, match="invalid_view_budget"):
            geometry_analysis.embedding_views(
                mesh_path,
                file_type="stl",
                component_index=0,
                image_size=size,
                triangle_cap=100,
            )

    def test_refuses_partial_render_embeddings(self, mesh_path):
        mesh_path.write_bytes(
            trimesh.creation.icosphere(subdivisions=2).export(file_type="stl")
        )

        with pytest.raises(GeometryError, match="embedding_requires_complete_geometry"):
            geometry_analysis.embedding_views(
                mesh_path,
                file_type="stl",
                component_index=0,
                image_size=32,
                triangle_cap=100,
            )
