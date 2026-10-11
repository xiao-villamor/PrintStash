# Compact GPU visual preparation

This change moves visual welding, angle-weighted normals, bounds and framing to
portable WebGPU passes. It does not change authoritative geometric measurements.
Integer atomics give bounded visual normal accumulation without vendor extensions.
Degenerate faces, excessive local degree and hash congestion fall back locally to
flat normals; malformed descriptors and invalid source indices remain errors.

| Behaviour | Category | Input | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| Transfer compact geometry | Happy | Canonical, deferred, visual packets | Same identity and source arrays; no copied staging body | Unit | Pass |
| Refuse invalid staging | Error | Truncated header/body, incompatible camera, invalid counts | Rejected before GPU exchange | Unit | Pass |
| Validate owner arrays | Error | NaN, excessive coordinates, invalid face indices | Owner refuses input | Unit | Pass |
| Weld visual duplicates | Happy | Separate indices with identical positions | Angle-weighted smooth normals agree within visual tolerance | GPU diagnostic | Pass |
| Isolate degenerate/high-degree vertices | Edge | Valid body with degenerate or overfull neighborhood | Valid faces survive, no whole-model retry | GPU diagnostic | Pass |
| Keep batching bounded | Edge | Multiple prepared models under a device budget | All buffers charged, one bounded submission/readback | Integration | Pass |
| Complete cached mixed-format ingestion | Happy | At least200 identical cached STL/OBJ/STEP sources | Durable requested outputs, timings and batch counts | Deployment | Pending |

Visual acceptance belongs to the user; images need not be pixel-identical to the
CPU renderer. Renderer-only timing does not establish ingestion throughput. GPU
measurements will use the same selected sources and retain the historical CPU
baseline without rerunning CPU ingestion. No new R2 requests are permitted.

## Executed gates

- 209 focused media/compute tests passed, covering immutable geometry, lazy CPU
  recovery, cache/admission boundaries and batch association.
- 52 compact-upload, ownership, bounds, status/API and OpenAPI tests passed after
  switching to mapped upload slabs. The OpenAPI change adds four diagnostic
  counters; ingestion and derivative responses do not change.
- 28 explicit GPU diagnostic cases passed in Docker/WSL on the RTX 5060 using the
  image's Mesa 25.0.7 D3D12/OpenGL path and wgpu 0.32. This includes real welding,
  overfull/cancelling/degenerate neighborhoods, grouped readbacks, caches,
  postprocessing and exceptional resource cleanup. Software test doubles cover
  injected allocation/copy failures separately; these are not physical OOM tests.
- Backend Pyright passed with 0 errors. The shared encoder/profile/visual-recipe
  selection passed 224 core tests and 83 backend tests.

The matched deployment benchmark uses 200 cached sources (84 STL, 83 OBJ, 33 STEP).
The baseline completed 200 original verifications and 200 thumbnails, with every
requested metadata/thumbnail derivative ready and no new R2 requests. Candidate
source-to-publication measurement is pending; no speedup or visual acceptance is
claimed by the shader diagnostic results.

The WSL monotonic and UTC durations diverged during the baseline. Windows
Stopwatch/QPC observations bracket the benchmark independently; wrapper durations
and all clock domains are retained. No clock was changed or timing corrected by
an inferred factor. Device counters and reserved-byte snapshots are not physical
VRAM peaks or GPU-busy percentages.
