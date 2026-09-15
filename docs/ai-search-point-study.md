# AI Search point-cloud export and quality study

Measured on 2026-09-12, x86_64 QEMU CPU, one ONNX inference thread. OpenShape
B/32 is an optional, preplaced profile. It uses the shared local inference worker,
model cache, vector store, compute permits and generation lifecycle. A ready
thumbnail or multiview index is required before preparing point-cloud search.
No original mesh or point data is sent to a remote provider.

## Export decision (S1)

The official [OpenShape inference support](https://huggingface.co/OpenShape/openshape-demo-support/tree/70dbc29fa30520cb78b4982de671f90600c08685)
provides the B/32 PointBERT architecture and identifies its aligned CLIP tower.
The [checkpoint](https://huggingface.co/OpenShape/openshape-pointbert-vitb32-rgb/tree/47e04daac585b2ce1cbbc72a42c0bf11971acddd)
and inference support model cards declare MIT. The separate
[training repository](https://github.com/Colin97/OpenShape_code/tree/abe5aa42b7c99c037c286ad54313f695a114bf0d)
is Apache-2.0. This distinction is retained; weights are not committed here.

The checkpoint is 101,260,675 bytes, SHA-256
`491a08f1922713df67099dfb7ffceaa6e039fd3953f99f80ecd7178d727c7f3f`.
It aligns with the existing pinned B/32 encoder Space
`7f56e23951620776f8a65d4de5441b6ff1eecd1f48c8ddf1eca8a82f1dea2089`.
Equal dimensions alone never establish alignment.

Direct ONNX export failed in DGL's native farthest-point sampling operation.
Separating deterministic preprocessing from the learned graph succeeded. The
preprocessing uses 10,000 area-weighted surface samples, fixed seed 166,
mean-centering and unit-ball scale, Z-up to Y-up conversion, neutral RGB 0.4,
64 FPS centers starting at point zero, then radius-0.4 groups of the first 256
points in source order. Short groups repeat their first member. The checkpoint
uses radius grouping, **not nearest-k grouping**.

The learned graph keeps the source weights unchanged. It has standard ONNX
operators only, opset 17, inputs `[1,3,64]` and `[1,9,256,64]`, and a native
512-dimensional output. SHA-256:
`d137214089a4f114deb7dd222d5ef15afe054ea6ef0a0e68b1635d4ccb2a3992`.
The split Torch forward matched the original exactly. Three independent spike
canaries differed from Torch by at most 2.37e-6; the production export canary
error was 1.93e-6, below the unchanged 1e-4 tolerance.

The production manifest is schema v3 and records the checkpoint, exact paired
manifest, graph digest, canary and sampling recipe. Its Space is
`4f845433d48ed5f38609b1538118ea8761405d528f750eb07784fbb795ffe448`.
Point units have `point_cloud` kind and `file:{id}:point` identity with a current
Artifact hash. A changed export or recipe requires a new generation.

## Measured quality and cost

The same frozen 32-query engineering corpus used by the visual study reaches
the point encoder with opaque object IDs, no descriptions and no tags.
Queries were frozen before inference but are assistant-authored. They are not
independent human acceptance. No real printed-part photograph was supplied.

| Production recipe | Recall @1 | Recall @5 | Recall @10 |
|---|---:|---:|---:|
| Existing CLIP B/32 thumbnail | 0.625 | 0.84375 | 0.84375 |
| Six CLIP views, maximum score | 0.40625 | 0.71875 | 0.78125 |
| OpenShape B/32 point cloud | 0.75 | 0.875 | 0.9375 |

The earlier spike used an object-specific sampling seed and reached 0.78125 /
0.90625 / 0.9375. The table uses the final production recipe, fixed seed 166.
Native vectors and source/input hashes are retained in
`backend/tests/fixtures/search/openshape-b32-point-vectors.json`. The regression
replays them through the actual authorized store with the paired real CLIP
query vectors. The source Model is found in the top 10 for 30 of 32 queries.

On this corpus the CLIP image threshold 0.20 removed four valid point matches.
The lowest correct top-10 point score was 0.1469. This exact OpenShape Space
therefore defaults to 0.10, with explicit per-Space administrator overrides
winning. This is an engineering calibration; out-of-domain false-positive
rates and human-query acceptance remain unverified.

Median surface preparation took 0.0055 seconds. The full contained point-worker
call, including grouping and pipe transfer, took 0.250 seconds. Cold loading and
validation of all three towers took 10.2–10.5 seconds. A separate ten-call run
reported a native child peak RSS of 1,653,492 KiB under the existing 2 GiB budget.
The export-only ONNX session took 0.32 seconds to open and 0.20 seconds per
cloud; those figures exclude the paired towers and are not application latency.

A real-browser test built a thumbnail fallback and the preplaced production
OpenShape profile, uploaded a cube, and found it with “a cube” and a visible
Shape match. It passed in 37 seconds (59.7 seconds including application setup).
Physical ARM canaries, Pi backfill time and large-library latency are still
unmeasured. The requested physical ARM host has not been supplied.

## Reproduction and installation

The application needs its existing optional ONNX dependencies. Torch and DGL
are export-tool dependencies only. Prepare a separate environment containing
`torch==2.14.0+cpu`, `onnx==1.22.0`, `onnxruntime==1.30.0`, `dgl==1.1.3`,
`torch-redstone==0.0.6`, `einops`, NumPy and `pydantic==2.12.5`. Obtain the pinned
checkpoint, support checkout and already verified CLIP B/32 assets above.

From `backend/`, with that environment's Python:

```sh
PYTHONPATH=.:packages/printstash-core/src python scripts/export_openshape.py \
  --checkpoint /path/to/pointbert-b32.pt \
  --support /path/to/openshape-demo-support \
  --paired /path/to/verified-clip-b32 \
  --output /path/to/new-point-export
```

The destination must be new. The tool verifies checkpoint SHA, support revision,
paired Space, original-vs-split output, operator domains and ONNX canary before
writing the manifest. It uses `torch.load(weights_only=True)` and performs no
download. Keep upstream license notices with redistributed model artifacts.
No public download URL is invented for this locally produced export.

Set `VAULT_EMBEDDING_LOCAL_MODEL_DIR` to the resulting directory, enable local
models in AI Search settings, and build a visual fallback. Select **Geometry
search · point cloud** and the installed OpenShape model, verify it, then build
the index. Mesh sampling happens in the existing contained media worker; source
Artifacts are never rewritten. Cancellation, current-source checks, hash-scoped
quarantine and atomic cutover use the same lifecycle as other search profiles.

To repeat the production corpus measurement with the application's environment:

```sh
uv run python -m tests.fakes.point_candidate_benchmark \
  /path/to/new-point-export /path/to/point-quality.json
```

The PostgreSQL test builds and queries both thumbnail and point generations.
It caught an untyped null in the visual source subquery; explicitly casting the
absent passage identity to integer fixes publication on PostgreSQL. The rerun
passed. Other focused checks cover private-collection exclusion, late hash / trash /
cancel fences, poison-geometry quarantine, and STL/3MF/STEP sampling. Repository
hygiene checks also caught test grouping and naming issues; these were corrected
without changing their assertions.
