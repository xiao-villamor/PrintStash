"""Real repository Models exercise the public path-based geometry capability."""

import hashlib

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media.fingerprints import extract
from app.modules.media.geometry_analysis import _load, verify_paths
from tests.paths import TESTDATA_DIR, require_fixtures

CUBE = TESTDATA_DIR / "Calibration Cube.stl"
SPATULA = TESTDATA_DIR / "Spatula_Printables_IS.3mf"
BENCHY = TESTDATA_DIR / "benchy" / "3dbenchy.stl"
require_fixtures(CUBE, SPATULA, BENCHY)


class TestExistingModels:
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

    def test_marks_oversized_benchy_partial(self):
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
