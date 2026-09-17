"""Explicit local measurement of the frozen clarity corpus using pinned exports.

Run with --bge-dir and --clip-dir. No downloads, private library inputs or labels
are supplied to the image encoder. Output includes input hashes and CPU timings.
"""

import argparse
import base64
import hashlib
import io
import json
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image
from printstash_core.inference import EmbeddingInput
from printstash_core.inference.context import InferenceContext
from printstash_core.mesh.rasterizer import render_mesh_thumbnail
from printstash_core.search.passages import PassageContent, render_passages
from sqlmodel import SQLModel, create_engine

from app.db.session import SQLiteSessionFactory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.worker_pool import pool
from tests.fakes.search_clarity_geometry import geometry
from tests.fakes.visual_candidate_benchmark import FRAMES
from tests.paths import FIXTURES_DIR


def measure(bge_dir, clip_dir, output):
    source = FIXTURES_DIR / "search/clarity-corpus.json"
    corpus = json.loads(source.read_text())
    result = {
        "corpus_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "generator_sha256": hashlib.sha256(
            Path(__file__).with_name("search_clarity_geometry.py").read_bytes()
        ).hexdigest(),
    }
    with tempfile.TemporaryDirectory(prefix="search-measure-") as temporary:
        engine = create_engine(f"sqlite:///{temporary}/compute.sqlite")
        SQLModel.metadata.create_all(engine)
        sessions = SQLiteSessionFactory(engine)
        for key, directory in (("text", bge_dir), ("clip", clip_dir)):
            manifest = json.loads((directory / "manifest.json").read_text())
            provider = LocalEmbeddingProvider(
                sessions, directory, manifest["model_key"], 1
            )
            provider.validate(context=InferenceContext.bounded(120))
            measured = {
                "space": provider.space.__dict__,
                "vectors": {},
                "timings_ms": [],
            }
            result[key] = measured

            def embed(value, provider=provider, measured=measured):
                started = time.monotonic()
                vector = provider.embed(
                    (value,), provider.space, context=InferenceContext.bounded(120)
                )[0]
                measured["timings_ms"].append((time.monotonic() - started) * 1000)
                return base64.b64encode(
                    np.asarray(vector, dtype="<f4").tobytes()
                ).decode()

            queries = [
                r["query"] for r in corpus["calibration"] + corpus["validation_queries"]
            ] + corpus["calibration_absent"]
            for query in queries:
                text = provider.space.query_prefix + query
                measured["vectors"][hashlib.sha256(text.encode()).hexdigest()] = embed(
                    EmbeddingInput("text", text=text)
                )
            for row in corpus["calibration"] + corpus["validation"]:
                if key == "text":
                    text = render_passages(
                        PassageContent(
                            title=row["name"], description=row["description"]
                        )
                    )[0].text
                    measured["vectors"][hashlib.sha256(text.encode()).hexdigest()] = (
                        embed(
                            EmbeddingInput(
                                "text", text=provider.space.document_prefix + text
                            )
                        )
                    )
                else:
                    mesh = geometry(row["geometry"])
                    views = []
                    for frame in (None, *FRAMES):
                        png = render_mesh_thumbnail(
                            mesh,
                            "",
                            width=224,
                            height=224,
                            matte=True,
                            view_rotation=np.asarray(frame, np.float64)
                            if frame
                            else None,
                        )
                        with Image.open(io.BytesIO(png)) as image:
                            rgba = image.convert("RGBA")
                            bg = Image.new("RGBA", rgba.size, "white")
                            bg.alpha_composite(rgba)
                            raw = bg.convert("RGB").tobytes()
                            if frame is None:
                                bg.save(Path(temporary) / f"{row['id']}.png")
                        views.append(
                            {
                                "rgb_sha256": hashlib.sha256(raw).hexdigest(),
                                "vector": embed(
                                    EmbeddingInput(
                                        "image", rgb=raw, width=224, height=224
                                    )
                                ),
                            }
                        )
                    measured.setdefault("images", {})[row["id"]] = views
            pool.close()
        engine.dispose()
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bge-dir", type=Path, required=True)
    parser.add_argument("--clip-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    measure(args.bge_dir, args.clip_dir, args.output)
