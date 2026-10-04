"""Path-based comparison contains malformed, partial and unavailable media inputs."""

import hashlib
import json

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import geometry_analysis, mesh_render, mesh_resources
from tests.factories.geometry import tetrahedron, three_mf
from tests.fixtures.three_mf_projects import build_instanced_project
from tests.paths import FIXTURES_DIR


@pytest.fixture
def mesh_path(tmp_path):
    path = tmp_path / "part.stl"
    path.write_bytes(tetrahedron().export(file_type="stl"))
    return path


class TestVerifyPaths:
    def test_stops_verification_when_pair_budget_expires(self, mesh_path):
        with pytest.raises(GeometryError, match="verification_time_limit"):
            geometry_analysis.verify_paths(
                mesh_path,
                mesh_path,
                first_type="stl",
                second_type="stl",
                sample_points=256,
                verification_seconds=1e-9,
            )

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

    @pytest.mark.parametrize("cap", [99, 2_000_001, True, 100.0])
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

        with pytest.raises(GeometryError, match="invalid_source"):
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

    def test_bounds_a_3mf_by_the_callers_cap_before_composing_the_scene(
        self, tmp_path, monkeypatch
    ):
        """The cap is a load budget, not only a check on the finished mesh.

        300 placements of a 12-face part is 3,600 faces from a few KiB of XML, so
        the size estimate admits it. Loading against the global analysis ceiling
        instead of the caller's cap composed every placement and only then found
        it over budget (#259).
        """
        composed = []
        real = mesh_resources.compose_scene
        monkeypatch.setattr(
            mesh_resources,
            "compose_scene",
            lambda scene: composed.append(scene) or real(scene),
        )
        path = tmp_path / "instanced.3mf"
        path.write_bytes(build_instanced_project(300))

        with pytest.raises(GeometryError, match="scene_resource_limit"):
            geometry_analysis.verify_paths(
                path, path, first_type="3mf", second_type="3mf", triangle_cap=1000
            )

        assert composed == []


class TestEmbeddingViews:
    @pytest.mark.parametrize("profile", ["thumbnail", "multiview"])
    def test_ignores_unreferenced_vertices_in_visual_inputs(self, tmp_path, profile):
        from printstash_core.inference import EmbeddingSpace
        from printstash_core.search.visual_inputs import VisualRecipe

        mesh = tetrahedron()
        original_path = tmp_path / "original.3mf"
        original_path.write_bytes(three_mf(meshes={1: mesh}))
        mesh.vertices = np.concatenate([mesh.vertices, [[1e6, -1e6, 1e6]]])
        padded_path = tmp_path / "padded.3mf"
        padded_path.write_bytes(three_mf(meshes={1: mesh}))
        recipe = VisualRecipe.for_space(
            VisualRecipe.space(
                EmbeddingSpace("clip", "v1", 3, "text_image", "test"),
                image_size=32,
                profile=profile,
            )
        )

        original = geometry_analysis.visual_views(
            original_path, file_type="3mf", recipe=recipe, triangle_cap=100
        )
        padded = geometry_analysis.visual_views(
            padded_path, file_type="3mf", recipe=recipe, triangle_cap=100
        )

        assert original.thumbnail is not None
        assert len(original.views) == (6 if profile == "multiview" else 1)
        assert padded == original

    def test_loads_the_mesh_once_for_a_complete_visual_pass(
        self, mesh_path, monkeypatch
    ):
        from printstash_core.inference import EmbeddingSpace
        from printstash_core.search.visual_inputs import VisualRecipe

        loads = []
        preparations = []
        original = geometry_analysis._load
        prepare = mesh_render.prepare_mesh_render
        source = mesh_path.read_bytes()

        def observed(*args, **kwargs):
            loads.append(args[0])
            prepared = original(*args, **kwargs)
            loads[-1] = (
                args[0],
                prepared,
                prepared.whole_mesh.vertices.tobytes(),
                prepared.whole_mesh.faces.tobytes(),
            )
            return prepared

        def observed_preparation(mesh):
            preparations.append(mesh)
            return prepare(mesh)

        monkeypatch.setattr(geometry_analysis, "_load", observed)
        monkeypatch.setattr(mesh_render, "prepare_mesh_render", observed_preparation)
        recipe = VisualRecipe.for_space(
            VisualRecipe.space(
                EmbeddingSpace("clip", "v1", 3, "text_image", "test"),
                image_size=32,
                profile="multiview",
            )
        )
        result = geometry_analysis.visual_views(
            mesh_path, file_type="stl", recipe=recipe, triangle_cap=100
        )

        assert len(loads) == 1
        path, prepared, vertices, faces = loads[0]
        assert path == mesh_path
        assert preparations == [prepared.whole_mesh]
        assert prepared.whole_mesh.vertices.tobytes() == vertices
        assert prepared.whole_mesh.faces.tobytes() == faces
        assert mesh_path.read_bytes() == source
        assert len(result.views) == 6
        assert result.thumbnail.modality == "image"
        assert len(result.thumbnail.rgb) == 32 * 32 * 3
        assert all(view.width == 32 for view in result.views)

    def test_preserves_frozen_visual_input_hashes(self, mesh_path):
        from printstash_core.inference import EmbeddingSpace
        from printstash_core.search.visual_inputs import VisualRecipe

        baseline = json.loads(
            (FIXTURES_DIR / "media/visual-prepared-v1.json").read_text()
        )
        recipe = VisualRecipe.for_space(
            VisualRecipe.space(
                EmbeddingSpace("clip", "v1", 3, "text_image", "test"),
                image_size=baseline["image_size"],
                profile=baseline["profile"],
            )
        )
        source = mesh_path.read_bytes()
        assert hashlib.sha256(source).hexdigest() == baseline["source_sha256"]
        assert recipe.view_identity == baseline["view_identity"]

        result = geometry_analysis.visual_views(
            mesh_path,
            file_type="stl",
            recipe=recipe,
            triangle_cap=baseline["triangle_cap"],
        )

        assert result.thumbnail is not None
        assert result.thumbnail.rgb is not None
        assert (
            hashlib.sha256(result.thumbnail.rgb).hexdigest()
            == baseline["thumbnail_rgb_sha256"]
        )
        assert len(result.views) == 6
        assert all(view.rgb is not None for view in result.views)
        assert [
            hashlib.sha256(view.rgb).hexdigest() for view in result.views
        ] == baseline["views_rgb_sha256"]
        assert mesh_path.read_bytes() == source

    def test_reuses_component_preparation_for_embedding_views(
        self, mesh_path, monkeypatch
    ):
        original = geometry_analysis._load
        prepare = mesh_render.prepare_mesh_render
        loaded = []
        preparations = []
        source = mesh_path.read_bytes()

        def observed(*args, **kwargs):
            prepared = original(*args, **kwargs)
            resource = prepared.scene.resources[0]
            loaded.append(
                (resource, resource.vertices.tobytes(), resource.faces.tobytes())
            )
            return prepared

        def observed_preparation(mesh):
            preparations.append(mesh)
            return prepare(mesh)

        monkeypatch.setattr(geometry_analysis, "_load", observed)
        monkeypatch.setattr(mesh_render, "prepare_mesh_render", observed_preparation)
        views = geometry_analysis.embedding_views(
            mesh_path,
            file_type="stl",
            component_index=1,
            image_size=32,
            triangle_cap=100,
        )

        assert len(preparations) == len(loaded) == 1
        resource, vertices, faces = loaded[0]
        assert resource.vertices.tobytes() == vertices
        assert resource.faces.tobytes() == faces
        assert mesh_path.read_bytes() == source
        assert len(views) == 6
        assert all(len(view.rgb) == 32 * 32 * 3 for view in views)

    def test_refuses_decoded_thumbnail_without_rgb(self, monkeypatch):
        from printstash_core.inference import EmbeddingInput, images

        monkeypatch.setattr(
            images,
            "decode_image",
            lambda *_args: EmbeddingInput("text", text="wrong modality"),
        )

        with pytest.raises(GeometryError, match="embedding_view_failed"):
            geometry_analysis.thumbnail_input(b"encoded", 32)

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


class TestSharedSTLSampleRefusals:
    @pytest.mark.parametrize(
        "cause",
        ["invalid_source", "resource_limit", "source_changed", "source_unavailable"],
    )
    def test_verification_preserves_the_shared_reader_refusal(
        self, tmp_path, monkeypatch, cause
    ):
        from app.modules.media import mesh_policy, stl_fallback
        from app.modules.media.stl_reader import (
            InvalidSTL,
            STLBudgetExceeded,
            STLSourceChanged,
        )

        source = tmp_path / "refused.stl"
        source.write_bytes(b"source")
        failures = {
            "invalid_source": InvalidSTL,
            "resource_limit": STLBudgetExceeded,
            "source_changed": STLSourceChanged,
            "source_unavailable": OSError,
        }
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *a, **kw: True)

        def refused(*args, **kwargs):
            raise failures[cause]()

        monkeypatch.setattr(stl_fallback, "read_stl_sample", refused)

        with pytest.raises(GeometryError) as caught:
            geometry_analysis.verify_paths(
                source, source, first_type="stl", second_type="stl", triangle_cap=100
            )

        assert caught.value.code == cause
