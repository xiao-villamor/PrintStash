"""The corpus CLI measures real disposable mesh workers, including refusals."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import BACKEND_DIR


class TestPrivateWorkspace:
    def test_retains_workspace_when_teardown_fails(self):
        probe = """
from contextlib import contextmanager
from scripts import bench_mesh_pipeline as cli
original_export = cli.export_private_settings
def export(settings):
    original_export(settings)
    from scripts import benchmark_ingestion
    original_client = benchmark_ingestion.TestClient
    @contextmanager
    def client(app):
        with original_client(app) as running:
            yield running
        raise RuntimeError("injected_teardown_failure")
    benchmark_ingestion.TestClient = client
cli.export_private_settings = export
raise SystemExit(cli.main())
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                probe,
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
                "PYTHONPATH": str(BACKEND_DIR),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 1, result.stderr
        assert "injected_teardown_failure" in result.stderr
        marker = "Benchmark workspace retained: "
        workspace = Path(result.stderr.split(marker, 1)[1].splitlines()[0])
        try:
            assert (workspace / "vault" / "db" / "printstash.sqlite").is_file()
            assert result.stdout == ""
        finally:
            shutil.rmtree(workspace)

    @pytest.mark.parametrize("cleanup_fails", [False, True])
    def test_closes_lifespan_after_sample_exception(self, tmp_path, cleanup_fails):
        probe = """
import os
from pathlib import Path
from contextlib import contextmanager
from scripts import bench_mesh_pipeline as cli
original_export = cli.export_private_settings
def export(settings):
    original_export(settings)
    from scripts import benchmark_ingestion
    original_client = benchmark_ingestion.TestClient
    @contextmanager
    def client(app):
        with original_client(app) as running:
            yield running
        Path(os.environ["BENCHMARK_LIFESPAN_MARKER"]).write_text("closed")
    def measure(*args, **kwargs):
        raise RuntimeError("injected_sample_failure")
    benchmark_ingestion.TestClient = client
    benchmark_ingestion.measure_ingestion = measure
    if os.environ["BENCHMARK_INJECT_CLEANUP_FAILURE"] == "True":
        original_cleanup = benchmark_ingestion.cleanup_private_jobs
        def cleanup(client):
            original_cleanup(client)
            raise KeyboardInterrupt("injected_cleanup_failure")
        benchmark_ingestion.cleanup_private_jobs = cleanup
cli.export_private_settings = export
raise SystemExit(cli.main())
"""
        marker_file = tmp_path / "lifespan-closed"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                probe,
                "--mode",
                "ingestion",
                "--case",
                "cube-binary.stl",
            ],
            cwd=BACKEND_DIR,
            env={
                **os.environ,
                "PYTHONPATH": str(BACKEND_DIR),
                "BENCHMARK_LIFESPAN_MARKER": str(marker_file),
                "BENCHMARK_INJECT_CLEANUP_FAILURE": str(cleanup_fails),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 1, result.stderr
        assert "injected_sample_failure" in result.stderr
        marker = "Benchmark workspace retained: "
        workspace = Path(result.stderr.split(marker, 1)[1].splitlines()[0])
        try:
            assert marker_file.read_text() == "closed"
            assert (workspace / "vault" / "db" / "printstash.sqlite").is_file()
            assert (
                "private cleanup also failed: KeyboardInterrupt: injected_cleanup_failure"
                in result.stderr
            ) is cleanup_fails
        finally:
            shutil.rmtree(workspace)

    def test_retains_workspace_when_cleanup_fails(self):
        probe = """
from dataclasses import replace
from scripts import bench_mesh_pipeline as cli
original_export = cli.export_private_settings
def export(settings):
    original_export(settings)
    from scripts import benchmark_ingestion
    original_cleanup = benchmark_ingestion.cleanup_private_jobs
    def cleanup(client):
        observed = original_cleanup(client)
        return replace(observed, quiescent=False,
                       errors=(*observed.errors, "injected_cleanup_failure"))
    benchmark_ingestion.cleanup_private_jobs = cleanup
cli.export_private_settings = export
raise SystemExit(cli.main())
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                probe,
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
                "PYTHONPATH": str(BACKEND_DIR),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 1, result.stderr
        report = json.loads(result.stdout)
        workspace = Path(report["workspace_retained"])
        try:
            assert report["samples"][0]["outcome"] == "timeout"
            assert report["cleanup"]["quiescent"] is False
            assert "injected_cleanup_failure" in report["cleanup"]["errors"]
            assert (workspace / "inputs" / "cube-binary.stl").stat().st_size == 684
            assert (workspace / "vault" / "db" / "printstash.sqlite").is_file()
            assert f"Benchmark workspace retained: {workspace}" in result.stderr
        finally:
            shutil.rmtree(workspace)

    def test_retains_workspace_when_bootstrap_fails(self):
        probe = """
from scripts import bench_mesh_pipeline as cli
original_export = cli.export_private_settings
def export(settings):
    original_export(settings)
    from scripts import benchmark_ingestion
    def bootstrap(*args, **kwargs):
        raise RuntimeError("injected_bootstrap_failure")
    benchmark_ingestion.measure_ingestions = bootstrap
cli.export_private_settings = export
raise SystemExit(cli.main())
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                probe,
                "--mode",
                "ingestion",
                "--case",
                "cube-binary.stl",
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 1, result.stderr
        assert "injected_bootstrap_failure" in result.stderr
        marker = "Benchmark workspace retained: "
        workspace = Path(result.stderr.split(marker, 1)[1].splitlines()[0])
        try:
            assert (workspace / "inputs" / "cube-binary.stl").stat().st_size == 684
            assert result.stdout == ""
        finally:
            shutil.rmtree(workspace)


@pytest.fixture(scope="module")
def worker_benchmark_report(tmp_path_factory):
    root = tmp_path_factory.mktemp("pipeline-worker-report")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.bench_mesh_pipeline",
            "--mode",
            "worker",
            "--case",
            "cube-binary.stl",
            "--case",
            "binary-truncated.stl",
            "--runs",
            "1",
        ],
        cwd=BACKEND_DIR,
        env={
            **os.environ,
            "VAULT_DATA_ROOT": str(root),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestWorkerBenchmark:
    def test_pins_child_configuration_across_directories(self, tmp_path):
        child_cwd = tmp_path / "child"
        child_cwd.mkdir()
        marker = child_cwd / "owner" / "marker.py"
        marker.parent.mkdir()
        marker.touch()
        (child_cwd / ".env").write_text(
            "VAULT_MODEL_THUMBNAIL_WIDTH=1280\n"
            "VAULT_JOBS_DERIVE_NATIVE_CONCURRENCY=invalid\n"
        )
        private_cwd = tmp_path / "private"
        private_cwd.mkdir()
        probe = """
import hashlib, json, os, sys
from dataclasses import asdict
from pathlib import Path
from scripts.bench_mesh_pipeline import configure_private_vault, export_private_settings
from scripts.mesh_benchmark_corpus import build_contract_corpus
from scripts.benchmark_pipeline_contracts import InputIdentity
private = Path(sys.argv[1])
configure_private_vault(private / "vault")
os.chdir(private)
from app.core.config import settings
export_private_settings(settings)
from app.modules.media import mesh_isolation
mesh_isolation.application_file = sys.argv[2]
from scripts.benchmark_native import measure_worker
manifest = build_contract_corpus(private / "inputs")
entry = next(item for item in manifest.fixtures if item.filename == "cube-binary.stl")
sample = measure_worker(private / "inputs" / entry.filename, InputIdentity(entry.filename, entry.sha256, entry.input_bytes, 1))
print(json.dumps({"sample": asdict(sample), "width": settings.model_thumbnail_width}))
"""
        result = subprocess.run(
            [sys.executable, "-c", probe, str(private_cwd), str(marker)],
            cwd=BACKEND_DIR,
            env={
                **os.environ,
                "PYTHONPATH": str(BACKEND_DIR),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["sample"]["outcome"] == "completed", report
        assert report["sample"]["output_dimensions"] == [
            report["width"],
            round(report["width"] * 3 / 4),
        ]
        assert report["width"] != 1280

    def test_reports_real_worker_costs(self, worker_benchmark_report):
        sample = worker_benchmark_report["samples"][0]
        assert sample["outcome"] == "completed"
        assert sample["output_bytes"] > 0
        assert len(sample["output_sha256"]) == 64
        assert sample["elapsed_ms"] > 0
        assert sample["supervision"]["elapsed_ns"] > 0
        assert sample["supervision"]["peak_tree_rss_bytes"] > 0
        assert sample["supervision"]["exit_cause"] == "exited_zero"
        assert sample["child_engine_elapsed_ms"] >= 0
        assert sample["child_peak_rss_bytes"] > 0
        assert sample["output_format"] == "WEBP"
        assert len(sample["output_dimensions"]) == 2
        assert {phase["phase"] for phase in sample["phase_stats"]} >= {
            "load",
            "measurements",
            "render",
        }

    def test_preserves_refused_worker_sample(self, worker_benchmark_report):
        sample = worker_benchmark_report["samples"][1]
        assert sample["outcome"] == "refused"
        assert sample["reason"] is not None
        assert sample["output_sha256"] is None
        assert sample["output_bytes"] == 0
        assert sample["elapsed_ms"] > 0
        assert sample["supervision"]["elapsed_ns"] > 0

    def test_describes_worker_measurement_boundaries(self, worker_benchmark_report):
        report = worker_benchmark_report
        assert report["schema_version"] == 1
        assert report["mode"] == "worker"
        assert report["protocol"]["worker_process"] == "fresh_per_sample"
        assert report["protocol"]["filesystem_cache"] == "uncontrolled"
        assert report["protocol"]["includes_http"] is False
        assert report["environment"]["performance_gate_qualified"] is False
        assert len(report["environment"]["commit"]) == 40
        assert report["recipes"]["metadata"] > 0
        assert report["recipes"]["thumbnail"] > 0
        expected = {
            item["filename"]: item for item in report["corpus_manifest"]["fixtures"]
        }
        for sample in report["samples"]:
            assert sample["input_sha256"] == expected[sample["name"]]["sha256"]
            assert sample["sample_index"] == 1

    def test_counts_every_observation(self, worker_benchmark_report):
        assert worker_benchmark_report["summary"] == {
            "sample_count": 2,
            "completed": 1,
            "refused": 1,
            "failed": 0,
            "timeout": 0,
        }

    @pytest.mark.parametrize(
        "runs", [pytest.param("0", id="zero"), pytest.param("-1", id="negative")]
    )
    def test_refuses_invalid_repetitions(self, runs):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_mesh_pipeline",
                "--mode",
                "worker",
                "--runs",
                runs,
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 2
        assert "run counts must be positive" in result.stderr

    def test_refuses_unknown_corpus_selector(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_mesh_pipeline",
                "--mode",
                "worker",
                "--case",
                "missing.stl",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 2
        assert "unknown corpus case" in result.stderr

    @pytest.mark.parametrize("deadline", ["-1", "nan", "inf"])
    def test_refuses_invalid_deadline(self, deadline):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_mesh_pipeline",
                "--mode",
                "worker",
                "--deadline-seconds",
                deadline,
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 2
        assert "deadline must be nonnegative" in result.stderr

    @pytest.mark.parametrize("profile", ["--full", "--download-external"])
    def test_refuses_incompatible_corpus_profiles(self, profile):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_mesh_pipeline",
                "--mode",
                "worker",
                profile,
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 2
        assert "full/download profiles require corpus v2" in result.stderr

    def test_executes_expanded_corpus(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_mesh_pipeline",
                "--mode",
                "worker",
                "--corpus",
                "v2",
                "--case",
                "binary-unreliable-normals.stl",
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["corpus_manifest"]["corpus_id"] == "mesh-contract-v2"
        assert report["samples"][0]["outcome"] == "completed", report["samples"]
        fixture = next(
            row
            for row in report["corpus_manifest"]["fixtures"]
            if row["filename"] == "binary-unreliable-normals.stl"
        )
        assert report["samples"][0]["input_sha256"] == fixture["sha256"]
