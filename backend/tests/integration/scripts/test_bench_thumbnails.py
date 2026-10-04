"""The benchmark executes the real engine and reports useful costs when it fails.

A fake that ignored the request hid a missing required engine field, while a
local dict lookup was presented as thumbnail cache performance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import BACKEND_DIR


@pytest.fixture(scope="module")
def benchmark_report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("benchmark")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.bench_thumbnails",
            "--quick",
            "--cold-runs",
            "2",
            "--warm-runs",
            "2",
        ],
        cwd=BACKEND_DIR,
        env={**os.environ, "VAULT_DATA_ROOT": str(root)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestMain:
    def test_executes_real_benchmark_cli(self, benchmark_report: dict) -> None:
        cube = benchmark_report["measurements"][0]

        assert benchmark_report["schema_version"] == 2
        assert cube["name"] == "cube.stl"
        assert cube["render_median_ms"] > 0
        assert [sample["error"] for sample in cube["renders"]] == [None, None]
        assert [sample["strategy"] for sample in cube["renders"]] == ["full", "full"]
        assert cube["renders"][0]["output_bytes"] > 0

    def test_reads_persisted_representation(self, benchmark_report: dict) -> None:
        cube = benchmark_report["measurements"][0]
        rendered = cube["renders"][-1]

        assert cube["representation_read_median_ms"] > 0
        assert len(cube["representation_reads"]) == 2
        assert [sample["output_sha256"] for sample in cube["representation_reads"]] == [
            rendered["output_sha256"],
            rendered["output_sha256"],
        ]
        assert [sample["output_bytes"] for sample in cube["representation_reads"]] == [
            rendered["output_bytes"],
            rendered["output_bytes"],
        ]

    def test_preserves_failed_render_cost(self, benchmark_report: dict) -> None:
        malformed = benchmark_report["measurements"][-1]

        assert malformed["name"] == "ambiguous-preview.3mf"
        assert len(malformed["renders"]) == 2
        assert malformed["renders"][0]["error"] is not None
        assert malformed["renders"][0]["elapsed_ms"] > 0
        assert malformed["renders"][0]["peak_rss_bytes"] > 0
        assert malformed["render_median_ms"] is None
        assert malformed["representation_reads"] == []
        assert malformed["representation_read_median_ms"] is None

    def test_describes_measurement_boundaries(self, benchmark_report: dict) -> None:
        assert benchmark_report["protocol"] == {
            "render": "uncached_engine_same_interpreter",
            "representation_read": "local_storage_delivery_plan_and_full_body_read",
            "filesystem_cache": "uncontrolled",
            "rss_scope": "process_lifetime_high_water_self",
            "includes_http": False,
            "includes_database": False,
            "includes_worker_startup": False,
        }
        assert benchmark_report["mesh_thumbnail_recipe"] > 0

    @pytest.mark.parametrize(
        "flag", ["--cold-runs", "--warm-runs"], ids=["render", "read"]
    )
    def test_rejects_nonpositive_repetitions(self, flag: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.bench_thumbnails", flag, "0"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        assert result.returncode == 2
        assert "run counts must be positive" in result.stderr

    def test_rejects_missing_external_model(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_thumbnails",
                "--external-model",
                str(tmp_path / "missing.stl"),
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        assert result.returncode == 2
        assert "external model must be an existing file" in result.stderr
