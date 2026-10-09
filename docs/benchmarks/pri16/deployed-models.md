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
quality values are in [the anonymized records](deployed-models-aad74576.jsonl).\n[Statistics](deployed-statistics-aad74576.json) include median, p95, dispersion\nand reproducible bootstrap confidence intervals for each admitted comparison.

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

These are correctness checks, not thirty-sample browser performance or reference
visual-parity qualification. WebGL remains the default.


## Coverage assessment

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | renders deployed geometry in the selected browser backend | Happy | Six private STL/3MF cases, both backends | Matching bytes, ready geometry, effective backend, usable PNG | Native browser | ✅ twelve checks |
| 2 | changes the real-model camera | Happy | Zoom control on each case/backend | Camera pose changes | Native browser | ✅ twelve checks |
| 3 | preserves the source-loader refusal | Error | Largest STL exceeds prepared-source policy | resource_limit, no comparison falsely reported | Native integration | ✅ recorded refusal |
| 4 | preserves real-model image quality | Edge | Thirty observations per admitted case | Fixed foreground/RGBA gates | Physical development probe | ❌ medium STL and large 3MF fail |
| 5 | qualifies server complete-flow performance | Happy | Conformant Linux/Docker NVIDIA and Intel | At least 1.5x median improvement | Hardware qualification | ❌ no qualification |
| 6 | qualifies browser performance on real Models | Happy | Frozen baseline/candidate browser corpus | Required interaction and first-visible-frame thresholds | Hardware qualification | ❌ not established by correctness checks |
