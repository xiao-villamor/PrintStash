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

        assert benchmark_report["schema_version"] == 4
        assert cube["name"] == "cube.stl"
        assert cube["render_median_ms"] > 0
        assert [sample["error"] for sample in cube["renders"]] == [None, None]
        assert [sample["strategy"] for sample in cube["renders"]] == ["full", "full"]
        assert cube["renders"][0]["output_bytes"] > 0

    def test_serializes_real_phase_evidence(self, benchmark_report: dict) -> None:
        sample = benchmark_report["measurements"][0]["renders"][0]
        phases = {phase["phase"]: phase for phase in sample["phase_stats"]}

        assert (
            phases["load"]["input_bytes"]
            == benchmark_report["measurements"][0]["input_bytes"]
        )
        assert phases["load"]["elapsed_ns"] > 0
        assert phases["render"]["elapsed_ns"] > 0
        assert phases["render"]["outcome"] == "completed"
        assert phases["render"]["output_bytes"] == sample["output_bytes"]

    def test_serializes_refused_phase_evidence(self, benchmark_report: dict) -> None:
        sample = benchmark_report["measurements"][-1]["renders"][0]
        phases = {phase["phase"]: phase for phase in sample["phase_stats"]}

        assert phases["load"]["outcome"] == "failed"
        assert phases["load"]["elapsed_ns"] > 0
        assert phases["load"]["output_bytes"] is None

    def test_identifies_sample_order(self, benchmark_report: dict) -> None:
        cube = benchmark_report["measurements"][0]

        assert [sample["sample_index"] for sample in cube["renders"]] == [1, 2]
        assert [sample["sample_index"] for sample in cube["representation_reads"]] == [
            1,
            2,
        ]
        assert benchmark_report["corpus_order"] == [
            measurement["name"] for measurement in benchmark_report["measurements"]
        ]

    def test_declares_applied_recipes(self, benchmark_report: dict) -> None:
        assert benchmark_report["request"] == {
            "include_geometry": False,
            "include_fingerprint": False,
            "include_thumbnail": True,
            "output_format": "WEBP",
        }
        assert benchmark_report["recipes"] == {
            "mesh_thumbnail": benchmark_report["mesh_thumbnail_recipe"],
            "mesh_metadata": None,
            "fingerprint": None,
        }

    def test_reports_environment_without_certifying_latency(
        self, benchmark_report: dict
    ) -> None:
        environment = benchmark_report["environment"]
        assert len(environment["commit"]) == 40
        assert environment["versions"]["trimesh"]
        assert environment["performance_gate_qualified"] is False
        assert "cpu_limit_read" in environment["cgroup"]
        assert benchmark_report["output_dimensions"]["width"] > 0
        assert benchmark_report["cold_runs"] == 2
        assert benchmark_report["warm_runs"] == 2

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
            "sample_order": "corpus_sequential_then_renders_then_publication_then_reads",
            "representation_cache": "final_render_published_to_private_local_storage",
            "temporary_storage": "per_file_removed_after_reads",
            "publication": "setup_excluded_from_sample_timings",
            "median_scope": "successful_attempts_only_raw_failures_retained",
            "phase_scope": "engine_spans_inclusive_not_additive_to_elapsed_ms",
            "phase_bytes": "known_logical_sizes_not_measured_io",
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

    def test_rejects_conflicting_corpus_profiles(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_thumbnails",
                "--quick",
                "--contract-corpus",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 2
        assert "not allowed with argument" in result.stderr

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


class TestContractCorpus:
    def test_reports_target_contract_separately_from_observed_renders(
        self, tmp_path: Path
    ) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_thumbnails",
                "--contract-corpus",
                "--cold-runs",
                "1",
                "--warm-runs",
                "1",
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "VAULT_DATA_ROOT": str(tmp_path)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        manifest = report["corpus_manifest"]
        assert (
            manifest["expectation_scope"] == "target_contract_not_observed_compliance"
        )
        fixtures = {entry["filename"]: entry for entry in manifest["fixtures"]}
        assert len(report["measurements"]) == len(fixtures) == 14
        for measurement in report["measurements"]:
            assert (
                measurement["input_sha256"] == fixtures[measurement["name"]]["sha256"]
            )
            assert len(measurement["renders"]) == 1
        cycle = fixtures["cyclic-components.3mf"]
        assert cycle["expectation"]["outcome"] == "refuse"
        assert report["environment"]["performance_gate_qualified"] is False
