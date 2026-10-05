"""Independent feature gates plus real optional native candidate qualification."""

import json

import numpy as np
import pytest

from scripts import viewer_lod_pilot as pilot


class TestCandidate:
    @pytest.mark.parametrize(
        ("family", "state"),
        [
            (pilot.Family.SHARP_CUBE, "degenerate_triangles"),
            (pilot.Family.TORUS_HOLE, "measured"),
            (pilot.Family.OPEN_BORDER, "measured"),
            (pilot.Family.THIN_SOLID, "measured"),
            (pilot.Family.TINY_COMPONENT, "degenerate_triangles"),
        ],
    )
    @pytest.mark.parametrize("reduction", [0.5, 0.8])
    def test_reports_protected_family_reductions(self, family, state, reduction):
        source = pilot.source_mesh(family)
        vertices, faces = source.vertices.copy(), source.faces.copy()
        source_digest = pilot.array_digest(source)

        report = pilot.run_case(
            family, pilot.Limits(reduction, samples=128), source=source
        )

        assert report["candidate_version"] == "0.2.0"
        assert report["attained_faces"] > 0
        assert report["attained_faces"] <= len(source.faces)
        assert report["simplification_ms"] > 0
        assert report["verification_ms"] > 0
        assert report["source_array_sha256"] == source_digest
        assert report["source_array_sha256_after"] == source_digest
        assert (
            report["canonical_source_fingerprint_sha256"]
            == report["canonical_source_fingerprint_sha256_after"]
        )
        assert report["passes_declared_checks"] == (not report["quality"]["failures"])
        assert report["quality"]["state"] == state
        _assert_report_quality(report["quality"])
        assert report["parameters"]["preserve_border"] is True
        np.testing.assert_array_equal(source.vertices, vertices)
        np.testing.assert_array_equal(source.faces, faces)
        assert not source.vertices.flags.writeable
        assert not source.faces.flags.writeable


def _assert_report_quality(quality):
    if quality["state"] == "measured":
        assert quality["distances"]["source_points"] > 128
        assert quality["distances"]["target_points"] > 128
    else:
        assert quality["state"] == "degenerate_triangles"
        assert quality["zero_area_triangles"] > 0
        assert quality["failures"] == (pilot.Failure.DEGENERATE,)
        assert "distances" not in quality


def _damaged_mesh(family):
    import trimesh

    source = pilot.source_mesh(family)
    match family:
        case pilot.Family.SHARP_CUBE:
            return pilot.Mesh(source.vertices * 0.97, source.faces.copy())
        case pilot.Family.TORUS_HOLE:
            mesh = trimesh.creation.cylinder(radius=12, height=4, sections=64)
            return pilot.Mesh(
                np.asarray(mesh.vertices, dtype=np.float64),
                np.asarray(mesh.faces, dtype=np.int64),
            )
        case pilot.Family.OPEN_BORDER:
            mesh = trimesh.creation.cylinder(radius=4, height=10, sections=64)
            return pilot.Mesh(
                np.asarray(mesh.vertices, dtype=np.float64),
                np.asarray(mesh.faces, dtype=np.int64),
            )
        case pilot.Family.THIN_SOLID:
            return pilot.Mesh(source.vertices * (1, 1, 0.1), source.faces.copy())
        case pilot.Family.TINY_COMPONENT:
            return pilot.source_mesh(pilot.Family.SHARP_CUBE)


class TestQuality:
    @pytest.mark.parametrize(
        "tolerance", [0, -1e-8, float("nan"), float("inf"), 0.05, 1.0001e-6]
    )
    def test_rejects_invalid_boundary_tolerance(self, tolerance):
        source = pilot.source_mesh(pilot.Family.OPEN_BORDER)

        with pytest.raises(ValueError, match="strict position precision cap"):
            pilot.assess(
                pilot.Family.OPEN_BORDER,
                source,
                source,
                boundary_tolerance_mm=tolerance,
            )

    def test_accepts_float32_boundary_representation(self):
        source = pilot.source_mesh(pilot.Family.OPEN_BORDER)
        packed = source.vertices.astype(np.float32).astype(np.float64)
        target = pilot.Mesh(packed, source.faces.copy())

        quality = pilot.assess(
            pilot.Family.OPEN_BORDER,
            source,
            target,
            samples=64,
            boundary_tolerance_mm=1e-6,
        )

        assert quality.failures == ()
        assert isinstance(quality.boundary, pilot.ComparedBoundary)
        assert quality.boundary.mapping_bijective is True
        assert quality.boundary.mapped_edges_identical is True
        assert quality.boundary.tolerance_mm == 1e-6
        assert quality.boundary.max_position_delta_mm <= 1e-6
        assert quality.source.boundary_edges == quality.target.boundary_edges == 128
        assert quality.source.boundary_loops == quality.target.boundary_loops == 2
        assert np.linalg.norm(packed - source.vertices, axis=1).max() < 1e-6

    def test_rejects_displaced_boundary_profile(self):
        source = pilot.source_mesh(pilot.Family.OPEN_BORDER)
        packed = source.vertices.astype(np.float32).astype(np.float64)
        target = pilot.Mesh(packed + (0.001, 0, 0), source.faces.copy())

        quality = pilot.assess(
            pilot.Family.OPEN_BORDER,
            source,
            target,
            samples=64,
            boundary_tolerance_mm=1e-6,
        )

        assert pilot.Failure.BOUNDARY in quality.failures
        assert isinstance(quality.boundary, pilot.ComparedBoundary)
        assert quality.boundary.max_position_delta_mm > quality.boundary.tolerance_mm
        assert quality.boundary.mapping_bijective is True
        assert quality.boundary.mapped_edges_identical is True
        assert quality.source.boundary_edges == quality.target.boundary_edges == 128
        assert quality.source.boundary_loops == quality.target.boundary_loops == 2
        assert pilot.Failure.SURFACE not in quality.failures

    @pytest.mark.parametrize(
        ("vertices", "faces"),
        [
            (np.zeros((3, 2), dtype=np.float64), np.array([[0, 1, 2]], dtype=np.int64)),
            (
                np.full((3, 3), np.nan, dtype=np.float64),
                np.array([[0, 1, 2]], dtype=np.int64),
            ),
            (np.zeros((3, 3), dtype=np.float64), np.array([[0, 1, 3]], dtype=np.int64)),
        ],
    )
    def test_rejects_invalid_candidate_arrays(self, vertices, faces):
        source = pilot.source_mesh(pilot.Family.SHARP_CUBE)
        digest = pilot.array_digest(source)

        quality = pilot._candidate_quality(
            pilot.Family.SHARP_CUBE,
            source,
            vertices,
            faces,
            samples=64,
        )

        assert isinstance(quality, pilot.InvalidArrayQuality)
        assert quality.state == "invalid_arrays"
        assert quality.failures == (pilot.Failure.INVALID_ARRAYS,)
        assert quality.target_faces == 1
        assert quality.target_vertices == 3
        assert pilot.array_digest(source) == digest

    def test_invalid_source_stays_strict(self):
        source = pilot.Mesh(
            np.zeros((3, 3), dtype=np.float64),
            np.array([[0, 1, 2]], dtype=np.int64),
        )
        candidate = pilot.source_mesh(pilot.Family.SHARP_CUBE)

        with pytest.raises(ValueError, match="degenerate pilot triangle"):
            pilot.assess(pilot.Family.SHARP_CUBE, source, candidate, samples=64)

    def test_measures_triangle_interiors(self):
        mesh = pilot.Mesh(
            np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0]], dtype=np.float64),
            np.array([[0, 1, 2]], dtype=np.int64),
        )
        query = np.array([[3, 3, 0.1]], dtype=np.float64)

        observed = pilot.surface_distances(mesh, query)

        assert observed[0] == pytest.approx(0.1, abs=1e-12)
        assert np.linalg.norm(mesh.vertices - query, axis=1).min() > 4

    @pytest.mark.parametrize(
        ("family", "failure"),
        [
            (pilot.Family.SHARP_CUBE, pilot.Failure.CUBE),
            (pilot.Family.TORUS_HOLE, pilot.Failure.HOLE),
            (pilot.Family.OPEN_BORDER, pilot.Failure.BOUNDARY),
            (pilot.Family.THIN_SOLID, pilot.Failure.THICKNESS),
            (pilot.Family.TINY_COMPONENT, pilot.Failure.TINY),
        ],
    )
    def test_rejects_loss_of_protected_features(self, family, failure):
        source = pilot.source_mesh(family)
        damaged = _damaged_mesh(family)

        quality = pilot.assess(family, source, damaged, samples=64)

        assert failure in quality.failures
        assert (
            max(
                quality.distances.source_to_target_max,
                quality.distances.target_to_source_max,
            )
            > 0.01
        )

    @pytest.mark.parametrize("family", list(pilot.Family))
    def test_preserves_exact_reference_surfaces(self, family):
        source = pilot.source_mesh(family)

        quality = pilot.assess(family, source, source, samples=64)

        assert quality.failures == ()
        assert quality.distances.source_to_target_max < 1e-9
        assert quality.distances.target_to_source_max < 1e-9
        assert quality.source == quality.target


class TestLimits:
    @pytest.mark.parametrize(
        ("reduction", "samples", "aggression"),
        [
            (0, 512, 3),
            (1, 512, 3),
            (float("nan"), 512, 3),
            (0.5, 0, 3),
            (0.5, 2049, 3),
            (0.5, True, 3),
            (0.5, 512, 0),
            (0.5, 512, 8),
            (0.5, 512, float("inf")),
        ],
    )
    def test_rejects_unbounded_pilot_parameters(self, reduction, samples, aggression):
        with pytest.raises(ValueError, match="pilot limits"):
            pilot.Limits(reduction, samples, aggression)


class TestCLI:
    def test_writes_a_finite_machine_readable_report(self, tmp_path):
        path = tmp_path / "lod.json"

        exit_code = pilot.main(
            [
                "--output",
                str(path),
                "--family",
                "sharp_cube",
                "--reduction",
                "0.5",
                "--reduction",
                "0.8",
                "--samples",
                "64",
            ]
        )
        report = json.loads(path.read_text())

        assert exit_code == 0
        assert report["schema_version"] == 1
        assert report["runtime_integration"] is False
        assert report["production_adoption"] is False
        assert len(report["cases"]) == 2
        assert report["cases"][0]["parameters"]["reduction"] == 0.5
        assert report["cases"][1]["parameters"]["reduction"] == 0.8
        assert report["cases"][0]["candidate_version"] == "0.2.0"
        assert report["limitations"]

    def test_invalid_candidate_preserves_remaining_report_cases(self, tmp_path):
        path = tmp_path / "invalid-candidate.json"

        exit_code = pilot.main(
            [
                "--output",
                str(path),
                "--family",
                "sharp_cube",
                "--family",
                "open_border",
                "--reduction",
                "0.5",
                "--samples",
                "64",
            ]
        )
        report = json.loads(path.read_text())

        assert exit_code == 0
        assert len(report["cases"]) == 2
        assert report["cases"][0]["family"] == "sharp_cube"
        assert report["cases"][0]["quality"]["state"] == "degenerate_triangles"
        assert report["cases"][0]["quality"]["zero_area_triangles"] > 0
        assert report["cases"][0]["quality"]["failures"] == [
            "degenerate_candidate_triangles"
        ]
        assert (
            report["cases"][0]["attained_faces"]
            == report["cases"][0]["quality"]["target_faces"]
        )
        assert report["cases"][0]["passes_declared_checks"] is False
        assert report["cases"][1]["family"] == "open_border"
        assert report["cases"][1]["quality"]["state"] == "measured"
        assert [item["source_array_sha256"] for item in report["cases"]] == [
            item["source_array_sha256_after"] for item in report["cases"]
        ]
        assert [
            item["canonical_source_fingerprint_sha256"] for item in report["cases"]
        ] == [
            item["canonical_source_fingerprint_sha256_after"]
            for item in report["cases"]
        ]
