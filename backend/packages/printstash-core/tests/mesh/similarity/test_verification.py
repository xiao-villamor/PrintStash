"""Machine evidence distinguishes physical variants without authorizing deletion.

These contracts exercise real registration and surface sampling. Equal counts or
quantized retrieval keys cannot substitute for full geometric equivalence.
"""

from __future__ import annotations

import numpy as np
import pytest

from printstash_core.mesh.similarity import verification
from printstash_core.mesh.similarity.fingerprint import GeometryError
from printstash_core.mesh.similarity.verification import verify_meshes


class TestVerifyMeshes:
    @pytest.mark.parametrize("scale", [1, 0.5, 2, 25.4], ids=str)
    def test_verifies_uniform_scale(self, tetra, scale):
        vertices, faces = tetra
        expected = {
            1: "identical_geometry",
            0.5: "rescaled",
            2: "rescaled",
            25.4: "rescaled",
        }

        result = verify_meshes(
            vertices, faces, vertices[::-1] * scale + 100, 3 - faces, sample_points=256
        )

        assert result.evidence_class == expected[scale]
        assert result.scale_factor == pytest.approx(scale)
        assert result.exact_equivalence
        assert result.confidence == 1
        transform = np.array(result.transform)
        restored = (vertices * scale + 100) @ transform[:3, :3].T + transform[:3, 3]
        np.testing.assert_allclose(restored, vertices, atol=1e-10)

    @pytest.mark.parametrize("scale", [1, 2], ids=str)
    def test_identifies_chiral_mirror(self, tetra, scale):
        vertices, faces = tetra
        expected = {1: "mirrored", 2: "rescaled_mirrored"}

        result = verify_meshes(
            vertices, faces, vertices * [-scale, scale, scale], faces, sample_points=256
        )

        assert result.evidence_class == expected[scale]
        assert result.mirrored
        assert not result.mirror_ambiguous
        assert np.linalg.det(np.array(result.transform)[:3, :3]) < 0

    def test_verifies_rotated_symmetric_cube(self, cube):
        vertices, faces = cube
        angle = 0.7
        rotation = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0],
                [np.sin(angle), np.cos(angle), 0],
                [0, 0, 1],
            ]
        )

        result = verify_meshes(
            vertices, faces, vertices @ rotation + 100, faces, sample_points=256
        )

        assert result.evidence_class == "identical_geometry"
        assert result.mirror_ambiguous

    def test_does_not_promote_quantized_collision(self, tetra):
        vertices, faces = tetra
        changed = vertices.copy()
        changed[0, 2] -= 0.0004

        result = verify_meshes(vertices, faces, changed, faces, sample_points=256)

        assert result.evidence_class != "identical_geometry"
        assert not result.exact_equivalence
        assert result.confidence < 1

    def test_excludes_partial_from_exact_evidence(self, tetra):
        result = verify_meshes(*tetra, *tetra, sample_points=256, partial=True)

        assert not result.exact_equivalence
        assert result.confidence < 1

    def test_retains_sampling_provenance(self, tetra):
        result = verify_meshes(*tetra, *tetra)

        assert result.sample_points == 5000
        assert result.evaluation_seeds == (15401, 15402)
        assert result.fit_seed == 154
        assert result.sampled_hausdorff > result.sampled_chamfer > 0
        assert result.voxel_iou == 1

    def test_rejects_unrelated_proportions(self, tetra):
        vertices, faces = tetra

        result = verify_meshes(
            vertices, faces, vertices * [1, 1, 0.3], faces, sample_points=256
        )

        assert result.evidence_class is None
        assert result.confidence == 0

    def test_recognizes_retessellated_surface(self, tetra, subdivide):
        result = verify_meshes(*tetra, *subdivide(*tetra))

        assert result.evidence_class == "remeshed"
        assert not result.exact_equivalence
        assert 0.9 < result.confidence < 1

    def test_recognizes_small_topology_repair(self, tetra, subdivide):
        vertices, faces = subdivide(*tetra, levels=3)
        # A single microfacet is missing from an otherwise equivalent shell.
        result = verify_meshes(*tetra, vertices, faces[:-1])

        assert result.evidence_class == "repaired"
        assert not result.exact_equivalence

    @pytest.mark.parametrize("factor", [1.002, 1.07])
    def test_near_shape_does_not_become_identity(self, tetra, factor):
        vertices, faces = tetra

        result = verify_meshes(*tetra, vertices * [1, 1, factor], faces)

        assert result.evidence_class == "similar_shape"
        assert result.confidence < 1
        assert not result.exact_equivalence

    def test_proves_ambiguity_on_octahedral_surface(self):
        vertices = np.r_[np.eye(3), -np.eye(3)]
        faces = np.array([[i, j, k] for i in (0, 3) for j in (1, 4) for k in (2, 5)])
        inward = np.linalg.det(vertices[faces]) < 0
        faces[inward] = faces[inward, ::-1]

        result = verify_meshes(vertices, faces, vertices, faces, sample_points=256)

        assert result.evidence_class == "identical_geometry"
        assert result.mirror_ambiguous

    def test_does_not_infer_mirror_history_from_surface_triangulation(self, cube):
        vertices, faces = cube

        result = verify_meshes(*cube, vertices * [-1, 1, 1], faces, sample_points=256)

        assert result.mirror_ambiguous
        assert result.evidence_class == "remeshed"
        assert result.confidence < 1

    def test_omits_unavailable_voxel_metric(self, tetra, monkeypatch):
        def unavailable(*args, **kwargs):
            raise GeometryError("voxel_resource_limit")

        monkeypatch.setattr(verification, "voxelize", unavailable)

        result = verify_meshes(*tetra, *tetra, sample_points=256)

        assert result.evidence_class == "identical_geometry"
        assert result.voxel_iou is None
        assert result.unavailable == (("voxel_iou", "voxel_resource_limit"),)

    def test_keeps_missing_mirror_evidence_explicit(self, cube, monkeypatch):
        real_voxelize = verification.voxelize
        calls = 0

        def exhausted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 2:
                raise GeometryError("voxel_resource_limit")
            return real_voxelize(*args, **kwargs)

        monkeypatch.setattr(verification, "voxelize", exhausted)

        result = verify_meshes(*cube, *cube, sample_points=256)

        assert result.exact_equivalence
        assert result.unavailable == (("mirror_ambiguity", "voxel_resource_limit"),)

    def test_retains_distance_in_physical_units(self, tetra):
        result = verify_meshes(*tetra, *tetra, sample_points=256)

        assert result.distance_normalization == "pca_bbox_diagonal"
        assert result.sampled_hausdorff_mm > result.sampled_hausdorff
        assert result.sampled_chamfer_mm > result.sampled_chamfer

    @pytest.mark.parametrize("count", [0, 255, 5001, True], ids=str)
    def test_rejects_invalid_sample_budget(self, tetra, count):
        with pytest.raises(GeometryError, match="invalid_sample_count"):
            verify_meshes(*tetra, *tetra, sample_points=count)
