"""Compare the same real G-code ingests with and without local text backfill.

Acceptance is declared before either run: no failed ingests, backfill active
throughout the loaded sample, and at most 25% additional p95 completion latency.
Each phase is a fresh installation; duplicate detection cannot shortcut a run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from tests.fakes.process_metrics import sample_processes
from tests.fakes.search_scale import environment

MAX_P95_INCREASE = 0.25
GENERATION_STARTUP_SECONDS = 900


def backfill_overlapped(observations) -> bool:
    states = [
        row[side]
        for row in observations
        for side in ("generation_before", "generation_after")
    ]
    return bool(states) and (
        all(
            state["state"] == "building"
            and state["phase"] == "backfill"
            and state["id"] == states[0]["id"]
            for state in states
        )
        and all(
            before["indexed"] <= after["indexed"]
            for before, after in zip(states, states[1:], strict=False)
        )
        and states[-1]["indexed"] > states[0]["indexed"]
    )


def phase(directory: Path, model: Path, *, count: int, uploads: int, loaded: bool):
    environment(directory, model, "numpy")
    import numpy as np
    from fastapi.testclient import TestClient
    from printstash_core.inference.context import InferenceContext
    from printstash_core.search.passages import SearchSubject, SubjectType

    from app.core.config import ensure_dirs, settings
    from app.db.migrate import run_migrations
    from app.db.session import get_session_factory
    from app.main import app
    from app.modules.inference import model_cache
    from app.modules.inference.local import LocalEmbeddingProvider
    from app.modules.search import lexical_index
    from app.modules.search.passages import sync_subject
    from tests.factories.library import build_model
    from tests.factories.search_scale import replicate_models
    from tests.paths import FIXTURES_DIR

    ensure_dirs()
    run_migrations()
    corpus = json.loads((FIXTURES_DIR / "search/corpus.json").read_text())
    source = FIXTURES_DIR / "real_orca_ender3_benchy.gcode"
    original = source.read_bytes()
    metrics, results = {}, []
    sessions = get_session_factory()
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        prepared = client.post("/api/v1/setup/session")
        assert prepared.status_code == 200, prepared.text
        client.headers["X-PrintStash-Setup-CSRF"] = prepared.json()["csrf"]
        setup = client.post(
            "/api/v1/setup",
            json={
                "username": "owner",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(settings.data_dir),
                "thumb_dir": str(settings.thumb_dir),
            },
        )
        assert setup.status_code == 201, setup.text
        client.headers["Authorization"] = "Bearer " + setup.json()["access_token"]
        opted = client.patch(
            "/api/v1/search/settings",
            json={"enabled": True, "local_models_enabled": True},
        )
        assert opted.status_code == 200, opted.text
        with sessions.scoped_session() as session:
            seeds = []
            for row in corpus:
                subject = build_model(
                    session, row["name"], description=row["description"]
                )
                sync_subject(session, SearchSubject(SubjectType.MODEL, subject.id))
                seeds.append(subject)
            replicate_models(session, seeds, None, count=count)
            while lexical_index.rebuild_partition(session, limit=1024):
                pass
            assert lexical_index.capability(session) == "fts5"
            session.commit()
        installed = model_cache.inspect(model)
        provider = LocalEmbeddingProvider(
            sessions, model, installed.manifest.model_key, 1
        )
        provider.validate(context=InferenceContext.bounded(120))
        generation_id = None
        startup_seconds = None

        def generation():
            if generation_id is None:
                return None
            response = client.get(f"/api/v1/search/generations/{generation_id}")
            assert response.status_code == 200, response.text
            return response.json()

        if loaded:
            startup_started = time.monotonic()
            proposal = client.post(
                "/api/v1/search/generations",
                json={
                    "local_model_id": installed.id,
                    "index_backend": "numpy",
                    "auto_activate": False,
                },
            )
            assert proposal.status_code == 202, proposal.text
            generation_id = proposal.json()["id"]
            deadline = time.monotonic() + GENERATION_STARTUP_SECONDS
            while time.monotonic() < deadline:
                state = generation()
                if state["processed"] > 0:
                    break
                if state["state"] in {"failed", "cancelled", "retired"}:
                    raise AssertionError(state)
                time.sleep(0.1)
            else:
                raise AssertionError("backfill did not start")
            startup_seconds = time.monotonic() - startup_started
        sample_processes(metrics)
        cpu_before = {pid: row["cpu_seconds"] for pid, row in metrics.items()}
        started = time.perf_counter()
        for index in range(uploads):
            # Byte-identical ordered corpus in each installation. The comment
            # keeps distinct ingests from taking the existing-file shortcut.
            body = original + f"\n; PrintStash scale sample {index}\n".encode()
            before_generation = generation()
            before = time.perf_counter()
            response = client.post(
                "/api/v1/ingest/orca",
                files={"file": (f"sample-{index}.gcode", body, "text/plain")},
                data={"model_name": f"Scale upload {index}"},
            )
            assert response.status_code == 202, response.text
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                state = client.get(f"/api/v1/ingest/jobs/{response.json()['job_id']}")
                assert state.status_code == 200, state.text
                job = state.json()
                sample_processes(metrics)
                if job["state"] in {"completed", "failed", "duplicate"}:
                    break
                time.sleep(0.05)
            else:
                job = {"state": "timeout"}
            elapsed = time.perf_counter() - before
            after_generation = generation()
            results.append(
                {
                    "sample": index,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                    "seconds": elapsed,
                    "state": job["state"],
                    "generation_before": before_generation,
                    "generation_after": after_generation,
                }
            )
            if job["state"] != "completed":
                break
        sample_processes(metrics)
        for pid, row in metrics.items():
            row["measurement_cpu_seconds"] = row["cpu_seconds"] - cpu_before.get(pid, 0)
        values = [row["seconds"] for row in results]
        overlap = not loaded or backfill_overlapped(results)
        result = {
            "phase": "backfill" if loaded else "baseline",
            "architecture": platform.machine(),
            "platform": platform.platform(),
            "cpu_affinity": sorted(os.sched_getaffinity(0)),
            "seed_passages": count,
            "distinct_seed_texts": len(corpus),
            "onnx_threads": 1,
            "backfill_startup_seconds": startup_seconds,
            "backfill_startup_timeout_seconds": GENERATION_STARTUP_SECONDS,
            "model_space": provider.space.__dict__,
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "requested_uploads": uploads,
            "successful_uploads": sum(row["state"] == "completed" for row in results),
            "backfill_overlapped_all_uploads": overlap,
            "p50_seconds": float(np.percentile(values, 50)),
            "p95_seconds": float(np.percentile(values, 95)),
            "wall_seconds": time.perf_counter() - started,
            "processes": metrics,
            "observations": results,
        }
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def compare(directory: Path, model: Path, *, count: int, uploads: int):
    directory.mkdir(exist_ok=False)
    phases = {}
    for name in ("baseline", "backfill"):
        with (directory / f"{name}.log").open("w") as log:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.fakes.search_ingest_load",
                    "--directory",
                    str(directory / name),
                    "--model-directory",
                    str(model),
                    "--count",
                    str(count),
                    "--uploads",
                    str(uploads),
                    "--phase",
                    name,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=1800,
            )
        phases[name] = json.loads((directory / name / "result.json").read_text())
    baseline, backfill = phases["baseline"], phases["backfill"]
    same_corpus = [row["sha256"] for row in baseline["observations"]] == [
        row["sha256"] for row in backfill["observations"]
    ]
    ratio = backfill["p95_seconds"] / baseline["p95_seconds"]
    passed = (
        same_corpus
        and all(row["successful_uploads"] == uploads for row in phases.values())
        and backfill["backfill_overlapped_all_uploads"]
        and ratio <= 1 + MAX_P95_INCREASE
    )
    result = {
        "tolerance_fraction_declared_before_run": MAX_P95_INCREASE,
        "same_upload_corpus": same_corpus,
        "p95_ratio": ratio,
        "passed": passed,
        "phases": phases,
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--uploads", type=int, default=20)
    parser.add_argument("--phase", choices=("baseline", "backfill"))
    options = parser.parse_args()
    if not 32 <= options.count <= 100_000 or not 2 <= options.uploads <= 100:
        parser.error("count must be 32..100000 and uploads 2..100")
    if options.phase:
        result = phase(
            options.directory,
            options.model_directory,
            count=options.count,
            uploads=options.uploads,
            loaded=options.phase == "backfill",
        )
    else:
        result = compare(
            options.directory,
            options.model_directory,
            count=options.count,
            uploads=options.uploads,
        )
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result.get("passed", True) else 1)


if __name__ == "__main__":
    main()
