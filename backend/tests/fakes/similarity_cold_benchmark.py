"""Measure real cold library work, restarting the worker between bounded batches.

Run from backend with --root /tmp/new-library --count 1001. Every Artifact has
distinct bytes and is actually parsed/fingerprinted; no evidence is pre-seeded.
The corpus repeats repository designs, so this is not a claim about 1,001
independent designs. Candidate breadth and sample count are recorded explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1001)
    parser.add_argument("--batch", type=int, default=250)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if not 20 <= args.count <= 10_000 or not 1 <= args.batch <= 1000:
        parser.error("count must be 20..10000; batch must be 1..1000")
    if not args.worker:
        if args.root.exists():
            parser.error("root must be new; existing results are preserved")
        args.root.mkdir(parents=True)
    os.environ["VAULT_DB_URL"] = f"sqlite:///{args.root / 'library.sqlite'}"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    from sqlmodel import Session, SQLModel, col, func, select

    from app.core.config import _overlay
    from app.db.models import (
        File,
        FileType,
        GeometryFingerprint,
        SimilarityCandidate,
        SimilarityRun,
    )
    from app.db.session import get_engine, get_session_factory
    from app.modules.media.geometry_analysis import _load
    from app.modules.media.mesh_processing import _detect_memory_limit_bytes
    from app.modules.similarity import configuration, runs, service
    from app.modules.similarity.processing import SimilarityProcessor
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests._env import use_local_storage
    from tests.factories import build_file, build_model, build_user
    from tests.paths import CORE_PACKAGE_ROOT, TESTDATA_DIR

    _overlay["storage_identity"] = "a" * 64
    use_local_storage(args.root / "storage")
    engine = get_engine()
    report_path = args.root / "result.json"

    def save(report):
        report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    if args.worker:
        report = json.loads(report_path.read_text())
        processor = SimilarityProcessor(get_session_factory(), get_backend())
        tick = time.perf_counter()
        for unit in range(args.batch):
            assert processor.work_one(), "worker unexpectedly has no runnable unit"
            with Session(engine) as session:
                run = session.get(SimilarityRun, report["run_id"])
                assert run is not None
                state = service.project_run(run)
                if (
                    run.phase != "fingerprint"
                    and report["cold_fingerprints_seconds"] is None
                ):
                    report["cold_fingerprints_seconds"] = (
                        report["worker_seconds"] + time.perf_counter() - tick
                    )
                assert run.state != "failed", state
                if run.state in runs.TERMINAL:
                    break
            if (unit + 1) % 25 == 0:
                print(
                    json.dumps({"phase": run.phase, "counters": state["counters"]}),
                    flush=True,
                )
        report["worker_seconds"] += time.perf_counter() - tick
        report["worker_processes"] += 1
        report["peak_process_rss_bytes"] = max(
            report["peak_process_rss_bytes"],
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        )
        report["run"] = state
        save(report)
        return

    SQLModel.metadata.create_all(engine)
    sources = [
        TESTDATA_DIR / "Calibration Cube.stl",
        TESTDATA_DIR / "Spatula_Printables_IS.3mf",
        *sorted((CORE_PACKAGE_ROOT / "tests/fixtures/similarity").glob("*.stl")),
        TESTDATA_DIR / "benchy/3dbenchy.stl",
    ]
    prototypes = []
    for path in sources:
        mesh = _load(
            path,
            path.suffix[1:],
            triangle_cap=configuration.SimilaritySettings().triangle_cap,
        ).whole_mesh
        prototypes.append(mesh.export(file_type="stl"))
    report = {
        "mode": "cold_library_with_process_restarts",
        "artifacts": args.count,
        "source_designs": len(sources),
        "source_files": [
            {"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sources
        ],
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "effective_memory_limit_bytes": _detect_memory_limit_bytes(),
        },
        "settings": {"enabled": True, "sample_points": 256, "max_candidates": 1},
        "source_distribution": {},
        "cold_fingerprints_seconds": None,
        "worker_seconds": 0,
        "worker_processes": 0,
        "peak_process_rss_bytes": 0,
        "limitations": [
            "Byte-distinct exports of repository designs, not independent designs.",
            "Candidate breadth is one and verification uses 256 samples; default settings cost more.",
            "Measured host is not certified as a Raspberry Pi or NAS.",
        ],
    }
    with Session(engine) as session:
        actor = build_user(session, superuser=True)
        configuration.update_settings(session, actor, report["settings"])
        for index in range(args.count):
            source_index = (
                len(sources) - 1 if index % 100 == 0 else index % (len(sources) - 1)
            )
            name = sources[source_index].name
            report["source_distribution"][name] = (
                report["source_distribution"].get(name, 0) + 1
            )
            blob = prototypes[source_index]
            content = (
                f"PrintStash cold library {index}".encode().ljust(80, b" ") + blob[80:]
            )
            model = build_model(session, name=f"{name} {index}")
            file = build_file(
                session,
                model,
                file_type=FileType.STL,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            file.path = get_backend().blob_key(
                model.slug, file.version, file.original_filename
            )
            get_backend().write_stream(io.BytesIO(content), file.path)
            session.add(file)
            session.commit()
        assert (
            session.exec(select(func.count()).select_from(GeometryFingerprint)).one()
            == 0
        )
        report["run_id"] = runs.start(session, actor).id
        report["actor_id"] = actor.id
    del prototypes, mesh, blob, content
    save(report)
    for _ in range(args.count * 12 // args.batch + 20):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.similarity_cold_benchmark",
                "--root",
                str(args.root),
                "--count",
                str(args.count),
                "--batch",
                str(args.batch),
                "--worker",
            ],
            check=True,
        )
        report = json.loads(report_path.read_text())
        if report["run"]["state"] in runs.TERMINAL:
            break
    assert report["run"]["state"] == "completed", report["run"]
    memory_limit = report["hardware"]["effective_memory_limit_bytes"]
    assert memory_limit is None or report["peak_process_rss_bytes"] < memory_limit
    with Session(engine) as session:
        report["fingerprint_states"] = {
            state: count
            for state, count in session.exec(
                select(GeometryFingerprint.state, func.count())
                .where(GeometryFingerprint.component_index == 0)
                .group_by(GeometryFingerprint.state)
            )
        }
        assert report["fingerprint_states"] == {"ready": args.count}, report
        report["max_fingerprint_attempts"] = session.exec(
            select(func.max(GeometryFingerprint.attempts))
        ).one()
        assert report["max_fingerprint_attempts"] == 1
        for file in session.exec(select(File)):
            assert (
                hashlib.sha256(get_backend().read_bytes(file.path)).hexdigest()
                == file.sha256
            )
        report["source_hashes_preserved"] = True
        report["candidates"] = session.exec(
            select(func.count()).select_from(SimilarityCandidate)
        ).one()
        assert report["candidates"] > 0
        from app.db.models import User

        actor = session.get(User, report["actor_id"])
        tick = time.perf_counter()
        reference = session.exec(
            select(SimilarityCandidate)
            .where(col(SimilarityCandidate.exact_equivalence).is_(True))
            .order_by(SimilarityCandidate.id)
            .limit(1)
        ).one()
        cached = service.query_model(session, actor, reference.model_a_id)
        report["cached_response_seconds"] = time.perf_counter() - tick
        report["cached_candidates"] = len(cached["items"])
        assert report["cached_candidates"] > 0
    report["database_bytes"] = (args.root / "library.sqlite").stat().st_size
    save(report)
    print(json.dumps({"stage": "complete", "report": str(report_path)}), flush=True)


if __name__ == "__main__":
    main()
