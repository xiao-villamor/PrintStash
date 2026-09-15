"""Export the reviewed OpenShape B/32 checkpoint for preplaced local inference.

Run in an isolated export environment (torch==2.14.0+cpu, onnx==1.22.0,
onnxruntime==1.30.0, dgl==1.1.3, einops, torch-redstone==0.0.6, pydantic).
The application itself never imports torch or DGL. Inputs must already exist
locally. See docs/ai-search-point-study.md for pinned sources and reproduction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

CHECKPOINT_SHA = "491a08f1922713df67099dfb7ffceaa6e039fd3953f99f80ecd7178d727c7f3f"
SUPPORT_REVISION = "70dbc29fa30520cb78b4982de671f90600c08685"
PAIRED_HASH = "7f56e23951620776f8a65d4de5441b6ff1eecd1f48c8ddf1eca8a82f1dea2089"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--paired", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Refuse both another revision and edits to executable model support files.
    revision = subprocess.check_output(
        ["git", "-C", str(args.support), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != SUPPORT_REVISION:
        raise ValueError("unreviewed_support_revision")
    subprocess.run(
        [
            "git",
            "-C",
            str(args.support),
            "diff",
            "--exit-code",
            "HEAD",
            "--",
            "openshape",
        ],
        check=True,
    )
    with args.checkpoint.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != CHECKPOINT_SHA:
            raise ValueError("unreviewed_checkpoint")
    if args.output.exists():
        raise ValueError("output_must_be_new")

    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    from printstash_core.inference.points import (
        POINT_RECIPE,
        canary_input,
        grouped_points,
    )

    from app.modules.inference.manifest import (
        LocalModelManifest,
        PointModelManifest,
        verify_assets,
    )

    paired = LocalModelManifest.model_validate_json(
        (args.paired / "manifest.json").read_bytes()
    )
    if paired.space().config_hash != PAIRED_HASH:
        raise ValueError("unreviewed_paired_tower")
    verify_assets(args.paired, paired)
    sys.path.insert(0, str(args.support))
    from openshape import B32, pointnet_util

    torch.set_num_threads(1)
    source = B32(
        torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    ).eval()
    pointnet_util.farthest_point_sample = lambda xyz, count: (
        pointnet_util.dgl.geometry.farthest_point_sampler(xyz, count, start_idx=0)
    )

    class Encoder(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.source = model

        def forward(self, centers, grouped):
            model = self.source
            for conv, bn in zip(model.sa.mlp_convs, model.sa.mlp_bns, strict=True):
                grouped = torch.relu(bn(conv(grouped)))
            encoded = grouped.max(dim=2).values
            tokens = model.lift(torch.cat([centers, encoded], dim=1))
            tokens = torch.cat(
                [model.cls_token[None, None, :].expand(1, 1, -1), tokens], dim=1
            )
            centers = torch.cat([centers.new_zeros((1, 3, 1)), centers], dim=2)
            return model.transformer(
                tokens, centers.unsqueeze(-1) - centers.unsqueeze(-2)
            )[:, 0]

    item = canary_input()
    inputs = grouped_points(item)
    encoder = Encoder(source).eval()
    with torch.no_grad():
        expected = source(
            torch.from_numpy(
                np.frombuffer(item.points, dtype="<f4").copy().reshape(1, 6, 10000)
            )
        ).numpy()
        split = encoder(*(torch.from_numpy(value) for value in inputs)).numpy()
    if not np.allclose(expected, split, atol=1e-5, rtol=0):
        raise ValueError("preprocessing_changed_checkpoint_output")
    args.output.mkdir(parents=True)
    graph = args.output / "pointbert-grouped.onnx"
    torch.onnx.export(
        encoder,
        tuple(torch.from_numpy(value) for value in inputs),
        str(graph),
        input_names=["centers", "grouped"],
        output_names=["point_embeds"],
        opset_version=17,
        dynamo=False,
    )
    model = onnx.load(graph)
    if {node.domain for node in model.graph.node} != {""}:
        raise ValueError("custom_operator_in_export")
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = options.enable_mem_pattern = False
    start = time.monotonic()
    runtime = ort.InferenceSession(
        str(graph), sess_options=options, providers=["CPUExecutionProvider"]
    )
    cold = time.monotonic() - start
    start = time.monotonic()
    actual = runtime.run(None, {"centers": inputs[0], "grouped": inputs[1]})[0]
    elapsed = time.monotonic() - start
    error = float(np.max(np.abs(expected - actual)))
    if not np.allclose(expected, actual, atol=1e-4, rtol=0):
        raise ValueError("onnx_canary_mismatch")
    manifest = PointModelManifest.model_validate(
        {
            "model_key": "openshape-pointbert-vitb32-rgb",
            "model_revision": "47e04daac585b2ce1cbbc72a42c0bf11971acddd",
            "repository": "OpenShape/openshape-pointbert-vitb32-rgb",
            "checkpoint_sha256": CHECKPOINT_SHA,
            "license": "MIT",
            "paired": paired.model_dump(),
            "paired_space_hash": PAIRED_HASH,
            "point": {
                "graph": {
                    "filename": graph.name,
                    "sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
                },
                "canary": (expected / np.linalg.norm(expected)).flatten().tolist(),
            },
        }
    )
    for asset in paired.assets():
        shutil.copyfile(args.paired / asset.filename, args.output / asset.filename)
    (args.output / "manifest.json").write_text(manifest.model_dump_json())
    evidence = {
        "checkpoint_sha256": CHECKPOINT_SHA,
        "support_revision": revision,
        "paired_space_hash": PAIRED_HASH,
        "space_hash": manifest.space().config_hash,
        "recipe": POINT_RECIPE,
        "torch": torch.__version__,
        "onnx": onnx.__version__,
        "onnxruntime": ort.__version__,
        "max_canary_error": error,
        "cold_seconds": cold,
        "onnx_seconds": elapsed,
        "input_sha256": hashlib.sha256(item.points).hexdigest(),
        "graph_sha256": manifest.point.graph.sha256,
    }
    (args.output / "export-evidence.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
