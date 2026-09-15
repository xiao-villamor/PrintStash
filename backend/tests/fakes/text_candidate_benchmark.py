"""Compare explicitly preplaced text exports using the production CPU worker.

No acquisition and no external inference. Labels and corpus must be frozen before
running. These engineering measurements do not substitute for user-labelled or
target-hardware acceptance tests.
"""

import argparse
import hashlib
import json
import platform
import tempfile
import time
from pathlib import Path

import numpy as np
from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.inference.context import InferenceContext
from printstash_core.search.passages import PassageContent, render_passages
from sqlmodel import SQLModel, create_engine

from app.db.session import SQLiteSessionFactory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.manifest import TextModelManifest, read_manifest
from app.modules.inference.worker_pool import pool
from tests.paths import FIXTURES_DIR


def measure(directory: Path, sessions: SQLiteSessionFactory, threads: int) -> dict:
    import csv

    root = FIXTURES_DIR / "search"
    corpus = json.loads((root / "corpus.json").read_text())
    with (root / "queries.csv").open() as stream:
        queries = [row for row in csv.DictReader(stream) if row["expected_id"]]
    key = json.loads((directory / "manifest.json").read_text())["model_key"]
    manifest = read_manifest(directory, key)
    if not isinstance(manifest, TextModelManifest):
        raise ValueError("text_manifest_required")
    result = {
        "repository": manifest.repository,
        "revision": manifest.model_revision,
        "space": manifest.space().__dict__,
        "manifest_sha256": hashlib.sha256(
            manifest.model_dump_json().encode()
        ).hexdigest(),
        "assets": [
            asset.model_dump() | {"bytes": (directory / asset.filename).stat().st_size}
            for asset in manifest.assets()
        ],
        "license": manifest.license,
        "languages": manifest.language,
        "threads": threads,
        "corpus_sha256": hashlib.sha256(
            (root / "corpus.json").read_bytes()
        ).hexdigest(),
        "queries_sha256": hashlib.sha256(
            (root / "queries.csv").read_bytes()
        ).hexdigest(),
    }
    pool.close()
    try:
        provider = LocalEmbeddingProvider(sessions, directory, key, threads)
        started = time.monotonic()
        provider.validate(context=InferenceContext.bounded(120))
        result["cold_canary_seconds"] = time.monotonic() - started

        def embed(text: str):
            return provider.embed(
                (EmbeddingInput("text", text=text),),
                provider.space,
                context=InferenceContext.bounded(120),
            )[0]

        started = time.monotonic()
        documents = np.asarray(
            [
                embed(
                    manifest.document_prefix
                    + render_passages(
                        PassageContent(
                            title=row["name"], description=row["description"]
                        )
                    )[0].text
                )
                for row in corpus
            ],
            dtype=np.float32,
        )
        result["documents_seconds"] = time.monotonic() - started
        timings = []
        misses = []
        for query in queries:
            started = time.monotonic()
            vector = np.asarray(
                embed(manifest.query_prefix + query["query"]), np.float32
            )
            timings.append(time.monotonic() - started)
            top = np.argsort(-(documents @ vector), kind="stable")[:5]
            if int(query["expected_id"]) not in {corpus[index]["id"] for index in top}:
                misses.append(query["query_id"])
        result.update(
            state="measured",
            queries=len(queries),
            native_recall_at_5=1 - len(misses) / len(queries),
            missed_queries=misses,
            warm_query_p50_ms=float(np.percentile(timings, 50) * 1000),
            warm_query_p95_ms=float(np.percentile(timings, 95) * 1000),
        )
    except EmbeddingError as exc:
        result.update(state="runtime_rejected", code=str(exc))
    finally:
        pool.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=1)
    arguments = parser.parse_args()
    measurements = []
    with tempfile.TemporaryDirectory(prefix="printstash-candidates-") as temporary:
        engine = create_engine(f"sqlite:///{temporary}/compute.sqlite")
        SQLModel.metadata.create_all(engine)
        sessions = SQLiteSessionFactory(engine)
        for directory in arguments.model_dir:
            result = measure(directory, sessions, arguments.threads)
            measurements.append(result)
            arguments.output.write_text(
                json.dumps(
                    {"architecture": platform.machine(), "measurements": measurements},
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            print(json.dumps(result), flush=True)
        engine.dispose()
