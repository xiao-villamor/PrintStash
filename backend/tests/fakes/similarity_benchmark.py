"""Reproducible warm-index benchmark with real repository meshes.

Run from backend: uv run python -m tests.fakes.similarity_benchmark --root /tmp/similarity-bench --count 10000
The cached population contains distinct anisotropic variants of eight real designs,
then byte-distinct STL re-exports. It deliberately stresses dense candidate buckets.
Seeding cached evidence is NOT a measurement of a full cold library backfill.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import resource
import shutil
import time
from datetime import datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--designs", type=int, default=32)
    args = parser.parse_args()
    if not 40 <= args.count <= 100_000 or not 8 <= args.designs <= 128:
        parser.error("count must be 40..100000 and designs 8..128")
    if args.root.exists():
        parser.error(
            "root must be a new directory; existing benchmark data is preserved"
        )
    if shutil.disk_usage(args.root.parent).free < 3 * 1024**3:
        parser.error("at least 3 GiB of free space is required")
    args.root.mkdir(parents=True)
    os.environ["VAULT_DB_URL"] = f"sqlite:///{args.root / 'library.sqlite'}"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    from sqlmodel import Session, SQLModel, func, select

    from app.core.config import _overlay
    from app.db.models import (
        FileType,
        GeometryFingerprint,
        SimilarityCandidate,
        SimilarityRun,
    )
    from app.db.session import get_engine, get_session_factory
    from app.modules.media.fingerprints import extract
    from app.modules.media.geometry_analysis import _load, verify_paths
    from app.modules.media.mesh_resources import prepare_loaded_mesh
    from app.modules.similarity import (
        candidates,
        configuration,
        fingerprints,
        runs,
        service,
    )
    from app.modules.similarity.processing import SimilarityProcessor
    from app.modules.storage import artifact_content
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests._env import use_local_storage
    from tests.factories import build_file, build_model, build_user
    from tests.paths import CORE_PACKAGE_ROOT, TESTDATA_DIR

    _overlay["storage_identity"] = "a" * 64
    use_local_storage(args.root / "storage")
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    sources = [
        TESTDATA_DIR / "Calibration Cube.stl",
        TESTDATA_DIR / "Spatula_Printables_IS.3mf",
    ] + sorted((CORE_PACKAGE_ROOT / "tests/fixtures/similarity").glob("*.stl"))
    report: dict = {
        "mode": "warm_index_with_one_uncached_artifact",
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "artifacts": args.count,
        "distinct_geometry_designs": args.designs,
        "source_files": [
            {"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sources
        ],
        "full_library_cold_backfill_seconds": None,
        "limitations": [
            f"Cached population is prepared from {args.designs} measured design variants, not {args.count} independently analyzed uploads.",
            "Dense duplicate/variant buckets deliberately exercise shortlist truncation.",
            "This host is not a Raspberry Pi or NAS; no hardware target is certified.",
        ],
    }

    def save():
        (args.root / "result.json").write_text(
            json.dumps(
                report,
                indent=2,
                default=lambda value: (
                    value.isoformat() if isinstance(value, datetime) else str(value)
                ),
            )
            + "\n"
        )

    prototypes = []
    measurements = []
    started = time.perf_counter()
    for index in range(args.designs):
        path = sources[index % len(sources)]
        mesh = _load(
            path, "3mf" if path.suffix == ".3mf" else "stl", triangle_cap=200_000
        ).whole_mesh.copy()
        variant = index // len(sources)
        mesh.apply_scale([1 + 0.13 * variant, 1 + 0.07 * variant, 1 + 0.19 * variant])
        tick = time.perf_counter()
        result = extract(prepare_loaded_mesh(mesh, file_type="stl"))
        assert result.state == "ready", (path.name, result.failure_code)
        prototypes.append((mesh.export(file_type="stl"), result))
        measurements.append(
            {
                "source": path.name,
                "variant": variant,
                "seconds": time.perf_counter() - tick,
                "triangles": len(mesh.faces),
            }
        )
        print(
            json.dumps(
                {"stage": "design", "completed": index + 1, "total": args.designs}
            ),
            flush=True,
        )
    report["design_analysis"] = measurements
    report["design_analysis_total_seconds"] = time.perf_counter() - started
    save()
    source_pair = []
    tick = time.perf_counter()
    with Session(engine) as session:
        actor = build_user(session, superuser=True)
        configuration.update_settings(session, actor, {"enabled": True})
        for index in range(args.count):
            blob, result = prototypes[index % args.designs]
            content = (
                f"PrintStash benchmark {index}".encode().ljust(80, b" ") + blob[80:]
            )
            model = build_model(
                session, name=f"{sources[index % len(sources)].stem} {index}"
            )
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
            assert fingerprints.publish_precomputed(session, file, result) == "ready"
            if index in (0, args.designs):
                source_pair.append(file)
            if (index + 1) % 500 == 0:
                print(
                    json.dumps({"stage": "cached_population", "completed": index + 1}),
                    flush=True,
                )
        first, second = source_pair
        left, right = [
            session.exec(
                select(GeometryFingerprint).where(
                    GeometryFingerprint.file_id == file.id,
                    GeometryFingerprint.component_index == 0,
                )
            ).one()
            for file in source_pair
        ]
        with (
            artifact_content.resolve(first, backend=get_backend()).materialize() as a,
            artifact_content.resolve(second, backend=get_backend()).materialize() as b,
        ):
            proof = verify_paths(a, b, first_type="stl", second_type="stl")
        cached = candidates.publish(session, left, right, proof)
        assert cached is not None and cached.exact_equivalence
        # One additional real Artifact is unanalysed; the response must return
        # existing review evidence immediately while its analysis remains durable.
        content = b"Uncached cube artifact".ljust(80, b" ") + prototypes[0][0][80:]
        missing = build_file(
            session,
            first.model,
            file_type=FileType.STL,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        missing.path = get_backend().blob_key(
            missing.model.slug, missing.version, missing.original_filename
        )
        get_backend().write_stream(io.BytesIO(content), missing.path)
        session.add(missing)
        session.commit()
        report["cached_population_seed_seconds"] = time.perf_counter() - tick
        tick = time.perf_counter()
        response = service.query_model(session, actor, first.model_id)
        report["initial_cached_response_seconds"] = time.perf_counter() - tick
        report["initial_cached_candidates"] = len(response["items"])
        run_id = response["run"]["id"]
        original_candidates = session.exec(
            select(func.count()).select_from(SimilarityCandidate)
        ).one()
    save()
    worker = SimilarityProcessor(get_session_factory(), get_backend())
    tick = time.perf_counter()
    first_new = None
    for unit in range(1000):
        worker.work_one()
        with Session(engine) as session:
            run = session.get(SimilarityRun, run_id)
            count = session.exec(
                select(func.count()).select_from(SimilarityCandidate)
            ).one()
            if first_new is None and count > original_candidates:
                first_new = time.perf_counter() - tick
            if run.state in runs.TERMINAL:
                report["run"] = service.project_run(run)
                break
        if unit % 10 == 0:
            print(
                json.dumps(
                    {
                        "stage": "query_run",
                        "units": unit + 1,
                        "elapsed_seconds": time.perf_counter() - tick,
                    }
                ),
                flush=True,
            )
    report["first_new_verified_candidate_seconds"] = first_new
    report["full_scoped_run_seconds"] = time.perf_counter() - tick
    report["peak_process_rss_bytes"] = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        report["database_bytes"] = (args.root / "library.sqlite").stat().st_size
        report["fingerprint_rows"] = connection.exec_driver_sql(
            "SELECT count(*) FROM geometry_fingerprints"
        ).scalar_one()
        report["component_rows"] = connection.exec_driver_sql(
            "SELECT count(*) FROM geometry_fingerprints WHERE component_index > 0"
        ).scalar_one()
        report["storage_pages"] = [
            dict(row._mapping)
            for row in connection.exec_driver_sql(
                "SELECT name, sum(pgsize) AS bytes FROM dbstat WHERE name LIKE '%fingerprint%' OR name LIKE '%similarity%' GROUP BY name"
            )
        ]
        report["descriptor_blob_bytes"] = connection.exec_driver_sql(
            "SELECT sum(coalesce(length(d2_blob),0)+coalesce(length(sh_blob),0)+coalesce(length(view_blob),0)) FROM geometry_fingerprints"
        ).scalar_one()
    report["similarity_bytes_per_component"] = sum(
        row["bytes"] for row in report["storage_pages"]
    ) / max(report["component_rows"], 1)
    save()
    assert report["run"]["state"] == "completed", report["run"]
    assert report["initial_cached_candidates"] > 0
    assert report["initial_cached_response_seconds"] < 2
    assert first_new is not None
    print(
        json.dumps({"stage": "complete", "report": str(args.root / "result.json")}),
        flush=True,
    )


if __name__ == "__main__":
    main()
