"""Binary geometry references preserve transport association and fault isolation."""

import json
import os
import subprocess
import sys

import pytest

from tests.paths import BACKEND_DIR


@pytest.fixture(scope="module")
def binary_report(tmp_path_factory):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.fakes.compute_binary_probe",
            str(tmp_path_factory.mktemp("binary")),
        ],
        cwd=BACKEND_DIR,
        env={**os.environ, "VAULT_COMPUTE_MODE": "cpu"},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestBinaryRender:
    def test_reuses_geometry_across_cameras(self, binary_report):
        assert binary_report["first"] == "first"
        assert binary_report["second"] == "second"
        assert binary_report["uploaded"] > 0
        assert binary_report["delta"] == 0

    def test_isolates_malformed_geometry(self, binary_report):
        assert binary_report["invalid"] == ["invalid_input", "invalid_input"]
        assert binary_report["healthy"] == "healthy"

    def test_associates_concurrent_results(self, binary_report):
        assert binary_report["outputs"] == ["one", "two", "three", "four"]

    def test_recovers_from_input_capacity_refusal(self, binary_report):
        assert binary_report["capacity"] == "capacity"
        assert binary_report["recovered"] == "recovered"

    def test_uploads_again_after_cache_eviction(self, binary_report):
        assert binary_report["reuploaded"] == binary_report["uploaded"]
        assert binary_report["healthy"] == "healthy"
