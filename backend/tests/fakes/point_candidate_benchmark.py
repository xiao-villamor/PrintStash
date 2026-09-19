"""Measure the production point recipe with pinned preplaced OpenShape/CLIP.

This engineering corpus does not substitute for independent human acceptance.
"""

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from printstash_core.inference.context import InferenceContext
from printstash_core.inference.points import POINT_RECIPE, point_input
from sqlmodel import SQLModel, create_engine

from app.core.config import _overlay
from app.db.session import SQLiteSessionFactory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.worker_pool import pool
from tests.fakes.visual_corpus import geometry
from tests.paths import FIXTURES_DIR


def measure(directory: Path, output: Path):
    original = json.loads(
        (FIXTURES_DIR / "search/clip-b32-visual-vectors.json").read_text()
    )
    corpus = json.loads((FIXTURES_DIR / "search/visual-queries.json").read_text())[
        "items"
    ]
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    _overlay["embedding_cache_dir"] = output.parent / "worker-cache"
    provider = LocalEmbeddingProvider(
        SQLiteSessionFactory(engine), directory, "openshape-pointbert-vitb32-rgb", 1
    )
    start = time.monotonic()
    provider.validate(context=InferenceContext.bounded(120))
    cold = time.monotonic() - start
    records = []
    try:
        for entry in corpus:
            mesh = geometry(entry["geometry"])
            start = time.monotonic()
            points = point_input(mesh.vertices, mesh.faces)
            preparation = time.monotonic() - start
            start = time.monotonic()
            vector = provider.embed(
                (points,), provider.space, context=InferenceContext.bounded(120)
            )[0]
            records.append(
                {
                    "id": entry["id"],
                    "mesh_sha256": hashlib.sha256(
                        mesh.export(file_type="stl")
                    ).hexdigest(),
                    "input_sha256": hashlib.sha256(points.points).hexdigest(),
                    "vector_float32_le_base64": base64.b64encode(
                        np.asarray(vector, dtype="<f4").tobytes()
                    ).decode(),
                    "sample_seconds": preparation,
                    "embedding_seconds": time.monotonic() - start,
                }
            )
            print(json.dumps({"finished": len(records)}), flush=True)
        vectors = np.asarray(
            [
                np.frombuffer(
                    base64.b64decode(row["vector_float32_le_base64"]), dtype="<f4"
                )
                for row in records
            ]
        )
        queries = np.asarray(
            [
                np.frombuffer(
                    base64.b64decode(row["query_float32_le_base64"]), dtype="<f4"
                )
                for row in original["items"]
            ]
        )
        scores = queries @ vectors.T
        rankings = np.argsort(-scores, axis=1, kind="stable")
        metrics = {
            f"recall_at_{k}": sum(i in rankings[i, :k] for i in range(len(records)))
            / len(records)
            for k in (1, 5, 10)
        }
        result = {
            "space": provider.space.__dict__,
            "space_hash": provider.space.config_hash,
            "recipe": POINT_RECIPE,
            "query_manifest_sha256": original["query_manifest_sha256"],
            "generator_sha256": original["generator_sha256"],
            "items": records,
            "metrics": metrics,
            "cold_seconds": cold,
        }
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"metrics": metrics, "cold_seconds": cold}), flush=True)
    finally:
        pool.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    measure(args.directory, args.output)
