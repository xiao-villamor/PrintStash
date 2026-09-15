"""Compare the existing persisted-media recipe using the frozen visual corpus.

Explicit local weights only. No network, no names/tags/descriptions reach CLIP.
"""

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from printstash_core.inference.context import InferenceContext
from printstash_core.inference.images import decode_image
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE
from sqlmodel import SQLModel, create_engine

from app.core.config import _overlay
from app.db.session import SQLiteSessionFactory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.worker_pool import pool
from app.modules.media import mesh_render, thumbnail
from tests.fakes.visual_corpus import geometry
from tests.paths import FIXTURES_DIR


def measure(directory: Path, output: Path):
    _overlay["model_thumbnail_width"] = 640
    original = json.loads(
        (FIXTURES_DIR / "search/clip-b32-visual-vectors.json").read_text()
    )
    corpus = json.loads((FIXTURES_DIR / "search/visual-queries.json").read_text())[
        "items"
    ]
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    provider = LocalEmbeddingProvider(
        SQLiteSessionFactory(engine), directory, original["space"]["model_key"], 1
    )
    provider.validate(context=InferenceContext.bounded(120))
    records = []
    for entry in corpus:
        mesh = geometry(entry["geometry"])
        start = time.monotonic()
        encoded = thumbnail.to_webp(
            mesh_render.render_mesh_thumbnail(
                mesh, "", width=640, height=480, output_format="WEBP"
            )
        )
        image = decode_image(encoded, "image/webp")
        render_seconds = time.monotonic() - start
        start = time.monotonic()
        vector = provider.embed(
            (image,), provider.space, context=InferenceContext.bounded(120)
        )[0]
        records.append(
            {
                "id": entry["id"],
                "mesh_sha256": hashlib.sha256(mesh.export(file_type="stl")).hexdigest(),
                "rgb_sha256": hashlib.sha256(image.rgb).hexdigest(),
                "encoded_sha256": hashlib.sha256(encoded).hexdigest(),
                "vector_float32_le_base64": base64.b64encode(
                    np.asarray(vector, dtype="<f4").tobytes()
                ).decode(),
                "render_seconds": render_seconds,
                "embedding_seconds": time.monotonic() - start,
            }
        )
        print(json.dumps({"finished": len(records)}), flush=True)
    vectors = np.array(
        [
            np.frombuffer(
                base64.b64decode(row["vector_float32_le_base64"]), dtype="<f4"
            )
            for row in records
        ]
    )
    queries = np.array(
        [
            np.frombuffer(base64.b64decode(row["query_float32_le_base64"]), dtype="<f4")
            for row in original["items"]
        ]
    )
    scores = queries @ vectors.T
    rankings = np.argsort(-scores, axis=1, kind="stable")
    metrics = {
        f"recall_at_{k}": sum(i in rankings[i, :k] for i in range(32)) / 32
        for k in (1, 5, 10)
    }
    output.write_text(
        json.dumps(
            {
                "space_hash": provider.space.config_hash,
                "width": 640,
                "height": 480,
                "preview_recipe": PREVIEW_PROFILE.recipe_fingerprint,
                "query_manifest_sha256": original["query_manifest_sha256"],
                "generator_sha256": original["generator_sha256"],
                "items": records,
                "metrics": metrics,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    measure(args.directory, args.output)
