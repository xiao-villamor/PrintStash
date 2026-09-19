"""Reproducible sqlite-vec feasibility measurements, separate from HTTP latency.

Run after the ordinary gates, on an otherwise idle host:
  uv run python -m tests.fakes.vector_scale --directory /tmp/ai-vector-scale

This measures exact KNN and the shipped bounded float scanner. It deliberately
does not claim model quality, HTTP latency, ingestion speed or physical ARM data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import sqlite3
import struct
import time
from pathlib import Path

from printstash_core.inference.vectors import VectorEntry, cosine_neighbors

from app.db.vector_extensions import load_sqlite_vector_extension


def digest(connection: sqlite3.Connection) -> str:
    result = hashlib.sha256()
    for id, blob in connection.execute("SELECT id, vector FROM vectors ORDER BY id"):
        result.update(struct.pack("<Q", id))
        result.update(blob)
    return result.hexdigest()


def fallback(connection, query, *, dimension, count, divisor):
    rows = connection.execute(
        "SELECT id, vector FROM vectors WHERE id % ? = 0 ORDER BY id", (divisor,)
    )
    result = cosine_neighbors(
        query,
        (VectorEntry(id, id, blob, "document") for id, blob in rows),
        dimension=dimension,
        limit=10,
        max_scan=count,
    )
    assert not result.truncated
    return [item.unit_id for item in result.items]


def measure(directory: Path, *, count=500_000, dimension=384, queries=32):
    import numpy as np

    if (
        not 100 <= count <= 1_000_000
        or not 8 <= dimension <= 4096
        or not 1 <= queries <= 100
    ):
        raise ValueError("scale_benchmark_budget_invalid")
    # Durable floats + native derivative + durable-only snapshot, with reserve.
    needed = count * (dimension * 12 + 384) + 1024**3
    if shutil.disk_usage(directory.parent).free < needed:
        raise ValueError("scale_benchmark_capacity_required")
    directory.mkdir(exist_ok=False)
    started, cpu_started = time.perf_counter(), time.process_time()
    source = sqlite3.connect(directory / "vectors.sqlite")
    restored = sqlite3.connect(directory / "restored.sqlite")
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("PRAGMA synchronous=NORMAL")
        source.execute("PRAGMA temp_store=MEMORY")
        source.execute(
            "CREATE TABLE vectors(id INTEGER PRIMARY KEY, vector BLOB NOT NULL)"
        )
        if not load_sqlite_vector_extension(source):
            raise RuntimeError("sqlite_vec_unavailable")
        source.execute(
            f"CREATE VIRTUAL TABLE neighbors USING vec0(embedding float[{dimension}] distance_metric=cosine)"
        )
        random = np.random.default_rng(166)
        build_started = time.perf_counter()
        for offset in range(0, count, 512):
            values = random.standard_normal(
                (min(512, count - offset), dimension)
            ).astype("<f4")
            values /= np.linalg.norm(values, axis=1)[:, None]
            rows = [
                (offset + index + 1, row.tobytes()) for index, row in enumerate(values)
            ]
            source.executemany("INSERT INTO vectors VALUES (?,?)", rows)
            source.executemany(
                "INSERT INTO neighbors(rowid,embedding) VALUES (?,?)", rows
            )
            source.commit()
        build_seconds = time.perf_counter() - build_started
        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert source.execute("SELECT count(*) FROM vectors").fetchone()[0] == count
        assert source.execute("SELECT count(*) FROM neighbors").fetchone()[0] == count
        original_digest = digest(source)
        probe = (
            np.random.default_rng(167)
            .standard_normal((queries, dimension))
            .astype("<f4")
        )
        probe /= np.linalg.norm(probe, axis=1)[:, None]
        observations = {}
        expected = {}
        for divisor in (1, 10):
            native_times, portable_times, recalls = [], [], []
            for index, query in enumerate(probe):
                before = time.perf_counter()
                baseline = fallback(
                    source,
                    query.tobytes(),
                    dimension=dimension,
                    count=count,
                    divisor=divisor,
                )
                portable_times.append(time.perf_counter() - before)
                before = time.perf_counter()
                native = [
                    row[0]
                    for row in source.execute(
                        "SELECT rowid FROM neighbors WHERE embedding MATCH ? AND k = 10 "
                        "AND rowid IN (SELECT id FROM vectors WHERE id % ? = 0) ORDER BY distance",
                        (query.tobytes(), divisor),
                    )
                ]
                native_times.append(time.perf_counter() - before)
                recalls.append(len(set(native) & set(baseline)) / len(baseline))
                expected[divisor, index] = baseline
            observations[str(divisor)] = {
                "eligible_vectors": count // divisor,
                "native_seconds": native_times,
                "fallback_seconds": portable_times,
                "native_p50_seconds": float(np.percentile(native_times, 50)),
                "native_p95_seconds": float(np.percentile(native_times, 95)),
                "fallback_p50_seconds": float(np.percentile(portable_times, 50)),
                "fallback_p95_seconds": float(np.percentile(portable_times, 95)),
                "mean_recall_at_10": float(np.mean(recalls)),
            }
        # A durable-only export, as opposed to copying an extension's shadows.
        # Never load sqlite-vec into the restored connection.
        backup_started = time.perf_counter()
        restored.execute(
            "CREATE TABLE vectors(id INTEGER PRIMARY KEY, vector BLOB NOT NULL)"
        )
        cursor = source.execute("SELECT id, vector FROM vectors ORDER BY id")
        while rows := cursor.fetchmany(512):
            restored.executemany("INSERT INTO vectors VALUES (?,?)", rows)
        restored.commit()
        backup_seconds = time.perf_counter() - backup_started
        assert restored.execute("SELECT count(*) FROM vectors").fetchone()[0] == count
        assert digest(restored) == original_digest
        assert not any(
            row[0] == "vec_version" for row in restored.execute("PRAGMA function_list")
        )
        for divisor in (1, 10):
            assert (
                fallback(
                    restored,
                    probe[0].tobytes(),
                    dimension=dimension,
                    count=count,
                    divisor=divisor,
                )
                == expected[divisor, 0]
            )
        result = {
            "count": count,
            "dimension": dimension,
            "queries": queries,
            "seed": 166,
            "architecture": platform.machine(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "cpu_affinity": sorted(os.sched_getaffinity(0)),
            "sqlite_version": sqlite3.sqlite_version,
            "sqlite_vec_version": source.execute("SELECT vec_version()").fetchone()[0],
            "numpy_version": np.__version__,
            "openblas_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
            "native_is_exact_knn": True,
            "observations": observations,
            "build_seconds": build_seconds,
            "backup_seconds": backup_seconds,
            "durable_float_payload_bytes": count * dimension * 4,
            "source_file_bytes": (directory / "vectors.sqlite").stat().st_size,
            "restored_file_bytes": (directory / "restored.sqlite").stat().st_size,
            "durable_sha256": original_digest,
            "restore_without_extension": True,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "cpu_seconds": time.process_time() - cpu_started,
            "wall_seconds": time.perf_counter() - started,
        }
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        source.close()
        restored.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--count", type=int, default=500_000)
    parser.add_argument("--dimension", type=int, default=384)
    parser.add_argument("--queries", type=int, default=32)
    options = parser.parse_args()
    # Set before the first NumPy import; report it rather than assuming a BLAS default.
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    result = measure(
        options.directory,
        count=options.count,
        dimension=options.dimension,
        queries=options.queries,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
