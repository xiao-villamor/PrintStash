# Real Models from the running deployment

The existing deployment at localhost:3000 contains 85 live STL/3MF Artifacts
(46 STL and 39 3MF, totaling 392,282,460 bytes), plus seven out-of-scope STEP
Artifacts. A read-only inventory used the application's live scopes. Each mesh
was copied into a private local corpus and checked against its stored SHA-256
and byte count. No Artifact, Model, Job or deployment configuration was changed.

Small, median-size and largest Artifacts were selected separately for STL and
3MF before measurements. Original files, names, storage paths, database identifiers
and screenshots remain local. Published cases use anonymous labels.

## Server preview measurements

Candidate aad74576 (code unchanged from d1679b86), Python 3.14.8, wgpu 0.32.0,
NVIDIA RTX 5060, Mesa 26.2.4 Dozen in WSL. Dozen requires an explicit
non-conformant-driver override; these are **development measurements**, not
eligible production-driver qualification.

Each admitted case has 30 CPU/GPU comparisons using cold GPU contexts and
retained preparation for both methods. The 3MF preview path retains instances.
All 150 comparisons completed; outputs were stable. Raw phases, failures and
quality values are in [the anonymized records](deployed-models-aad74576.jsonl).
[Statistics](deployed-statistics-aad74576.json) include median, p95, dispersion
and reproducible bootstrap confidence intervals for each admitted comparison.

| Case | Bytes | Triangles | Comparisons passing quality | Maximum RGBA difference | Foreground-mask differences | Estimated CPU/GPU median ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| STL small | 14,284 | 284 | 30/30 | 1 | 0 | 0.771 |
| STL medium | 455,584 | 9,110 | 0/30 | 11 | 0 | 0.947 |
| STL large | 100,481,584 | Not admitted | 0 comparisons | N/A | N/A | N/A |
| 3MF small | 1,701 | 12 | 30/30 | 0 | 0 | 0.645 |
| 3MF medium | 635,447 | 44,178 | 30/30 | 0 | 0 | 0.774 |
| 3MF large | 8,063,202 | 329,264 | 0/30 | 247 | 1 | 0.895 |

The largest STL retains the source loader's resource_limit refusal before a
comparison starts. This direct prepared-source pilot does not exercise the
application's bounded streaming fallback, so the refusal must not be described
as a failure of the deployed thumbnail endpoint.

Ratios below one indicate slower GPU estimates. The estimate adds measured
shared import/load/preparation costs to each render observation. It is not actual
upload-to-thumbnail latency or GPU-only supervised Job cost. Other development
validation overlapped. These results reject adoption of the current candidate
for this sample; they do not characterize conformant native Linux or Intel.

A subsequent [pipeline review](../../gpu-thumbnail-pipeline-review.md) confirms
that the medium and large 3MF sources contain validated embedded thumbnails.
Production prefers those images, so their forced-raster measurements are renderer
controls rather than thumbnail-only pipeline costs. The review also measures a
revised adapter-selection path; the archived observations here remain unchanged.

## Browser correctness on the same Models

[Native Chrome results](deployed-browser-72c66550.json) cover all six selected
cases with both WebGL and WebGPU on frontend commit 72c66550: twelve successful
checks. Every check verifies the source hash, effective renderer, ready geometry,
zoom response and a nonempty 1280x960 PNG. The 100 MB STL renders successfully in
both browser backends.

The three 3MFs use offline copies of the application's viewer-STL conversion;
their browser hashes identify those derived STL bytes, not the original archives.
The browser fixture injects the private bytes at its previewFetcher seam.
This verifies real geometry through parsing/rendering/capture, not authentication
or the deployment's durable-preparation HTTP endpoints.

Two harness problems are retained locally and excluded from product conclusions:
an initial route did not match Vite's module query, caught by the source-hash
assertion; then sending the largest source through DevTools exceeded Chrome's
100 MiB protocol buffer. A token-protected loopback stream supplied the large
file without exposing it on the LAN. Those attempts are not viewer failures.

## Browser performance development comparison

The same six cases completed 360 measured observations (30 per case and viewer),
plus twelve retained warmups, with no failed attempts. Each observation verifies
the intended source hash, renderer and 1280x960 screenshot. The baseline is the
original application at 8d70c556; the candidate is 72c66550.

[Frozen protocol and runtime metadata](deployed-browser-manifest-72c66550.json)
identify the corpus, application commits, browser and adapter.
[Raw observations](deployed-browser-performance-72c66550.jsonl) retain every frame
interval and attempt. [Statistics](deployed-browser-statistics-72c66550.json)
include medians, p95, dispersion and seeded bootstrap confidence intervals.
Frame columns below are the median of each observation's p95, in milliseconds.
Readiness columns are a proxy, not first-visible-frame latency.

| Case | Baseline/candidate observations | Original frame p95 | WebGPU frame p95 | Original readiness proxy | WebGPU readiness proxy |
| --- | ---: | ---: | ---: | ---: | ---: |
| real-3mf-large | 30/30 | 7.10 | 7.10 | 549.2 | 1046.8 |
| real-3mf-medium | 30/30 | 7.10 | 7.10 | 495.7 | 860.4 |
| real-3mf-small | 30/30 | 7.10 | 7.10 | 473.0 | 825.7 |
| real-stl-large | 30/30 | 7.20 | 7.10 | 912.8 | 1867.7 |
| real-stl-medium | 30/30 | 7.10 | 7.10 | 477.9 | 853.4 |
| real-stl-small | 30/30 | 7.10 | 7.10 | 477.4 | 854.1 |

Neither declared large case demonstrates the required 20% interaction improvement.
Readiness callbacks have different semantics: the candidate waits for its first
mesh draw, while the original can signal earlier. Their proxy ratio cannot
establish the required first-visible-frame gate. Headless RAF intervals also
include refresh scheduling. No visual-parity or performance qualification is
claimed; WebGL remains the default.

### Reproduction and boundaries

Browser comparison protocol:

- Candidate 72c66550602fc36ef2fa206bf1bfca8b30f9ca1b; original application 8d70c556.
- Backported candidate benchmark fixture only; original application source unchanged.
- Native Chrome 154.0.8037.98, Windows RTX 5060, NVIDIA driver 610.88.
- Fixed 900x700 viewport, 640x480 canvas, DPR 1, 1280x960 PNG.
- Six anonymized real-source cases; source hashes verified for every context.
- Thirty observations per variant/case plus one retained warmup.
- Alternating variant order; fresh browser contexts, reused browser process, warm dedicated Vite servers.
- 120 RAF intervals per observation while alternating fit and zoom.
- Readiness-plus-two-RAF is a proxy after source hashing, not actual first-visible paint.
- Readiness semantics differ: the candidate waits for its first mesh draw; the original callback can fire earlier. Do not interpret proxy ratios as first-visible-frame regression or improvement.
- Source download recorded separately; it uses token-protected private loopback file streaming.
- Scene preparation is offline application viewer-STL conversion, not deployed HTTP preparation.
- Headless RAF intervals include refresh scheduling; GPU execution durations are not measured.
- These development measurements do not qualify production browser performance or visual parity.

The fixture SHA-256 is
f44a886e5386a2ca02413e37d3e1bf5704c033e48153f39b1cda93b3bf7fc65b
for both checkouts. The candidate fixture was copied to the baseline without
changing its application source. Dedicated Vite servers use ports 3291 (baseline)
and 3292 (candidate), leaving the deployment untouched.

[Replay script](browser-deployed-controls.mjs) preserves the measured protocol.
Install Playwright Core 1.60.0, set PLAYWRIGHT_MODULE to its importable module
specifier and CHROME_PATH to the Chrome executable. Provide the six matching
private-deployed-corpus/real-{stl,3mf}-{small,medium,large}.stl files locally and
start from a directory without private-browser-performance-72c66550. Three 3MF
inputs must be converted with the application's viewer-STL converter first.
The archived script only makes the dependency/executable paths configurable;
the measured script used those exact Windows paths. Private file bytes and
screenshots are not part of this repository.

## Coverage assessment

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | renders deployed geometry in the selected browser backend | Happy | Six private STL/3MF cases, both backends | Matching bytes, ready geometry, effective backend, usable PNG | Native browser | ✅ twelve checks |
| 2 | changes the real-model camera | Happy | Zoom control on each case/backend | Camera pose changes | Native browser | ✅ twelve checks |
| 3 | preserves the source-loader refusal | Error | Largest STL exceeds prepared-source policy | resource_limit, no comparison falsely reported | Native integration | ✅ recorded refusal |
| 4 | preserves real-model image quality | Edge | Thirty observations per admitted case | Fixed foreground/RGBA gates | Physical development probe | ❌ medium STL and large 3MF fail |
| 5 | qualifies server complete-flow performance | Happy | Conformant Linux/Docker NVIDIA and Intel | At least 1.5x median improvement | Hardware qualification | ❌ no qualification |
| 6 | qualifies browser performance on real Models | Happy | Frozen baseline/candidate browser corpus | Required interaction and first-visible-frame thresholds | Hardware qualification | ❌ interaction target not demonstrated; actual first-visible-frame and visual parity remain unqualified |
