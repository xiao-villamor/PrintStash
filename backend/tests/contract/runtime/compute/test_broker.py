"""Real IPC fault isolation; the native executor is deliberately injected."""

import json
import os
import subprocess
import sys

from tests.paths import BACKEND_DIR


class TestBroker:
    def test_isolates_bad_members_of_a_real_socket_batch(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_scheduler_probe",
                str(tmp_path),
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "VAULT_COMPUTE_MODE": "cpu"},
            capture_output=True,
            text=True,
            timeout=20,
        )

        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["batches"][0] > 1
        assert max(report["batches"]) <= 8
        assert report["results"][0]["vectors"] == [[0.0]]
        assert report["results"][1]["vectors"] == [[1.0]]
        assert report["results"][2]["code"] == "embedding_input_invalid"
        assert report["results"][3]["vectors"] == [[3.0]]
        assert report["singleton"]["vectors"] == [[4.0]]
        assert report["elapsed"] < 0.5

    def test_preserves_render_members_after_batch_refusal(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_render_scheduler_probe",
                str(tmp_path),
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "VAULT_COMPUTE_MODE": "cpu"},
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["results"] == [
            ["0", "1", "2", "3"],
            ["4", {"error": "invalid_input"}, "5", "6"],
            ["7", "capacity", "8", "9"],
        ]
        assert max(report["batches"]) > 1
        assert max(report["batches"]) <= 8
        assert report["singleton"] == "single"
        assert report["singleton_seconds"] < 0.5

    def test_refuses_staging_pressure_without_poisoning_owner(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_render_scheduler_probe",
                str(tmp_path),
                "staging",
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "VAULT_COMPUTE_MODE": "cpu"},
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report == {"refusal": "capacity", "healthy": "healthy", "cooldown": 0.0}
