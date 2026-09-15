# AI Search visual model and render study

Measured locally on 2026-09-12, x86_64 QEMU CPU, one ONNX inference thread.
These are reproducible engineering measurements. The 32 queries were written
before inference, but are assistant-authored, not independent human acceptance.
Physical Raspberry Pi measurements and independent human-query acceptance remain open. A separate public printed-part photograph is measured below.

The frozen corpus has 26 original CC0 analytic meshes and six attributed CC0
Thingi10K meshes already present in the core fixtures. Shapes reach the image
tower without names, tags or descriptions. The query manifest and generator
are SHA-256 bound into each result; native float32 vectors, RGB/source hashes
and timings are retained in `backend/tests/fixtures/search/`.

| Candidate / recipe | Recall @1 | Recall @5 | Recall @10 |
|---|---:|---:|---:|
| CLIP B/32, existing media thumbnail (640×480 lossless WebP) | 0.625 | 0.84375 | 0.84375 |
| CLIP B/32, catalog shading at native 224×224 | 0.625 | 0.8125 | 0.875 |
| CLIP B/32, matte shading at native 224×224 | 0.625 | 0.8125 | 0.875 |
| CLIP B/32, six matte views, normalized mean | 0.4375 | 0.625 | 0.75 |
| CLIP B/32, six matte views, maximum score | 0.40625 | 0.71875 | 0.78125 |

The selected thumbnail recipe uses the existing media pipeline. Reuse requires
a ready, complete, full-mesh thumbnail with the exact source hash, preview
recipe, dimensions and encoded digest. Embedded slicer images and user covers
are not substituted for a geometry render. Missing or corrupt derivatives fall
back to the same renderer. Multiview generates its six views and fallback
thumbnail from one mesh load. Neither aggregation improved this engineering
corpus over the thumbnail; multiview is an explicit profile choice, not a
promised quality upgrade. Independent human-query and broad printed-photo quality remain acceptance work.

On this host, native-size thumbnail rendering took a median 0.15 seconds and
B/32 image inference 0.44 seconds. Six-view rendering took 0.89 seconds and
inference 2.56 seconds. The B/32 cold paired canary took 9.61 seconds; median
warm text inference was 0.31 seconds. These figures exclude production queueing,
large-library retrieval, network storage and unrelated simultaneous load.
The generation resource estimate uses observations from matching generations;
these small-corpus measurements are not a Raspberry Pi backfill guarantee.

The generic text floor 0.35 admitted a candidate for only one of the 32 B/32
queries. The pinned B/32 visual Space therefore defaults to 0.20, with an
administrator's explicit per-Space override taking precedence. This is an
engineering calibration; it does not establish a false-positive rate for
out-of-domain photographs or other CLIP checkpoints.

The regression replay queries the actual authorized native-vector store and
verifies every recipe's measured top-five result. The browser lane separately
uses original tiny ONNX towers to exercise upload, indexing, fallback, image
query, Model-as-query and responsive UI. Tiny fixtures do not supply quality
measurements.

## Pinned assets

B/32 uses the MIT-licensed [Xenova export](https://huggingface.co/Xenova/clip-vit-base-patch32/tree/d15189d7028b43f1d3e65039190477f6af591c2a):
revision `d15189d7028b43f1d3e65039190477f6af591c2a`. Its existing v1 encoder Space
is preserved: `7f56e23951620776f8a65d4de5441b6ff1eecd1f48c8ddf1eca8a82f1dea2089`.

| Asset | Bytes | SHA-256 |
|---|---:|---|
| Vision float32 graph | 351685709 | `fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40` |
| Text float32 graph | 254058553 | `3f6571f5bad13a97c469c1622e1cfc4d9aef78b79fdbfcff804ca357bfada8cc` |
| Tokenizer | 2224119 | `f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd` |

The MIT-licensed [L/14 export](https://huggingface.co/Xenova/clip-vit-large-patch14/tree/c307790166907339eed5a9a53a249af534102536)
was also inspected at revision `c307790166907339eed5a9a53a249af534102536`.
Full float32 graphs exceed the current per-file and combined asset caps. The
FP16 pair fits those disk caps (vision 608690866 bytes; text 247784246 bytes),
uses standard ONNX operators and float32 I/O, and produced independent
single-tower canaries. Loading the pair through the actual shared runtime
failed with `embedding_worker_oom` before any retrieval query. No L/14 quality
result or production catalog support is claimed; memory limits were retained.

## Reproduction

Run the explicit local-only measurement tools from `backend/`, with preplaced,
verified model assets. Neither command downloads weights or sends library data.

```sh
uv run python -m tests.fakes.visual_candidate_benchmark --help
uv run python -m tests.fakes.visual_existing_benchmark /path/to/clip-b32 /tmp/existing.json
uv run pytest -n 0 tests/integration/modules/search/retrieval/test_visual_quality.py
```

The original native-size measurement SHA-256 is
`3a50787bfcdcdfcdca4bc90395a792e71b67f6fb5428708d4d4cdd5b26f91a36`.


## Printed-part photograph

A separate acceptance case uses the unmodified original
[physical Benchy photograph](https://commons.wikimedia.org/wiki/File:-3DBenchy_3D-printed_at_low_resolution_on_a_BEETHEFIRST_3D_printer_(17203395618).jpg)
by #3DBenchy (CC BY 2.0), the matching existing STL and the same 32 frozen
visual distractors. The full scene includes the printed boat, printer, a large
foreground filament spool and a watermark. No crop, background removal or
query-text tuning is applied. Library names and descriptions are opaque;
visible text in the photograph is retained.

| Profile | Source Model rank among 33 | Cosine score | Top-10 acceptance |
|---|---:|---:|---|
| Existing thumbnail | 13 | 0.66444 | Fail |
| Six views, normalized mean | 1 | 0.71606 | Pass |
| Six views, maximum score | 2 | 0.69456 | Pass |

The same pinned CLIP encoder produced every vector. This is concrete evidence
for one photo-to-Model retrieval case and a multiview improvement on that case.
It does not erase the weaker multiview results on the text-to-shape corpus or
establish a general photo-recall rate. All three ranks, native vectors and
input hashes are frozen in `printed-benchy-vectors.json`; the integration test
replays them through the actual authorized visual store with complete units.
Attribution and original-byte hashes accompany the photograph fixture.

```sh
uv run python -m tests.fakes.printed_photo_benchmark \
  --model-directory /path/to/clip-b32 \
  --output /tmp/printed-photo.json --profile multiview_mean
```

The profile is required explicitly; the command preserves all profile results
and fails if the selected profile misses the fixed top-10 target. Selecting
`thumbnail` retains the measured failure.
