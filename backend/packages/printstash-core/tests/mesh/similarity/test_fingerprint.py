"""Retrieval evidence survives re-export without becoming Model identity.

Synthetic surfaces have analytic dimensions and no redistribution restrictions.
A scalene tetrahedron is chiral; a cube deliberately has an ambiguous PCA frame.
The public array operation requires neither a parser nor database. The ingest /
review e2e remains pending until the application integration exists.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import numpy as np
import pytest

from printstash_core.mesh.similarity import (
    ALGORITHM_VERSION,
    FingerprintBudget,
    GeometryError,
    fingerprint_mesh,
)

from ...paths import FIXTURES_DIR


@pytest.fixture
def uneven_tetra(tetra):
    vertices, faces = tetra
    refined = faces[:1]
    # 81 triangles on the smallest face, leaving the other three faces intact.
    for _ in range(4):
        centers = vertices[refined].mean(axis=1)
        indices = np.arange(len(vertices), len(vertices) + len(refined))
        vertices = np.vstack((vertices, centers))
        refined = np.concatenate(
            [
                np.column_stack((refined[:, i], refined[:, (i + 1) % 3], indices))
                for i in range(3)
            ]
        )
    return vertices, np.vstack((refined, faces[1:]))


class TestFingerprintMesh:
    def test_reports_physical_surface_metrics(self, tetra):
        # Analytic cross products of the edge vectors give these four areas.
        area = (200 + np.sqrt(90900) + np.sqrt(362269) + np.sqrt(450589)) / 2

        result = fingerprint_mesh(*tetra)

        assert result.metrics.vertex_count == 4
        assert result.metrics.face_count == 4
        assert result.metrics.euler_characteristic == 2
        assert result.metrics.watertight is True
        assert result.metrics.winding_consistent is True
        assert result.metrics.surface_area == pytest.approx(area)
        assert result.metrics.bbox_dimensions == (10, 20, 30)
        assert result.metrics.volume == pytest.approx(1000)
        assert result.metrics.volume_reason is None
        assert result.metrics.area_volume_ratio == pytest.approx(area**1.5 / 1000)

    def test_preserves_input_arrays(self, tetra):
        vertices, faces = tetra
        original = (vertices.tobytes(), faces.tobytes())
        vertices.flags.writeable = False
        faces.flags.writeable = False

        fingerprint_mesh(vertices, faces)

        assert (vertices.tobytes(), faces.tobytes()) == original

    @pytest.mark.parametrize(
        "representation", ["soup", "reordered", "cyclic", "float32"], ids=str
    )
    def test_stabilizes_reexport_order(self, tetra, representation):
        vertices, faces = tetra
        # Float32 now loses real coordinate precision, as binary STL does.
        vertices = vertices * [1.1234567, 0.9876543, 1.0135791] + 0.01234567
        variants = {
            "soup": (vertices[faces].reshape((-1, 3)), np.arange(12).reshape((-1, 3))),
            "reordered": (vertices[::-1], (3 - faces)[::-1]),
            "cyclic": (vertices, np.roll(faces, 1, axis=1)),
            "float32": (vertices.astype(np.float32), faces),
        }
        original = fingerprint_mesh(vertices, faces)

        result = fingerprint_mesh(*variants[representation])

        assert result.keys is not None
        assert result.keys == original.keys
        assert len(set(result.keys.physical + result.keys.normalized)) == 8

    @pytest.mark.parametrize("angle", [0, 0.2, 0.7, 1.5, np.pi / 2, np.pi], ids=str)
    def test_stabilizes_rigid_transforms(self, tetra, angle):
        vertices, faces = tetra
        c, s = np.cos(angle), np.sin(angle)
        rotation = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        tilted = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
        original = fingerprint_mesh(vertices, faces)

        result = fingerprint_mesh(vertices @ rotation @ tilted + [100, -200, 50], faces)

        assert result.keys is not None
        assert result.keys == original.keys

    def test_recovers_across_base_grid_boundary(self, tetra):
        vertices, faces = tetra
        original = fingerprint_mesh(vertices, faces)
        vertices[0, 2] -= 0.0004

        perturbed = fingerprint_mesh(vertices, faces)

        assert original.keys is not None
        assert perturbed.keys is not None
        assert original.keys.normalized[2] != perturbed.keys.normalized[2]
        assert original.keys.normalized[3] == perturbed.keys.normalized[3]

    @pytest.mark.parametrize("scale", [0.5, 2, 25.4], ids=str)
    def test_retains_physical_scale(self, tetra, scale):
        vertices, faces = tetra
        original = fingerprint_mesh(vertices, faces)

        result = fingerprint_mesh(vertices * scale, faces)

        assert result.keys is not None
        assert original.keys is not None
        assert set(result.keys.physical).isdisjoint(original.keys.physical)
        assert result.keys.normalized == original.keys.normalized

    def test_separates_chiral_reflection(self, tetra):
        vertices, faces = tetra
        original = fingerprint_mesh(vertices, faces)

        reflected = fingerprint_mesh(vertices * [-1, 1, 1], faces)

        assert reflected.keys is not None
        assert original.keys is not None
        assert set(reflected.keys.physical + reflected.keys.normalized).isdisjoint(
            original.keys.physical + original.keys.normalized
        )

    def test_distinguishes_winding_from_reflection(self, tetra):
        vertices, faces = tetra
        original = fingerprint_mesh(vertices, faces)

        reversed_winding = fingerprint_mesh(vertices, faces[:, ::-1])

        assert original.keys is not None
        assert reversed_winding.keys is not None
        assert original.keys.physical[2:] == reversed_winding.keys.physical[2:]
        assert original.keys.normalized[2:] == reversed_winding.keys.normalized[2:]
        assert set(original.keys.normalized[:2]).isdisjoint(
            reversed_winding.keys.normalized[:2]
        )

    def test_cleans_analysis_copy(self, tetra):
        vertices, faces = tetra
        dirty_vertices = np.vstack((vertices, vertices[0], [100000, 0, 0]))
        dirty_faces = np.vstack((faces, faces[0], [4, 0, 1]))
        original = fingerprint_mesh(vertices, faces)

        cleaned = fingerprint_mesh(dirty_vertices, dirty_faces)

        assert cleaned.keys == original.keys
        assert cleaned.metrics == original.metrics

    def test_reports_ambiguous_frame(self):
        vertices = np.array(
            [
                [-1.0, -1, -1],
                [1, -1, -1],
                [1, 1, -1],
                [-1, 1, -1],
                [-1, -1, 1],
                [1, -1, 1],
                [1, 1, 1],
                [-1, 1, 1],
            ]
        )
        faces = np.array(
            [
                [0, 2, 1],
                [0, 3, 2],
                [4, 5, 6],
                [4, 6, 7],
                [0, 1, 5],
                [0, 5, 4],
                [1, 2, 6],
                [1, 6, 5],
                [2, 3, 7],
                [2, 7, 6],
                [3, 0, 4],
                [3, 4, 7],
            ]
        )

        result = fingerprint_mesh(vertices, faces)

        assert result.ambiguous_frame is True
        assert result.keys is None
        assert ("canonical_keys", "ambiguous_frame") in result.unavailable
        assert result.metrics.volume == pytest.approx(8)
        assert sum(result.d2.histogram) == pytest.approx(1)

    @pytest.mark.parametrize("defect", ["open", "inconsistent", "flat"], ids=str)
    def test_omits_unreliable_volume(self, tetra, defect):
        vertices, faces = tetra
        variants = {
            "open": (vertices, faces[1:]),
            "inconsistent": (vertices, np.vstack((faces[0, ::-1], faces[1:]))),
            "flat": (vertices * [1, 1, 0], faces),
        }
        reasons = {
            "open": "not_watertight",
            "inconsistent": "inconsistent_winding",
            "flat": "degenerate_volume",
        }

        result = fingerprint_mesh(*variants[defect])

        assert result.metrics.volume is None
        assert result.metrics.area_volume_ratio is None
        assert result.metrics.volume_reason == reasons[defect]

    @pytest.mark.parametrize("shape", ["empty", "collapsed", "collinear"], ids=str)
    def test_reports_degenerate_surface(self, shape):
        variants = {
            "empty": (np.empty((0, 3)), np.empty((0, 3), dtype=np.int64)),
            "collapsed": (np.zeros((3, 3)), np.array([[0, 1, 2]])),
            "collinear": (
                np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]]),
                np.array([[0, 1, 2]]),
            ),
        }

        with pytest.raises(GeometryError, match="^degenerate_surface$"):
            fingerprint_mesh(*variants[shape])

    @pytest.mark.parametrize("coordinate", [np.nan, np.inf, -np.inf], ids=str)
    def test_rejects_nonfinite_geometry(self, tetra, coordinate):
        vertices, faces = tetra
        vertices[0, 0] = coordinate

        with pytest.raises(GeometryError, match="^nonfinite_geometry$"):
            fingerprint_mesh(vertices, faces)

    @pytest.mark.parametrize(
        "indices",
        [[[-1, 1, 2]], [[0, 1, 4]], [[0.0, 1.5, 2.0]], [[0, 1, 2**63]]],
        ids=["negative", "outside", "float", "huge"],
    )
    def test_rejects_invalid_indices(self, tetra, indices):
        vertices, _ = tetra

        with pytest.raises(GeometryError, match="^invalid_faces$"):
            fingerprint_mesh(vertices, np.array(indices))

    @pytest.mark.parametrize("bad_array", ["vertices", "faces", "text"], ids=str)
    def test_rejects_invalid_array_shapes(self, tetra, bad_array):
        vertices, faces = tetra
        variants = {
            "vertices": (vertices[:, :2], faces, "invalid_vertices"),
            "faces": (vertices, faces[:, :2], "invalid_faces"),
            "text": (vertices.astype(str), faces, "invalid_vertices"),
        }
        v, f, code = variants[bad_array]

        with pytest.raises(GeometryError, match=f"^{code}$"):
            fingerprint_mesh(v, f)

    def test_accepts_mesh_at_budget(self, tetra):
        result = fingerprint_mesh(
            *tetra, budget=FingerprintBudget(max_vertices=4, max_faces=4)
        )

        assert result.metrics.face_count == 4

    @pytest.mark.parametrize(
        "budget",
        [FingerprintBudget(max_vertices=3), FingerprintBudget(max_faces=3)],
        ids=["vertices", "faces"],
    )
    def test_enforces_mesh_budget(self, tetra, budget):
        with pytest.raises(GeometryError, match="^resource_limit$"):
            fingerprint_mesh(*tetra, budget=budget)

    @pytest.mark.parametrize("limit", [0, -1, True, 600001], ids=str)
    def test_rejects_invalid_budget(self, tetra, limit):
        with pytest.raises(GeometryError, match="^invalid_budget$"):
            fingerprint_mesh(*tetra, budget=FingerprintBudget(max_vertices=limit))

    def test_rejects_numeric_overflow(self, tetra):
        vertices, faces = tetra

        with pytest.raises(GeometryError, match="^numeric_range$"):
            fingerprint_mesh(vertices * 1e200, faces)

    def test_computes_normalized_d2(self, tetra):
        result = fingerprint_mesh(*tetra)

        assert len(result.d2.histogram) == 64
        assert min(result.d2.histogram) >= 0
        assert sum(result.d2.histogram) == pytest.approx(1)
        assert result.d2.mean_distance > 0
        assert result.d2.sample_pairs == 8192
        assert result.d2.histogram_range == (0, 4)

    def test_repeats_content_seed(self, tetra):
        vertices, faces = tetra
        original = fingerprint_mesh(vertices, faces)

        reordered = fingerprint_mesh(vertices[::-1] + 100, (3 - faces)[::-1])

        assert reordered.d2 == original.d2

    @pytest.mark.parametrize("scale", [0.5, 2, 25.4], ids=str)
    def test_normalizes_d2_scale(self, tetra, scale):
        vertices, faces = tetra
        original = fingerprint_mesh(vertices, faces)

        scaled = fingerprint_mesh(vertices * scale, faces)

        assert scaled.d2.histogram == original.d2.histogram
        assert scaled.d2.seed == original.d2.seed
        assert scaled.d2.mean_distance == pytest.approx(
            original.d2.mean_distance * scale
        )

    def test_weights_d2_by_surface_area(self, tetra, uneven_tetra):
        original = fingerprint_mesh(*tetra)

        subdivided = fingerprint_mesh(*uneven_tetra)

        assert (
            np.abs(np.array(subdivided.d2.histogram) - original.d2.histogram).sum()
            < 0.12
        )
        assert subdivided.d2.mean_distance == pytest.approx(
            original.d2.mean_distance, rel=0.03
        )

    def test_stabilizes_surface_pca_after_retessellation(self, tetra, uneven_tetra):
        original = fingerprint_mesh(*tetra)

        subdivided = fingerprint_mesh(*uneven_tetra)

        assert subdivided.metrics.surface_eigenvalue_ratios == pytest.approx(
            original.metrics.surface_eigenvalue_ratios
        )

    def test_records_unavailable_descriptors(self, tetra):
        result = fingerprint_mesh(*tetra)

        assert result.state == "partial"
        assert dict(result.unavailable) == {
            "sh": "uncalibrated_basis",
            "view_hashes": "not_implemented",
            "convex_hull_ratio": "not_implemented",
            "volumetric_inertia": "not_implemented",
            "components": "not_implemented",
        }
        assert result.numpy_version == np.__version__

    def test_pins_algorithm_golden(self, tetra):
        golden = json.loads((FIXTURES_DIR / "similarity-tetra-v1.json").read_text())

        result = fingerprint_mesh(*tetra)

        assert (
            result.algorithm_version == ALGORITHM_VERSION == golden["algorithm_version"]
        )
        assert result.keys is not None
        assert list(result.keys.physical) == golden["physical"]
        assert list(result.keys.normalized) == golden["normalized"]
        assert result.d2.seed == golden["seed"]
        assert (
            hashlib.sha256(
                np.array(result.d2.histogram, dtype="<f4").tobytes()
            ).hexdigest()
            == golden["d2_sha256"]
        )

    def test_keeps_numpy_optional_at_import(self):
        program = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'numpy' or name.startswith('numpy.'):
        raise ImportError('mesh extra is absent')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from printstash_core.mesh.similarity import fingerprint_mesh
print(fingerprint_mesh.__name__)
"""

        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=False
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "fingerprint_mesh"
