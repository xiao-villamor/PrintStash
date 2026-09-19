"""Run the frozen render comparison against preplaced real CLIP weights only.

Outputs contain native vectors and input hashes, allowing ordinary regression
tests to replay real measurements without downloading weights. No descriptions,
names or tags are supplied to the image tower.
"""

import argparse
import base64
import hashlib
import io
import json
import platform
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image
from printstash_core.inference import EmbeddingInput
from printstash_core.inference.context import InferenceContext
from printstash_core.mesh.rasterizer import render_mesh_thumbnail
from sqlmodel import SQLModel, create_engine

from app.db.session import SQLiteSessionFactory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.manifest import LocalModelManifest, read_manifest
from app.modules.inference.worker_pool import pool
from tests.fakes.visual_corpus import geometry
from tests.paths import FIXTURES_DIR

FRAMES = (
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((-1, 0, 0), (0, 1, 0), (0, 0, -1)),
    ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
    ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
    ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
)


def normalize(values):
    array = np.asarray(values, dtype=np.float32)
    return array / np.linalg.norm(array, axis=-1, keepdims=True)


def measure(directory: Path, sessions: SQLiteSessionFactory, output: Path):
    source = FIXTURES_DIR / "search/visual-queries.json"
    corpus = json.loads(source.read_text())["items"]
    key = json.loads((directory / "manifest.json").read_text())["model_key"]
    manifest = read_manifest(directory, key)
    if not isinstance(manifest, LocalModelManifest) or manifest.family != "clip":
        raise ValueError("paired_clip_manifest_required")
    provider = LocalEmbeddingProvider(sessions, directory, key, 1)
    started = time.monotonic()
    provider.validate(context=InferenceContext.bounded(120))
    result = {
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "threads": 1,
        "space": provider.space.__dict__,
        "space_hash": provider.space.config_hash,
        "query_manifest_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "generator_sha256": hashlib.sha256(
            Path(__file__).with_name("visual_corpus.py").read_bytes()
        ).hexdigest(),
        "cold_canary_seconds": time.monotonic() - started,
        "items": [],
    }
    size = manifest.image.image_size
    records = []
    for entry in corpus:
        mesh = geometry(entry["geometry"])
        item = {
            "id": entry["id"],
            "mesh_sha256": hashlib.sha256(mesh.export(file_type="stl")).hexdigest(),
            "views": {},
        }
        for style, frames, matte in (
            ("thumbnail_catalog", (None,), False),
            ("thumbnail_matte", (None,), True),
            ("multiview_matte", FRAMES, True),
        ):
            started = time.monotonic()
            inputs, digests = [], []
            for index, frame in enumerate(frames):
                png = render_mesh_thumbnail(
                    mesh,
                    "",
                    width=size,
                    height=size,
                    matte=matte,
                    view_rotation=np.asarray(frame, np.float64) if frame else None,
                )
                if png is None:
                    raise ValueError("fixture_render_failed")
                with Image.open(io.BytesIO(png)) as image:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, "white")
                    background.alpha_composite(rgba)
                    rgb = background.convert("RGB")
                    if entry["id"] in (13, 16, 31, 32):
                        rgb.save(output.parent / f"{entry['id']}-{style}-{index}.png")
                    raw = rgb.tobytes()
                digests.append(hashlib.sha256(raw).hexdigest())
                inputs.append(EmbeddingInput("image", rgb=raw, width=size, height=size))
            render_seconds = time.monotonic() - started
            started = time.monotonic()
            vectors = provider.embed(
                tuple(inputs), provider.space, context=InferenceContext.bounded(120)
            )
            item["views"][style] = {
                "rgb_sha256": digests,
                "vectors_float32_le_base64": base64.b64encode(
                    np.asarray(vectors, dtype="<f4").tobytes()
                ).decode(),
                "render_seconds": render_seconds,
                "embedding_seconds": time.monotonic() - started,
            }
        started = time.monotonic()
        text = provider.embed(
            (EmbeddingInput("text", text=entry["query"]),),
            provider.space,
            context=InferenceContext.bounded(120),
        )[0]
        item["query_seconds"] = time.monotonic() - started
        item["query_float32_le_base64"] = base64.b64encode(
            np.asarray(text, dtype="<f4").tobytes()
        ).decode()
        records.append(item)
        print(json.dumps({"finished": len(records), "id": entry["id"]}), flush=True)
    text = np.asarray(
        [
            np.frombuffer(
                base64.b64decode(item["query_float32_le_base64"]), dtype="<f4"
            )
            for item in records
        ]
    )
    scores = {}
    for style in ("thumbnail_catalog", "thumbnail_matte", "multiview_matte"):
        views = np.asarray(
            [
                np.frombuffer(
                    base64.b64decode(item["views"][style]["vectors_float32_le_base64"]),
                    dtype="<f4",
                ).reshape(-1, manifest.native_dimension)
                for item in records
            ]
        )
        if style.startswith("thumbnail"):
            scores[style] = text @ views[:, 0].T
        else:
            scores[style + "_mean"] = text @ normalize(normalize(views).mean(axis=1)).T
            scores[style + "_max"] = np.einsum("qd,svd->qsv", text, views).max(axis=2)
    metrics = {}
    for style, score in scores.items():
        order = np.argsort(-score, axis=1, kind="stable")
        metrics[style] = {
            f"recall_at_{k}": float(
                np.mean([i in indices[:k] for i, indices in enumerate(order)])
            )
            for k in (1, 5, 10)
        }
        metrics[style]["missed_at_10"] = [
            corpus[i]["id"] for i, indices in enumerate(order) if i not in indices[:10]
        ]
    result["metrics"] = metrics
    result["items"] = records
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metrics), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_directory", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visual-study-") as root:
        engine = create_engine(f"sqlite:///{root}/compute.sqlite")
        SQLModel.metadata.create_all(engine)
        try:
            measure(
                arguments.model_directory,
                SQLiteSessionFactory(engine),
                arguments.output,
            )
        finally:
            pool.close()
            engine.dispose()
