"""Benchmark observations describe real accepted uploads and durable derivatives."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import BACKEND_DIR


@pytest.fixture(scope="module")
def ingestion_benchmark_sandbox(tmp_path_factory) -> Path:
    sandbox = tmp_path_factory.mktemp("benchmark-config-isolation")
    (sandbox / "benchmark-temporary").mkdir()
    return sandbox


@pytest.fixture(scope="module")
def ingestion_benchmark_report(ingestion_benchmark_sandbox):
    external = ingestion_benchmark_sandbox / "external-installation"
    dotenv = ingestion_benchmark_sandbox / ".env"
    dotenv.write_text(
        f"VAULT_DB_URL=sqlite:///{external}/dotenv.sqlite\nVAULT_SETUP_MODE=disabled\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.bench_mesh_pipeline",
            "--mode",
            "ingestion",
            "--case",
            "cube-binary.stl",
            "--case",
            "binary-truncated.stl",
            "--runs",
            "2",
            "--deadline-seconds",
            "60",
        ],
        cwd=ingestion_benchmark_sandbox,
        env={
            **os.environ,
            "PYTHONPATH": str(BACKEND_DIR),
            "TMPDIR": str(ingestion_benchmark_sandbox / "benchmark-temporary"),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "VAULT_DB_URL": f"sqlite:///{external}/inherited.sqlite",
            "VAULT_DATA_ROOT": str(external),
            "VAULT_DATA_DIR": str(external / "files"),
            "VAULT_SETUP_MODE": "disabled",
            "VAULT_PROCESS_ROLE": "worker",
            "VAULT_API_RUNS_JOBS": "false",
            "VAULT_SIMILARITY_ENABLED": "true",
        },
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def deadline_benchmark_sandbox(tmp_path_factory):
    return tmp_path_factory.mktemp("benchmark-deadline")


@pytest.fixture(scope="module")
def deadline_benchmark_report(deadline_benchmark_sandbox):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.bench_mesh_pipeline",
            "--mode",
            "ingestion",
            "--case",
            "cube-binary.stl",
            "--deadline-seconds",
            "0",
        ],
        cwd=BACKEND_DIR,
        env={
            **os.environ,
            "TMPDIR": str(deadline_benchmark_sandbox),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestIngestionBenchmark:
    def test_drains_private_work_before_workspace_removal(
        self, ingestion_benchmark_report, ingestion_benchmark_sandbox
    ):
        assert ingestion_benchmark_report["cleanup"]["quiescent"] is True
        assert ingestion_benchmark_report["cleanup"]["active_mutations"] == 0
        assert ingestion_benchmark_report["cleanup"]["active_readers"] is False
        assert ingestion_benchmark_report["cleanup"]["errors"] == []
        assert ingestion_benchmark_report["cleanup"]["elapsed_ms"] > 0
        assert ingestion_benchmark_report["workspace_retained"] is None
        assert (
            list(
                (ingestion_benchmark_sandbox / "benchmark-temporary").glob(
                    "printstash-pipeline-benchmark-*"
                )
            )
            == []
        )

    def test_drains_timed_out_private_work(
        self, deadline_benchmark_report, deadline_benchmark_sandbox
    ):
        assert deadline_benchmark_report["cleanup"]["quiescent"] is True
        assert deadline_benchmark_report["cleanup"]["active_mutations"] == 0
        assert deadline_benchmark_report["cleanup"]["active_readers"] is False
        assert deadline_benchmark_report["cleanup"]["errors"] == []
        assert deadline_benchmark_report["cleanup"]["cancelled"]
        assert deadline_benchmark_report["workspace_retained"] is None
        assert deadline_benchmark_report["samples"][0]["outcome"] == "timeout"
        assert (
            list(deadline_benchmark_sandbox.glob("printstash-pipeline-benchmark-*"))
            == []
        )

    def test_isolates_installation_configuration(
        self, ingestion_benchmark_report, ingestion_benchmark_sandbox
    ):
        assert not (ingestion_benchmark_sandbox / "external-installation").exists()
        assert (
            ingestion_benchmark_report["protocol"]["configuration"]
            == "isolated_production_defaults"
        )
        assert ingestion_benchmark_report["protocol"]["fingerprint_requested"] is False
        assert ingestion_benchmark_report["samples"][0]["outcome"] == "completed"

    def test_measures_real_derivative_availability(self, ingestion_benchmark_report):
        report = ingestion_benchmark_report
        sample = report["samples"][0]
        assert sample["outcome"] == "completed", sample
        assert 0 < sample["accepted_ms"] <= sample["artifact_observed_ms"]
        assert sample["artifact_observed_ms"] <= sample["metadata_observed_ms"]
        assert sample["artifact_observed_ms"] <= sample["thumbnail_visible_ms"]
        assert sample["thumbnail_visible_ms"] <= sample["elapsed_ms"]
        assert sample["metadata_state"] == "ready"
        assert sample["thumbnail_state"] == "ready"
        assert sample["file_id"] > 0
        assert sample["output_bytes"] > 0
        assert len(sample["output_sha256"]) == 64
        assert sample["output_format"] == "WEBP"
        assert sample["output_dimensions"] == [
            report["output_dimensions"]["width"],
            report["output_dimensions"]["height"],
        ]
        assert report["bootstrap_ms"] > 0
        assert report["protocol"]["http_transport"] == "asgi_no_network"
        assert (
            report["protocol"]["availability_timestamps"]
            == "first_observed_upper_bounds"
        )
        assert report["protocol"]["includes_database"] is True
        assert report["protocol"]["job_engine"] == "dbos"
        assert sample["original_verified"] is True
        assert sample["original_verification_ms"] > 0

    def test_retains_refused_derivative_observations(self, ingestion_benchmark_report):
        sample = ingestion_benchmark_report["samples"][2]
        assert sample["outcome"] == "refused", sample
        assert 0 < sample["accepted_ms"] <= sample["artifact_observed_ms"]
        assert sample["file_id"] > 0
        assert sample["reason"] is not None
        assert sample["thumbnail_state"] in {"failed", "skipped"}
        assert sample["thumbnail_visible_ms"] is None
        assert sample["output_bytes"] == 0
        assert sample["output_sha256"] is None
        assert sample["original_verified"] is True

    def test_distinguishes_source_presence_from_artifact_reuse(
        self, ingestion_benchmark_report
    ):
        first, second = ingestion_benchmark_report["samples"][:2]
        assert first["source_preexisting"] is False
        assert second["source_preexisting"] is True
        assert first["artifact_reused"] is False
        assert second["artifact_reused"] is False
        assert second["file_id"] != first["file_id"]
        assert first["sample_index"] == 1
        assert second["sample_index"] == 2
        assert second["input_sha256"] == first["input_sha256"]
        assert second["output_sha256"] == first["output_sha256"]
        assert second["outcome"] == "completed", second

    def test_preserves_flow_deadline_costs(self, deadline_benchmark_report):
        report = deadline_benchmark_report
        sample = report["samples"][0]
        assert sample["outcome"] == "timeout", sample
        assert sample["reason"] == "availability_deadline"
        assert 0 < sample["accepted_ms"] <= sample["elapsed_ms"]
        assert sample["artifact_observed_ms"] is None
        assert sample["metadata_observed_ms"] is None
        assert sample["thumbnail_visible_ms"] is None
        assert report["summary"]["timeout"] == 1
        assert sample["original_verified"] is None
        assert sample["original_verification_ms"] is None
        assert (
            report["protocol"]["timeout_carryover"]
            == "previous_jobs_can_remain_active_in_shared_vault"
        )
