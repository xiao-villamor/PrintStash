"""Qualification tooling runs real HTTP/DBOS privately and keeps failed evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.paths import BACKEND_DIR


class TestMain:
    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param(["--min-artifacts", "0"], id="zero-artifacts"),
            pytest.param(["--samples", "0"], id="zero-samples"),
            pytest.param(["--duration-seconds", "nan"], id="nonfinite-duration"),
            pytest.param(["--deadline-seconds", "0"], id="zero-deadline"),
            pytest.param(["--drain-seconds", "0"], id="zero-drain"),
            pytest.param(["--mode", "archive"], id="missing-archive"),
            pytest.param(["--concurrency", "3"], id="unknown-concurrency"),
            pytest.param(["--native-slots", "4"], id="slots-outside-load"),
            pytest.param(
                ["--mode", "load", "--native-slots", "0"], id="zero-native-slots"
            ),
            pytest.param(
                ["--mode", "load", "--native-slots", "9"], id="excessive-native-slots"
            ),
        ],
    )
    def test_rejects_invalid_requests(self, tmp_path, arguments):
        output = tmp_path / "never-created"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.qualify_ingestion",
                "--output-dir",
                str(output),
                *arguments,
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        assert completed.returncode == 2, completed.stderr
        assert "error:" in completed.stderr
        assert completed.stdout == ""
        assert not output.exists()

    def test_runs_a_private_smoke(self, tmp_path):
        user_vault = tmp_path / "user-vault"
        user_vault.mkdir()
        sentinel = user_vault / "library.sqlite"
        sentinel.write_bytes(b"user-database-must-never-open")
        environment_file = user_vault / ".env"
        environment_file.write_text("VAULT_DATA_ROOT=must-not-be-used\n")
        original = sentinel.read_bytes()
        output = tmp_path / "qualification"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.qualify_ingestion",
                "--output-dir",
                str(output),
                "--duration-seconds",
                "0",
                "--min-artifacts",
                "1",
                # This cold real-app smoke includes imports, schema setup and
                # DBOS launch. The observed cold start alone consumed ~40s;
                # allow startup plus two full imports and an explicit drain.
                "--deadline-seconds",
                "180",
                "--job-deadline-seconds",
                "60",
                "--drain-seconds",
                "30",
            ],
            cwd=user_vault,
            env={
                **os.environ,
                "PYTHONPATH": str(BACKEND_DIR),
                "VAULT_DB_URL": "sqlite:///" + str(sentinel),
                "VAULT_DATA_ROOT": str(user_vault),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )

        assert completed.returncode == 0, (
            completed.stderr + (output / "child.stderr.log").read_text()
        )
        report = json.loads((output / "report.json").read_text())
        samples = [
            json.loads(line)
            for line in (output / "observations.jsonl").read_text().splitlines()
        ]
        assert report["requested"]["deadline_seconds"] == 180
        assert report["requested"]["job_deadline_seconds"] == 60
        assert report["requested"]["drain_seconds"] == 30
        assert report["timing"]["startup_seconds"] >= 0
        assert report["timing"]["workload_seconds_available_after_startup"] > 0
        assert (
            report["timing"]["startup_seconds"]
            + report["timing"]["workload_seconds_available_after_startup"]
            + report["timing"]["drain_reserve_seconds"]
        ) == pytest.approx(180)
        assert report["elapsed_total_seconds"] < 180
        assert report["smoke_only"] is True
        assert report["summary"]["soak_thresholds_met"] is False
        assert report["summary"]["distinct_usable_artifacts"] == 1
        assert samples[0]["original_verified"] is True
        assert samples[0]["metadata_state"] == "ready"
        assert samples[0]["thumbnail_state"] == "ready"
        assert samples[0]["artifact_reused"] is False
        assert report["cleanup"]["quiescent"] is True
        assert report["workspace_retained"] is False
        assert not (output / "workspace").exists()
        assert sentinel.read_bytes() == original
        assert environment_file.read_text() == "VAULT_DATA_ROOT=must-not-be-used\n"
        assert set(user_vault.iterdir()) == {sentinel, environment_file}
        assert (output / "resources.jsonl").read_text().strip()
        assert report["warmup"]["purpose"] == "warmup"
        assert report["warm_resources"]["rss_current_tree_bytes"] > 0
        assert report["final_resources"]["temporary_file_count"] == 0
        assert report["final_resources"]["capacity_resources"] == []
        assert report["natural_drain_complete"] is True

    def test_retains_deadline_evidence(self, tmp_path):
        output = tmp_path / "qualification"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.qualify_ingestion",
                "--output-dir",
                str(output),
                "--duration-seconds",
                "0",
                "--min-artifacts",
                "1000",
                "--deadline-seconds",
                "0.01",
                "--drain-seconds",
                "1",
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
            timeout=45,
            check=False,
        )

        assert completed.returncode == 1, completed.stderr
        report = json.loads((output / "report.json").read_text())
        assert report["decision"] == "failed"
        assert report["summary"]["distinct_usable_artifacts"] == 0
        assert report["summary"]["soak_thresholds_met"] is False
        assert report["parent_failure"] == "TimeoutExpired"
        assert "qualification_parent_deadline" in report["errors"]
        assert not (output / "report.pending").exists()

    def test_verifies_a_reviewed_archive(self, tmp_path):
        import zipfile

        from scripts.mesh_benchmark_corpus import build_contract_corpus

        corpus = tmp_path / "corpus"
        build_contract_corpus(corpus)
        archive = tmp_path / "models.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.write(corpus / "cube-binary.stl", "folder/part.stl")
            zipped.write(corpus / "cube-mm.3mf", "other/part.3mf")
        original = archive.read_bytes()
        output = tmp_path / "archive-qualification"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.qualify_ingestion",
                "--mode",
                "archive",
                "--archive",
                str(archive),
                "--output-dir",
                str(output),
                "--deadline-seconds",
                "180",
                "--job-deadline-seconds",
                "60",
                "--drain-seconds",
                "30",
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
            timeout=240,
            check=False,
        )

        assert completed.returncode == 0, (
            completed.stderr + (output / "child.stderr.log").read_text()
        )
        report = json.loads((output / "report.json").read_text())
        results = report["archive"]["entries"]
        assert report["archive"]["logical_entries"] == 2
        assert report["archive"]["distinct_source_hashes"] == 2
        assert {entry["name"] for entry in results} == {
            "folder/part.stl",
            "other/part.3mf",
        }
        assert {entry["original_verified"] for entry in results} == {True}
        assert {entry["outcome"] for entry in results} == {"completed"}
        assert report["archive"]["source_unchanged"] is True
        assert report["archive"]["verification_complete"] is True
        assert all(entry["thumbnail_decoded"] for entry in results)
        assert report["archive"]["job"]["processed"] == 2
        assert len((output / "archive-entries.jsonl").read_text().splitlines()) == 2
        assert report["workspace_retained"] is False
        assert archive.read_bytes() == original

    def test_records_every_load_cell(self, tmp_path):
        output = tmp_path / "load-qualification"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.qualify_ingestion",
                "--mode",
                "load",
                "--native-slots",
                "1",
                "--samples",
                "1",
                "--output-dir",
                str(output),
                "--deadline-seconds",
                "180",
                "--job-deadline-seconds",
                "60",
                "--drain-seconds",
                "30",
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
            timeout=240,
            check=False,
        )

        assert completed.returncode == 0, (
            completed.stderr + (output / "child.stderr.log").read_text()
        )
        report = json.loads((output / "report.json").read_text())
        assert [cell["concurrency"] for cell in report["load_cells"]] == [1, 2, 4, 8]
        assert {
            sample["original_verified"]
            for cell in report["load_cells"]
            for sample in cell["samples"]
        } == {True}
        assert (
            len(
                {
                    sample["file_id"]
                    for cell in report["load_cells"]
                    for sample in cell["samples"]
                }
            )
            == 4
        )
        assert len((output / "observations.jsonl").read_text().splitlines()) == 5
        assert report["workspace_retained"] is False
        assert report["resource_review_required"] is True
        assert report["throughput_gate"]["four_to_one_ratio"] > 0
        assert report["throughput_gate"]["qualification"] == "not_qualified_N/A"
        assert report["throughput_gate"]["gate_passed"] is None
        assert report["physical_budget"]["native_slots"] == 1
        assert all(cell["heartbeat"]["samples"] > 0 for cell in report["load_cells"])
        assert all(
            cell["usable_artifacts_per_second"] > 0 for cell in report["load_cells"]
        )
        assert (
            "excludes socket latency" in report["load_cells"][0]["heartbeat"]["scope"]
        )


@pytest.fixture
def archive_client():
    """Controlled public responses exercise the verifier without DB seams."""
    import hashlib
    from io import BytesIO

    from fastapi import FastAPI, Response
    from fastapi.testclient import TestClient
    from PIL import Image

    original = b"the selected source bytes"
    image = BytesIO()
    Image.new("RGB", (10, 10)).save(image, format="WEBP")
    state = {
        "original": original,
        "thumbnail": image.getvalue(),
        "thumbnail_status": 200,
        "derivatives": [
            {"kind": kind, "state": "ready", "recipe_version": 1}
            for kind in ("metadata", "thumbnail")
        ],
    }
    app = FastAPI()

    @app.get("/api/v1/files/{file_id}/derivatives")
    def derivatives(file_id: int):
        return state["derivatives"]

    @app.get("/api/v1/files/{file_id}/download")
    def download(file_id: int):
        return Response(state["original"])

    @app.get("/api/v1/files/{file_id}/thumbnail")
    def thumbnail(file_id: int):
        return Response(state["thumbnail"], status_code=state["thumbnail_status"])

    entry = {
        "name": "source.stl",
        "size": len(original),
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    with TestClient(app) as client:
        yield client, state, entry


class TestVerifyArchiveArtifact:
    def test_completes_a_verified_source(self, archive_client):
        import time

        from scripts.qualify_ingestion import verify_archive_artifact

        client, _, entry = archive_client
        result = verify_archive_artifact(client, entry, 1, time.monotonic() + 1)

        assert result["outcome"] == "completed"
        assert result["original_verified"] is True
        assert result["thumbnail_decoded"] is True
        assert result["thumbnail_dimensions"] == [10, 10]

    def test_rejects_a_mismatched_original(self, archive_client):
        import time

        from scripts.qualify_ingestion import verify_archive_artifact

        client, state, entry = archive_client
        state["original"] = b"different bytes"
        result = verify_archive_artifact(client, entry, 1, time.monotonic() + 1)

        assert result["outcome"] == "failed"
        assert result["original_verified"] is False
        assert isinstance(result["error"], str)
        assert "original_hash_mismatch" in result["error"]

    def test_rejects_an_undecodable_thumbnail(self, archive_client):
        import time

        from scripts.qualify_ingestion import verify_archive_artifact

        client, state, entry = archive_client
        state["thumbnail"] = b"not an image"
        result = verify_archive_artifact(client, entry, 1, time.monotonic() + 1)

        assert result["outcome"] == "failed"
        assert result["thumbnail_decoded"] is False

    @pytest.mark.parametrize(
        "status", [201, 404], ids=["unexpected-success", "missing"]
    )
    def test_rejects_an_unavailable_thumbnail(self, archive_client, status):
        import time

        from scripts.qualify_ingestion import verify_archive_artifact

        client, state, entry = archive_client
        state["thumbnail_status"] = status
        result = verify_archive_artifact(client, entry, 1, time.monotonic() + 1)

        assert result["outcome"] == "failed"
        assert result["thumbnail_http_status"] == status

    def test_rejects_a_missing_expected_derivative(self, archive_client):
        from scripts import qualify_ingestion

        client, state, entry = archive_client
        state["derivatives"] = state["derivatives"][:1]
        instants = iter([0.0, 2.0])
        result = qualify_ingestion.verify_archive_artifact(
            client, entry, 1, 1.0, clock=lambda: next(instants), pause=lambda _: None
        )

        assert result["outcome"] == "timeout"
        assert result["error"] == "expected_derivatives_deadline"

    @pytest.mark.parametrize(
        "state_name,outcome",
        [
            pytest.param("failed", "failed", id="operational-failure"),
            pytest.param("skipped", "refused", id="typed-refusal"),
        ],
    )
    def test_preserves_terminal_unavailability(
        self, archive_client, state_name, outcome
    ):
        import time

        from scripts.qualify_ingestion import verify_archive_artifact

        client, state, entry = archive_client
        state["derivatives"][1].update(state=state_name, failure_reason="worker_failed")
        result = verify_archive_artifact(client, entry, 1, time.monotonic() + 1)

        assert result["outcome"] == outcome
        assert result["original_verified"] is True

    def test_rejects_a_missing_artifact(self, archive_client):
        import time

        from scripts.qualify_ingestion import verify_archive_artifact

        client, _, entry = archive_client
        result = verify_archive_artifact(client, entry, None, time.monotonic() + 1)

        assert result["outcome"] == "failed"
        assert result["error"] == "source_artifact_missing"


@pytest.fixture
def resource_vault(tmp_path):
    """Real empty application schema beside its private temporary workspaces."""
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel

    workspace = tmp_path / "workspace"
    root = workspace / "vault"
    root.mkdir(parents=True)
    db = root / "printstash.sqlite"
    engine = create_engine(f"sqlite:///{db}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    yield root, db


class TestResourceSnapshot:
    def test_observes_prepared_native_payloads_separately_from_history(
        self, resource_vault
    ):
        import os
        import threading

        from app.runtime.engine.dbos_engine import system_database_url
        from scripts.qualify_ingestion import resource_snapshot

        root, db = resource_vault
        prepared = root / "runtime" / "prepared" / "sources" / "owned-grant"
        native = prepared / "printstash-mesh-owned"
        native.mkdir(parents=True)
        (prepared / "source.3mf").write_bytes(b"prepared-source")
        (native / "partial.part").write_bytes(b"native-partial")
        outer = root.parent / "printstash-mesh-outer"
        outer.mkdir()
        (outer / "partial.part").write_bytes(b"outer-partial")
        persistent = root / "files"
        persistent.mkdir()
        (persistent / "model.stl").write_bytes(b"retained-artifact")
        inputs = root.parent / "inputs"
        inputs.mkdir()
        (inputs / "corpus.stl").write_bytes(b"qualification-input")
        dbos_url, _ = system_database_url(f"sqlite:///{db}")
        from pathlib import Path

        history = Path(dbos_url.removeprefix("sqlite:///"))
        history.write_bytes(b"retained-dbos-history")
        journal = Path(str(history) + "-wal")
        journal.write_bytes(b"retained-dbos-journal")
        pause = threading.Event()
        worker = threading.Thread(target=pause.wait, name="resource-test-thread")
        worker.start()
        try:
            actual = resource_snapshot(root, db)
        finally:
            pause.set()
            worker.join(timeout=1)

        assert actual["prepared_workspace_file_count"] == 2
        assert actual["prepared_workspace_bytes"] == len(
            b"prepared-source" + b"native-partial"
        )
        assert actual["native_temporary_file_count"] == 2
        assert actual["native_temporary_bytes"] == len(
            b"native-partial" + b"outer-partial"
        )
        assert actual["temporary_file_count"] == 3
        assert actual["temporary_bytes"] == len(
            b"prepared-source" + b"native-partial" + b"outer-partial"
        )
        assert actual["database_file_bytes"][db.name] == db.stat().st_size
        assert actual["database_file_bytes"][history.name] == history.stat().st_size
        assert actual["database_file_bytes"][journal.name] == journal.stat().st_size
        assert actual["database_counts"]["jobs"] == 0
        assert actual["database_counts"]["artifact_derivatives"] == 0
        assert actual["python_threads_current_count"] >= 2
        assert actual["threads_current_tree_count"] >= 2
        assert os.getpid() in actual["process_ids"]

    def test_closes_each_sqlite_observation(self, resource_vault, monkeypatch):
        import sqlite3

        from scripts import qualify_ingestion

        root, db = resource_vault
        original_connect = sqlite3.connect
        opened = []

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            if args and args[0] == f"file:{db}?mode=ro":
                opened.append(connection)
            return connection

        monkeypatch.setattr(qualify_ingestion.sqlite3, "connect", connect)
        for _ in range(3):
            qualify_ingestion.resource_snapshot(root, db)
        assert len(opened) == 3
        for connection in opened:
            with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
                connection.execute("SELECT 1")
