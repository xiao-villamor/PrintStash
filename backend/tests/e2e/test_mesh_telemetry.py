"""A separate worker-role process exports real native costs in production logs.

An API-process scrape cannot see the registry of a standalone worker. These
flows consume stdout from the real text logger, without installing a test sink.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.factories import content
from tests.paths import BACKEND_DIR


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "cube.stl"
    path.write_bytes(content.binary_stl())
    return path


def _probe(mode: str, source: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.fakes.mesh_telemetry_probe", mode, str(source)],
        cwd=BACKEND_DIR,
        env={
            **os.environ,
            "VAULT_PROCESS_ROLE": "worker",
            "VAULT_LOG_LEVEL": "INFO",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


class TestMeshTelemetry:
    def test_worker_stdout_correlates_phase_costs(self, source: Path) -> None:
        result = _probe("metadata", source)

        assert result.returncode == 0, result.stderr
        supervision = json.loads(
            next(
                line.split("mesh_supervision ", 1)[1]
                for line in result.stdout.splitlines()
                if "mesh_supervision " in line
            )
        )
        phases = json.loads(
            next(
                line.split("mesh_phases ", 1)[1]
                for line in result.stdout.splitlines()
                if "mesh_phases " in line
            )
        )
        assert supervision["execution_id"] == phases["execution_id"]
        assert supervision["process_id"] == phases["process_id"]
        assert supervision["process_id"] != os.getpid()
        assert supervision["process_role"] == phases["process_role"] == "worker"
        assert supervision["peak_tree_rss_bytes"] > 0
        assert supervision["elapsed_ns"] > 0
        assert {stage["phase"] for stage in phases["stages"]} == {
            "admission",
            "load",
            "measurements",
        }
        assert str(source) not in result.stdout

    def test_worker_stdout_preserves_native_failure_cost(self, source: Path) -> None:
        result = _probe("failure", source)

        assert result.returncode == 0, result.stderr
        supervision = json.loads(
            next(
                line.split("mesh_supervision ", 1)[1]
                for line in result.stdout.splitlines()
                if "mesh_supervision " in line
            )
        )
        assert supervision["process_role"] == "worker"
        assert supervision["exit_cause"] == "exited_nonzero"
        assert supervision["reply_bytes"] == 7
        assert supervision["elapsed_ns"] > 0
        assert supervision["active_phase"] is None
        assert "mesh_phases " not in result.stdout
