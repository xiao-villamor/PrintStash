# Shared source execution: 250-source GPU result

**The shared-source candidate completed in 466.162 monotonic seconds, 19.95% below
the best earlier optimized GPU pass (582.315 s) and 33.44% below the rejected
separate-process pass (700.374 s).** All 250 source hashes/sizes, all nine metadata
fields on all 250 rows, the 238 valid thumbnails and the same 12 invalid sources
were verified. This is a GPU-to-GPU comparison; CPU was not rerun.

## Design

One admitted, disposable source worker decodes each source once. Independent
measurement and visual functions share its source-precision arrays; only compact
render geometry crosses to the persistent GPU broker. Native source ownership
ends after clean process exit, before the retained packet finishes GPU rendering
and WebP encoding under their existing separate credits. Source custody remains
bounded throughout. There is no new resident parser pool or second source read.

STL without optional fingerprints uses bounded arrays for basic metadata:
referenced-vertex bounds, exact integer edge pairing, the existing 1e-8 position
weld when necessary, winding checks, connected components and a component-relative
float64 signed volume integral in 4096-face batches. It calculates only the
volume term, not inertia or centre of mass. NumPy and SciPy remain CPU consumers;
Trimesh is not imported on this path. Visual float32 coordinates never replace the
source measurement buffers.

OBJ stored normals/UVs can affect canonical welding, so its analytical loader
remains authoritative. OBJ preview-only repairs still use the lightweight loader.
CAD, retained scenes and optional fingerprints keep their specialized loaders.
All combined formats return to one guarded execution per source. Already-published
outputs retain their publication fences and survive a later worker failure;
a process crash before an output exists does not independently retry that output
inside the same execution.

With optional fingerprints, the direct staged-output path remains in use so
basic outputs are not held until optional analysis finishes. Small analytic views
also retain their old canonical-normal policy; small preview normals use the GPU.

GPU normals, bounds/camera, vertex expansion, rendering and downsampling remain.
The test uses 512×384 output (2× internal supersampling), quality-85/method-2 lossy
WebP with exact alpha. Product default width remains 640. GPU source parsing,
authoritative GPU topology/volume and overlapping multiple readback batches are
not implemented by this candidate.

## Comparable results

Same 250-source selection, in the same order:
`2780f43fa04318c7c8ebbc0b127e250f5887e0b3f4382f2d8800e4d72ccd3824`.
130 STL, 111 OBJ, 9 STEP; 808,880,155 source bytes. Four HTTP uploads and a
40-Artifact outstanding window after the first four cold sources. Cached R2
originals, zero R2 acquisition requests and unchanged source-request ledger.
Detailed process, pool, GPU and host observers remain enabled.

| Metric | Earlier optimized GPU | Control GPU | Separate processes | Shared source |
| --- | ---: | ---: | ---: | ---: |
| Total monotonic seconds | 582.315 | 597.779 | 700.374 | **466.162** |
| Total UTC seconds | 626.608 | 650.899 | 766.257 | **509.649** |
| Guarded executions | 250 | 250 | 491 | **250** |
| Trimesh imports | 250 | 250 | 250 | **120** |
| Accumulated Trimesh import seconds | 278.469 | 332.967 | 279.999 | **135.929** |
| Accumulated mesh-load phase seconds | 343.145 | 414.011 | 429.231 | **213.394** |
| Native admission wait seconds | 721.976 | 698.413 | 1093.683 | **581.145** |
| Claims for the whole native pool | 60 | 53 | 88 | **49** |
| Container CPU seconds | 1671.820 | 1864.560 | 2095.455 | **1394.938** |
| Sampled container RAM peak, MiB | 2064.3 | 1983.9 | 2181.3 | **1902.4** |

Measured runtime image:
`sha256:8bcc4085864cd70f1befd650d725c8e0464d4f5e69790111cd03eb73cf57845d`.
Uncommitted source manifest:
`e8991903ac633bfe2d8e7ccd7058df9d75613436db44af676b506be8b7a0baa4`,
based on `de69c1d1abc22847e54878d9e8af6f0be0e731ce`. The source tree, immutable
image, local patch and file manifest were retained before measurement. No commit
or CI was started until this improvement had been measured. A subsequent guard
excludes optional fingerprints from deferred handoff; the benchmark had them
disabled for every input, so its execution branch is unchanged. Focused tests
first failed for that optional STL/OBJ boundary, then passed after the guard.

This is one later pass on a shared WSL host, not a randomized multi-pass study.
UTC exceeds the monotonic interval by 43.488 s; both clocks show an improvement
(18.67% UTC versus the best earlier pass). No timing correction was applied.
Accumulated stage/lock times overlap and must not be added as elapsed time.

## Where time and memory remain

- Worker supervision: 732.921 accumulated seconds, down from 1269.464.
  Basic measurement spans are now 72.177 s versus 31.272 s in the split run:
  SciPy import/topology work now occurs in this phase. Avoided Trimesh imports and
  duplicated loading, rather than a claimed faster individual volume kernel,
  account for the architectural improvement.
- Native pool: 4 slots / 4,146,847,744 bytes. Mean active slots 1.574; 49 sources
  still claim the whole pool. 13,019 admission polls, 11,517 blocked by memory.
  Native flock wait/hold: 159.204 / 89.323 accumulated seconds; 13,269 acquisitions.
  Less duplicated work reduces coordination, but conservative admission remains
  a substantial limit.
- GPU: 236 rendered views in 231 actual batches (227 single, 3 double, 1 triple),
  plus one cache hit. 232 GPU normal uploads. Render trace spans total 9.303 s;
  broker rendering transfers total 2.114 s; map/fence wait totals 1.017 s.
  The render trace also sees the cached result, hence 232 calls versus
  231 submitted batches. Increasing GPU work alone cannot recover the remaining
  preparation/admission time.
- Geometry upload: 326,458,172 bytes. Local IPC sends 568,346,101 bytes in 8.929 s
  (60.70 MiB/s), reads 569,708,760 in 46.362 s (11.72 MiB/s), including protocol
  waits and 165 read poll timeouts. These are socket measurements, **not PCIe
  bandwidth**; host transfer spans are not GPU kernel timestamps.
- GPU logical budget stays at 1 GiB and the geometry packet limit at 48 MiB.
  End-of-run resident reservation is 203,802,096 bytes across 28 entries.
  Physical adapter samples peak at 1522 MiB of 8151 MiB and include unrelated host
  applications; they are not exclusive PrintStash VRAM. The qualified adapter
  reports NVIDIA GeForce RTX 5060 through the WSL D3D12/OpenGL backend.
- Handoff: 233 completions, zero CPU recoveries; codec queue/execute totals
  8.528 / 10.775 s. CPU codec work remains separate from parser admission.
  Zero OOM and zero swap. The test-owned container was stopped after completion.
- Upload-to-thumbnail median/p95 improves from 99.379/140.813 s in the split run
  to 61.972/87.466 s. Against the previous control it improves from 81.518/132.646 s.
  Thumbnail publication again follows metadata (median STL/OBJ lag 0.995 s);
  avoiding duplicated work improves the user's overall queue time.

The next measurable bottlenecks are OBJ startup/loading and conservative unknown
complexity admission. Recalibrating memory requires per-phase hard-limit probes,
not replacing safety estimates with average RSS. GPU topology could consume
integer edge/component buffers, but needs source-precision qualification and
explicit ownership/fallback rules before replacing authoritative measurements.
Multiple GPU batches in flight become useful when preparation can keep them fed;
the measured fence wait alone is about one second across the entire run.

## Validation and coverage matrix

The local differential probe checked all 130 STL sources: 118 exact measurement
matches and the same 12 invalid sources. Source arithmetic initially differed by
a few ULPs in five inputs; matching the canonical edge subtraction order removed
all differences before the full benchmark. Decoded output audit: 238 lossy RGBA
WebP images, all 512×384, no empty alpha, no clipped borders, transparent corners,
2,277,688 total encoded bytes.

Paths below are relative to `backend/tests/`. Requirements were listed before
implementation. Final full-suite and latest-commit CI results are recorded in [PR #430](https://github.com/xiao-villamor/PrintStash/pull/430).

| Behaviour | Evidence | Status |
| --- | --- | --- |
| Exact source dimensions/count/volume; source arrays unchanged | `unit/modules/media/test_array_measurements.py::TestGeometryFromArrays::test_preserves_source_measurements`; 250-row benchmark diff | ✅ |
| Open, inconsistent, inverted and nonmanifold volume evidence | Same parametrized unit test | ✅ |
| Cavities and distant components retain signed precision | Same test; `test_preserves_component_relative_precision` | ✅ |
| Unreferenced vertices do not expand bounds | `TestGeometryFromArrays::test_ignores_unreferenced_vertices` | ✅ |
| Nonfinite integral retains explicit unavailable evidence | `TestGeometryFromArrays::test_reports_nonfinite_integral` | ✅ |
| Basic STL load/measure/preview does not import Trimesh | `integration/modules/media/thumbnail_engine/test_visual_dependencies.py`, including a closed STL | ✅ |
| One actual guarded execution per combined STL/OBJ/3MF source | `integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_retains_one_worker_per_source`; 250-process benchmark | ✅ |
| Published outputs survive later worker errors | `TestSharedMeshSource::test_publishes_outputs_independently` | ✅ |
| Cancellation propagates; result freed before host credit | `TestSharedMeshSource::test_preserves_cancellation`, `test_releases_result_before_host_credit` | ✅ |
| Optional fingerprint work does not defer basic outputs | `TestStagedMeshPublication::test_keeps_basics_ahead_of_optional_analysis` (STL/OBJ/3MF), red then green | ✅ |
| Clean reaping and bounded host/GPU/codec ownership | Existing `test_mesh_isolation.py`, `test_thumbnail_handoff.py`, focused 80-case pass | ✅ |
| Small preview GPU normals preserve analytic-view policy | `unit/modules/media/test_mesh_render.py`; GPU packet roundtrip | ✅ |
| Real HTTP ingestion retains metadata/thumbnail contracts | Existing STL/OBJ ingest E2E plus 250-source real HTTP benchmark | ✅ |
| Same images, invalid-source set, hashes, metadata and elapsed comparison | Immutable-image benchmark + decoded audit | ✅ |
| Plain source arrays release after processing without retaining their buffers | `unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_releases_plain_source_buffers` | ✅ |
| Basic geometry numeric dependencies are declared directly | `repo/test_python_runtime.py::TestPythonRuntime::test_declares_basic_geometry_runtime_dependencies`; Deptry and offline lock check | ✅ |
| CPU rerun | Explicit user preference; earlier CPU result retained | ⏭️ |

Local unit media + runtime-dependency validation passed **1612 tests** after
updating old test doubles to the new source-loader seam. The two broad local
attempts were stopped on test-structure issues (3767 passed / 2 failed) and then
the obsolete Trimesh reclamation expectation (7376 passed / 1 failed); these are
not reported as green full runs. The focused recheck closes all four old loader/
reclamation assumptions and asserts actual plain-buffer release. Ruff, Pyright,
Deptry and the offline lock check pass. Remote CI initially caught the missing
direct SciPy declaration: the lock update only adds the direct dependency edge,
without changing any installed package/version or benchmark code. The PR records
the final exhaustive CI gate result for the latest commit.

[Follow-up: single-pass CPU preparation](compute-cpu-preparation-250.md) ·
[Interactive HTML report (Spanish)](compute-pipeline-report.es.html) ·
[Sanitized detailed counters](compute-shared-source-250-summary.json) ·
[CPU/GPU diagram](compute-pipeline-explained.es.md) ·
[Rejected split-worker result](compute-visual-paths.md)

## Standalone HTML analysis

The [Spanish HTML report](compute-pipeline-report.es.html) embeds 28 stage and
coordination rows, CPU/GPU ownership, concurrency and memory boundaries, and the
four comparable GPU passes. It works offline, distinguishes nested accumulated
spans from cohort elapsed time, and marks unmeasured stages rather than assigning
zero. It adds no runtime change or benchmark run.

| Behaviour | Evidence | Status |
| --- | --- | --- |
| Every embedded stage statistic matches its source | All 28 rows checked against the sanitized JSON (unmeasured rows carry no statistics) | ✅ |
| Filters select owners and detail rows | Chromium: default, all details and GPU filter counts | ✅ |
| Sorting uses accumulated time or p95 | Chromium: supervision first by sum; admission first by p95 | ✅ |
| Export preserves the measured dataset | Chromium download parsed and compared with all embedded rows | ✅ |
| Printing includes all rows and restores filters | Chromium beforeprint/afterprint event assertions | ✅ |
| Report works on mobile and without JavaScript | Chromium 390px width without document overflow; all 28 static rows visible with JavaScript disabled | ✅ |
| Offline report needs no remote assets | Chromium: zero HTTP requests and zero page errors; local links and unique anchors checked | ✅ |
| New CPU/GPU benchmark | Documentation only; existing immutable measurements reused | ⏭️ |
