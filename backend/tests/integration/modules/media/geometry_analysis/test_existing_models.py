"""Real repository Models exercise the public path-based geometry capability."""

import hashlib

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media.fingerprints import extract
from app.modules.media.geometry_analysis import _load, verify_paths
from app.modules.similarity.configuration import SimilaritySettings
from tests.paths import TESTDATA_DIR, require_fixtures

CUBE = TESTDATA_DIR / "Calibration Cube.stl"
SPATULA = TESTDATA_DIR / "Spatula_Printables_IS.3mf"
BENCHY = TESTDATA_DIR / "benchy" / "3dbenchy.stl"
require_fixtures(CUBE, SPATULA, BENCHY)


class TestExistingModels:
    def test_fingerprints_complete_repository_benchy(self):
        before = hashlib.sha256(BENCHY.read_bytes()).hexdigest()
        prepared = _load(BENCHY, "stl", triangle_cap=SimilaritySettings().triangle_cap)

        result = extract(prepared)

        assert result.state == "ready"
        assert result.records[0].values["face_count"] > 200_000
        assert result.records[0].values["keys"] is not None
        assert result.records[0].values["recipe"]["complete_geometry"] is True
        assert hashlib.sha256(BENCHY.read_bytes()).hexdigest() == before

    def test_verifies_complete_repository_benchy(self, tmp_path):
        mesh = _load(
            BENCHY, "stl", triangle_cap=SimilaritySettings().triangle_cap
        ).whole_mesh
        mesh.apply_translation([40, 0, 0])
        exported = tmp_path / "benchy-translated.stl"
        exported.write_bytes(mesh.export(file_type="stl"))

        result = verify_paths(BENCHY, exported, first_type="stl", second_type="stl")

        assert result.evidence_class == "identical_geometry"
        assert result.exact_equivalence is True

    def test_describes_complete_repository_spatula(self):
        prepared = _load(SPATULA, "3mf", triangle_cap=SimilaritySettings().triangle_cap)

        result = extract(prepared)

        assert result.state == "ready"
        assert result.records[0].values["face_count"] == 6704
        assert result.records[0].values["hull_ratio"] > 0
        assert result.records[0].values["unavailable"] == []

    def test_verifies_calibration_cube_obj_export(self, tmp_path):
        before = hashlib.sha256(CUBE.read_bytes()).hexdigest()
        mesh = _load(CUBE, "stl", triangle_cap=200_000).whole_mesh
        exported = tmp_path / "calibration-cube.obj"
        exported.write_text(mesh.export(file_type="obj"))
        result = verify_paths(CUBE, exported, first_type="stl", second_type="obj")
        assert result.evidence_class == "identical_geometry"
        assert result.exact_equivalence is True
        assert hashlib.sha256(CUBE.read_bytes()).hexdigest() == before

    def test_verifies_spatula_resource_export(self, tmp_path):
        before = hashlib.sha256(SPATULA.read_bytes()).hexdigest()
        prepared = _load(SPATULA, "3mf", triangle_cap=200_000)
        exported = tmp_path / "spatula.stl"
        exported.write_bytes(prepared.whole_mesh.export(file_type="stl"))
        result = verify_paths(SPATULA, exported, first_type="3mf", second_type="stl")
        assert result.evidence_class == "identical_geometry"
        assert result.exact_equivalence is True
        assert hashlib.sha256(SPATULA.read_bytes()).hexdigest() == before

    def test_honors_explicit_smaller_analysis_budget(self):
        before = hashlib.sha256(BENCHY.read_bytes()).hexdigest()
        prepared = _load(BENCHY, "stl", triangle_cap=200_000)
        result = extract(prepared)
        assert prepared.complete is False
        assert prepared.failure_code == "sampled_oversized_source"
        assert len(prepared.whole_mesh.faces) <= 10_000
        assert result.state == "partial"
        assert result.records[0].values["keys"] is None
        assert result.records[0].values["face_count"] is None
        assert hashlib.sha256(BENCHY.read_bytes()).hexdigest() == before

    def test_rejects_spatula_when_triangle_budget_is_too_small(self):
        with pytest.raises(GeometryError, match="geometry_work_limit"):
            _load(SPATULA, "3mf", triangle_cap=100)
