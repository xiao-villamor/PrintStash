"""Explicit offline photo/mesh measurement; no acquisition or timing claims."""

import argparse
import base64
import hashlib
import json
from pathlib import Path

import numpy as np
import trimesh
from printstash_core.inference.context import InferenceContext
from printstash_core.inference.images import decode_image
from printstash_core.mesh.rasterizer import render_mesh_thumbnail
from printstash_core.search.visual_inputs import mean_pool
from sqlmodel import SQLModel, create_engine

from app.db.session import SQLiteSessionFactory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.worker_pool import pool
from app.modules.media import mesh_render, thumbnail
from tests.fakes.visual_candidate_benchmark import FRAMES
from tests.paths import FIXTURES_DIR, TESTDATA_DIR


def measure(directory: Path, output: Path, profile: str):
    root = FIXTURES_DIR / "search"
    original = json.loads((root / "clip-b32-visual-vectors.json").read_text())
    distractors = json.loads(
        (root / "clip-b32-existing-thumbnail-vectors.json").read_text()
    )
    photo = (root / "printed-benchy.jpg").read_bytes()
    mesh_path = TESTDATA_DIR / "benchy/3dbenchy.stl"
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    provider = LocalEmbeddingProvider(
        SQLiteSessionFactory(engine), directory, original["space"]["model_key"], 1
    )
    try:
        provider.validate(context=InferenceContext.bounded(120))
        encoded = thumbnail.to_webp(
            mesh_render.render_mesh_thumbnail(
                trimesh.load_mesh(mesh_path, process=False),
                "",
                width=640,
                height=480,
                output_format="WEBP",
            )
        )
        query, candidate = provider.embed(
            (decode_image(photo, "image/jpeg"), decode_image(encoded, "image/webp")),
            provider.space,
            context=InferenceContext.bounded(120),
        )
        candidates = [
            np.frombuffer(
                base64.b64decode(row["vector_float32_le_base64"]), dtype="<f4"
            )
            for row in distractors["items"]
        ]
        scores = np.asarray([*candidates, candidate]) @ np.asarray(query)
        order = np.argsort(-scores, kind="stable").tolist()
        result = {
            "space": provider.space.__dict__,
            "space_hash": provider.space.config_hash,
            "photo_sha256": hashlib.sha256(photo).hexdigest(),
            "mesh_sha256": hashlib.sha256(mesh_path.read_bytes()).hexdigest(),
            "thumbnail_sha256": hashlib.sha256(encoded).hexdigest(),
            "distractors_sha256": hashlib.sha256(
                (root / "clip-b32-existing-thumbnail-vectors.json").read_bytes()
            ).hexdigest(),
            "multiview_distractors_sha256": hashlib.sha256(
                (root / "clip-b32-visual-vectors.json").read_bytes()
            ).hexdigest(),
            "query_float32_le_base64": base64.b64encode(
                np.asarray(query, dtype="<f4").tobytes()
            ).decode(),
            "model_float32_le_base64": base64.b64encode(
                np.asarray(candidate, dtype="<f4").tobytes()
            ).decode(),
            "rank": order.index(len(candidates)) + 1,
            "candidate_count": len(candidates) + 1,
            "score": float(scores[-1]),
            "scores": scores.tolist(),
        }
        mesh = trimesh.load_mesh(mesh_path, process=False)
        views = tuple(
            decode_image(
                render_mesh_thumbnail(
                    mesh,
                    "",
                    width=224,
                    height=224,
                    matte=True,
                    view_rotation=np.asarray(frame, np.float64),
                ),
                "image/png",
            )
            for frame in FRAMES
        )
        model_views = provider.embed(
            views, provider.space, context=InferenceContext.bounded(120)
        )
        result["model_views_float32_le_base64"] = base64.b64encode(
            np.asarray(model_views, dtype="<f4").tobytes()
        ).decode()
        result["model_view_rgb_sha256"] = [
            hashlib.sha256(view.rgb).hexdigest() for view in views
        ]
        multi_candidates = [
            np.frombuffer(
                base64.b64decode(
                    row["views"]["multiview_matte"]["vectors_float32_le_base64"]
                ),
                dtype="<f4",
            ).reshape(-1, 512)
            for row in original["items"]
        ]
        multi_candidates.append(np.asarray(model_views))
        result["multiview"] = {}
        for aggregation in ("mean", "max"):
            multi_scores = [
                float(
                    np.asarray(mean_pool(tuple(tuple(v) for v in vectors), 512)) @ query
                )
                if aggregation == "mean"
                else float(np.max(vectors @ query))
                for vectors in multi_candidates
            ]
            multi_order = np.argsort(-np.asarray(multi_scores), kind="stable").tolist()
            result["multiview"][aggregation] = {
                "rank": multi_order.index(len(candidates)) + 1,
                "score": multi_scores[-1],
                "scores": multi_scores,
            }
        result["acceptance_profile"] = profile
        result["acceptance_rank"] = (
            result["rank"]
            if profile == "thumbnail"
            else result["multiview"][profile.removeprefix("multiview_")]["rank"]
        )
        output.write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "rank": result["rank"],
                    "candidates": result["candidate_count"],
                    "score": result["score"],
                    "multiview": result["multiview"],
                }
            )
        )
        if result["acceptance_rank"] > 10:
            raise SystemExit("printed_photo_recall_failed")
    finally:
        pool.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile",
        choices=("thumbnail", "multiview_mean", "multiview_max"),
        required=True,
    )
    args = parser.parse_args()
    measure(args.model_directory, args.output, args.profile)
