"""The native STEP result is a readable mesh plus finite millimetre evidence."""

import json

import numpy as np
import pytest

from app.modules.media import step_worker
from tests.paths import FIXTURES_DIR


class TestBrepProtocol:
    def test_writes_safe_native_result(self, tmp_path, monkeypatch):
        destination = tmp_path / "mesh.npz"
        monkeypatch.setenv("PRINTSTASH_STEP_BREP", "1")
        monkeypatch.setenv("PRINTSTASH_STEP_TRIANGLE_LIMIT", "200000")
        monkeypatch.setattr(
            step_worker.sys,
            "argv",
            [
                "step_worker",
                str(FIXTURES_DIR / "cascadio_material.stp"),
                str(destination),
            ],
        )

        assert step_worker.main() == 0

        with np.load(destination, allow_pickle=False) as mesh:
            assert mesh["vertices"].shape[1] == mesh["faces"].shape[1] == 3
            assert len(mesh["faces"]) > 0
            assert np.isfinite(mesh["vertices"]).all()
            assert np.max(np.ptp(mesh["vertices"], axis=0)) > 10
        evidence = json.loads(destination.with_suffix(".json").read_text())
        assert evidence["source_units"] == ["metre"]
        assert evidence["volume_mm3"] > 0
        assert evidence["recipe"]["linear_deflection_mm"] == pytest.approx(0.05)
        json.dumps(evidence, allow_nan=False)
