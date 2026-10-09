# PRI-16 WebGPU qualification

The `webgpu-pilot` extra is research tooling. Production thumbnail and canonical
view Jobs still use CPU rendering. No GPU speedup, physical-device qualification,
or deployment support is claimed by this change. Browser modernization and
server adoption are separate deliverables.

## Runtime and abstraction

Use Python 3.14 and `uv sync --extra dev --extra full --extra webgpu-pilot --frozen`.
`wgpu==0.32.0` is the latest stable PyPI release checked at implementation start;
its Python requirement is >=3.11 and it imports under Python 3.14.8. The ordinary
CPU/full installation excludes it. The lock contains its platform wheels.

The operation-specific ports in `scripts/render_backend.py` keep native objects
inside adapters. CPU preparation supplies bounded face chunks through the
existing `DeferredRasteriser` seam. WebGPU rasterizes face identifiers and coverage into
offscreen attachments. Canonical interpolation uses the original submitted
geometry precision on the CPU before shared material/lighting and quantization.
This avoids amplification of float32 interpolation errors at translucent edges
during alpha-aware resizing. It does not eliminate hardware coverage/depth
differences; all quality gates still apply.
Camera selection, normal preparation, matte policy, postprocessing and encoding
remain shared. One session owns one pipeline and at most one active frame.
Depth radii outside the normal finite float32 range are refused before native
allocation, including values that would overflow or be flushed to zero by a GPU.

Adapters are selected by capabilities and limits, independently of vendor.
Explicit `--adapter` selection matches an exact device name and refuses ambiguity.
Only `DiscreteGPU` and `IntegratedGPU` adapter types count as physical acceleration.
Software conformance requires `--allow-software`; it never qualifies acceleration.
No device initialization occurs in API startup or request handlers.

Allocations include color/depth attachments, one bounded vertex upload, uniform
storage and one aligned readback buffer. Context/driver overhead is not physical
VRAM accounting. Retained host geometry has a separate 128 MiB default ceiling
and is reported as retained_geometry_bytes; exceeding it refuses the frame
before its next upload. Pixel interpolation uses bounded 65,536-pixel batches.
Geometry is released on frame close. Session cleanup drops native wrappers and
collects device/queue reference cycles after destruction: native memory pressure
does not trigger Python garbage collection. Include that cleanup cost in timing. This field measures retained arrays, not
peak RSS or interpolation/readback temporaries. Whole-worker RSS, requested storage and optional device-wide
telemetry are separate measurements; shared Intel memory contributes to host RSS.
Each submission drains through mapping a four-byte copy into the owned staging
buffer before reusing the vertex buffer. Final image readback happens once. readback_ms measures only native readback;
cpu_resolve_ms separately measures face-ID validation, original-precision
interpolation, canonical shading and publication into CPU pixels. Historical
reports before this split include CPU resolution in readback_ms.
This public mapping API avoids an ABI mismatch in 0.32.0's queue-completion
callback. No private wgpu APIs or package patches are used.

## Diagnostic

Run only the disposable command with device access:

```sh
cd backend
uv run --extra webgpu-pilot python -m scripts.webgpu_doctor
# Conformance on software, explicitly not hardware evidence:
uv run --extra webgpu-pilot python -m scripts.webgpu_doctor --allow-software
```

The command exits nonzero if its requested physical adapter is unavailable.
It enumerates identity, creates a bounded frame and verifies nonempty readback.
Missing packages, capabilities, initialization, compilation, allocation,
execution and readback retain typed failures. A diagnostic pass is not performance
or recovery qualification.

## Freeze inputs before measuring

Keep private sources, manifests and raw results outside version control. Generate
redistributable controls with `scripts.viewer_representation_corpus`. Declare each
STL/3MF workload family before observing output; cover ASCII/binary STL, retained
instances, thin parts, holes, curved surfaces, disconnected parts, reflected and
sheared instances, overlaps and large coordinates. A manifest freezes source
hashes, software/lock versions, tested commit, workload flow and quality policy.
It requires a clean committed checkout and refuses policy/source/software drift.

```sh
uv run python -m scripts.render_qualification \
  --source /absolute/control.stl --family standard-stl \
  --flow preview --output /absolute/preview-manifest.json
uv run --extra webgpu-pilot python -m scripts.gpu_render_pilot \
  --candidate wgpu --source /absolute/control.stl \
  --manifest /absolute/preview-manifest.json --trials 30 \
  --flow preview --output-dir /absolute/preview-results --telemetry
```

Repeat separately for `--flow multiview` (640x480 thumbnail plus six canonical
matte views), both physical verification devices and each predeclared family.
The CLI preserves raw failures, image quality, determinism, identity, source hash,
phase costs and supervision costs. Timing summaries include count, median,
nearest-rank p95, population standard deviation and a reproducible bootstrap
95% median confidence interval. A minimum of 30 observations is required even
for the preliminary cost/quality/hardware gate. The final decision never reports
production adoption.

`cold` creates a new context per observation; `reused` shares a context and
prepared geometry within one worker invocation. Both compare against retained CPU
preparation. With the default --processes 1 these are **not** thirty fresh worker processes.
Use --processes 30 for independent supervised workers, retaining every result,
including failures. Each worker still uses the declared --trials count. The supervised cost
includes the paired CPU reference and must not be represented as GPU-only Job
latency. `full_cold_source_visual_ms` is explicitly an estimate adding shared
imports/load/preparation, not upload-to-publication latency.

The immutable quality gate is identical foreground masks at alpha >=128 and
maximum RGBA difference <=8/255. Independent protected-part assertions and repeated
output stability are also required. Equal masks cannot alone prove geometry
correctness. Failures narrow or reject qualification; do not adjust tolerances.

## Disposable container diagnostic

Build the research image from the repository root:

~~~sh
docker build -f deploy/gpu-qualification/Dockerfile -t printstash-gpu-check .
# No device: must refuse physical acceleration.
docker run --rm --user 12345:12345 printstash-gpu-check
# Mesa/Intel on native Linux; grant only the selected render device and its group.
docker run --rm --user "$(id -u):$(id -g)" \
  --device /dev/dri/renderD128 \
  --group-add "$(stat -c %g /dev/dri/renderD128)" printstash-gpu-check
# NVIDIA Container Toolkit on native Linux; graphics is required for Vulkan.
docker run --rm --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=graphics,utility printstash-gpu-check
~~~

This image runs only a disposable diagnostic and is not a GPU-enabled application
deployment. Device access is granted to that command, not to the API. An absent
device, missing driver or denied render-node permission must fail explicitly.
Do not treat --allow-software as a workaround for physical qualification.

## Candidate decision

[Sixty physical Windows observations](benchmarks/pri16/README.md) reject production
adoption of candidate 969b2966: the curved control exceeds the unchanged RGBA gate,
and six context initializations fail. Raw failures and the reproduction probe are
retained. This is not a Linux/Docker or complete-flow performance qualification.
The revised 649cd8f2 candidate passes all sixty cube/torus control observations
with zero RGBA difference and no failures. Its face-ID readback and session
cleanup address the observed precision and repeated-context failures. This
does not qualify the full corpus, complete-flow speed, recovery or Linux/Intel
deployment. See the same evidence record for both historical and revised results.

## Adoption prerequisites

Require >=1.5x median complete-flow improvement per declared target family on
both RTX 5060 and Intel N100/N150-class physical hardware, separately for preview
and multiview. Record native backend, driver, exact commit and versions. Linux
and Docker runs are mandatory; WSL development results cannot replace them.
Physical cancellation, allocation failure, device loss, deadline and process
termination must prove cleanup and subsequent-job recovery. Tests with a fake
native boundary verify contracts only. Missing hardware must fail explicitly.

The initial WSL environment exposes an RTX 5060 to `nvidia-smi`, but wgpu sees only
llvmpipe Vulkan/OpenGL software adapters. A bounded software diagnostic and one
unsupervised 64x48 cube comparison passed (zero channel/mask difference); the
bounded disposable worker refused native software execution. These observations
are development checks, not qualification evidence or speedup claims. A separate Windows
Python 3.14.8 diagnostic selected the physical RTX 5060 through Vulkan (driver
610.88) and completed bounded rendering/readback; D3D12 also enumerated the card.
This is a development check on an uncommitted snapshot, not qualification.
Intel and Linux/Docker physical results remain outstanding.

Until those gates pass, do not inject this adapter into derivative/visual Jobs,
add server backend settings, change recipes, or advertise a GPU deployment image.
Adoption must retain disposable supervision, shared host-memory admission,
one execution per device, bounded allocation leases through cleanup, one CPU
retry within the original deadline, atomic publication and recipe reconciliation.
CPU recovery must never count as GPU success.

Primary references: [wgpu-py guide](https://wgpu-py.readthedocs.io/en/stable/guide.html),
[wgpu-py backends](https://wgpu-py.readthedocs.io/en/stable/backends.html),
[PyPI release metadata](https://pypi.org/project/wgpu/0.32.0/),
[existing GPU decision](adr/0015-gpu-rendering.md).

## Automated coverage matrix

These rows describe software contracts, not hardware qualification. All test names
below are under the mirrored `backend/tests/{unit,integration,e2e,repo}` tiers.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `test_ignores_vendor_identity` | Edge | Different vendor labels | Physical eligibility unchanged | Integration | ✅ |
| 2 | `test_refuses_software_acceleration_claims` | Edge | CPU/unknown adapter type | No acceleration claim | Integration | ✅ |
| 3 | `test_closes_owned_resources_once` | Happy | Active frame and repeated close | Frame/device closed once | Integration | ✅ |
| 4 | `test_refuses_cross_thread_access` | Error | Foreign thread | Closed refusal, owner remains usable | Integration | ✅ |
| 5 | `test_preserves_dependency_failure` | Error | Missing optional import | Typed dependency refusal | Integration | ✅ |
| 6 | `test_preserves_capability_refusal` | Error | No eligible adapter | Capability refusal survives | Integration | ✅ |
| 7 | `test_shades_complete_readback_with_canonical_callback` | Happy | Valid face readback | Canonical pixels and coverage published | Integration | ✅ |
| 8 | `test_defers_publication_until_finish` | Happy | Multiple submissions | CPU image untouched until final readback | Integration | ✅ |
| 9 | `test_refuses_excess_allocation_before_native_work` | Edge | Over allowance | Allocation refused before native creation | Integration | ✅ |
| 10 | `test_accepts_exact_allocation_limit` | Edge | Exact allowance | Requested accounting equals limit | Integration | ✅ |
| 11 | `test_refuses_device_texture_limit` | Edge | Device limit below frame size | Refusal before allocation | Integration | ✅ |
| 12 | `test_refuses_competing_frame` | Edge | Session already has frame | Second frame refused | Integration | ✅ |
| 13 | `test_refuses_invalid_readback_without_publication` | Error | Truncated/nonfinite readback | Image/depth remain unpublished | Integration | ✅ |
| 14 | `test_recovers_session_after_device_failure` | Error | Simulated device failure | Old frame closes, fresh session renders | Integration | ✅ |
| 15 | `test_refuses_repeat_publication` | Error | Finished frame | No second readback/publication | Integration | ✅ |
| 16 | `test_binds_source_digest` | Happy | Frozen control | Corpus hash and immutable gates retained | Integration | ✅ |
| 17 | `test_refuses_dirty_qualification_source` | Error | Uncommitted source | Freeze refused | Integration | ✅ |
| 18 | `test_refuses_source_mutation` | Error | Changed source bytes | Verification refused | Integration | ✅ |
| 19 | `test_refuses_quality_tolerance_mutation` | Error | Relaxed RGBA tolerance | Verification refused | Integration | ✅ |
| 20 | `test_refuses_software_mutation` | Error | Changed installed version | Verification refused | Integration | ✅ |
| 21 | `test_refuses_workload_flow_mutation` | Error | Different flow | Verification refused | Integration | ✅ |
| 22 | `test_reports_nearest_rank_p95` | Happy | Known distribution | Median/p95/dispersion match independent values | Unit | ✅ |
| 23 | `test_reproduces_confidence_interval` | Edge | Fixed samples | Reproducible median interval | Unit | ✅ |
| 24 | `test_retains_absence_of_successful_observations` | Error | No successes | Zero count and absent statistics | Unit | ✅ |
| 25 | `test_refuses_invalid_observations` | Error | Negative/nonfinite timing | Invalid input refused | Unit | ✅ |
| 26 | `test_reports_missing_optional_dependency` | Error | Executable without wgpu | Nonzero diagnostic with no qualification claim | E2E | ✅ |
| 27 | `test_pins_current_optional_webgpu_library` | Happy | Project/lock metadata | Stable optional version pinned | Repo | ✅ |
| 28 | Existing CPU and ModernGL pilot regression tests | Happy | Optional library absent | CPU report retained; historical pilot compatible | Integration | ✅ |
| 29 | test_refuses_depth_not_representable_by_gpu | Error | Overflow, underflow, nonfinite or nonpositive radius | Typed refusal before native allocation | Integration | ✅ |
| 30 | test_matches_cpu_quantization_from_original_precision | Happy | Nontrivial original normals | CPU reference quantization preserved | Integration | ✅ |
| 31 | test_keeps_submitted_geometry_owned_until_readback | Edge | Caller replaces submitted arrays | Original frame remains unchanged | Integration | ✅ |
| 32 | test_resolves_winner_from_later_chunk | Happy | Winner in second submission | Correct chunk supplies canonical shading | Integration | ✅ |
| 33 | test_rejects_invalid_winning_face_without_publication | Error | Negative, fractional, absent, infinite ID | Invalid frame remains unpublished | Integration | ✅ |
| 34 | test_bounds_retained_host_geometry_before_upload | Edge | Exact host ceiling followed by excess chunk | Further upload refused | Integration | ✅ |
| 35 | test_releases_retained_geometry_on_close | Happy | Frame holds geometry | Owned geometry becomes collectible | Integration | ✅ |
| 36 | test_does_not_publish_earlier_chunks_when_later_shading_fails | Error | Later callback returns invalid colors | Entire frame remains unpublished | Integration | ✅ |
| 37 | Physical quality/performance/recovery gates | Edge | NVIDIA and Intel Linux/Docker | Predeclared complete-flow and recovery gates | Hardware | ❌ awaiting physical access |
| 38 | Production backend selection/publication/admission | Happy | Qualified adapter | Existing Job contracts preserved | Integration/E2E | ⏭️ Stage B prohibited until hardware gates pass |
| 39 | Browser modernization | Happy | Browser renderer preference | Worker loading and compatible recovery | Playwright | ⏭️ separate PR |

| Behaviour | Category | Input | Outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| test_releases_partial_native_allocations | Error | Failure at each attachment/buffer allocation | All earlier native resources destroyed | Integration | ✅ |
| test_releases_native_device_wrappers_after_close | Error | Closed owner remains referenced | Native device wrapper becomes collectible | Integration | ✅ |
| test_reports_distinct_resolution_phases | Happy | Independently controlled native readback and CPU shading costs | Reported phase excludes the other cost | Integration | ✅ |

## Fresh-process sampling and encoding controls

Use --processes 30 --trials 1 with a frozen manifest to run thirty fresh
supervised workers per mode. Each attempt has its own directory and raw report;
the aggregate retains failed attempts and summarizes all-attempt and successful
startup-through-cleanup costs separately. A cancelled attempt ends the campaign.
Worker PIDs identify independent child invocations. The pilot refuses existing
output directories instead of overwriting evidence.

Cold/reused workers include their paired CPU reference. Their supervised costs
are not GPU-only execution or upload-to-thumbnail latency. A single observation
per worker does not establish reused-context performance; use --trials 30 when
measuring that separately. This command never grants production qualification.

The shared generated corpus now includes ASCII and binary STL versions of the
same cube, exact duplicate faces and intersecting solids. Source extensions match
their encodings. These join the existing thin, hole, disconnected, curved,
reflected, sheared, translated and retained-instance controls.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | preserves geometry across STL encodings | Happy | ASCII/binary cube | Twelve facets and 20 mm bounds | Integration | ✅ integration/scripts/test_viewer_representation_corpus.py::TestSources::test_preserves_geometry_across_stl_encodings |
| 2 | preserves overlapping faces | Edge | Duplicated 3MF cube | Twenty-four facets, original bounds | Integration | ✅ integration/scripts/test_viewer_representation_corpus.py::TestSources::test_preserves_overlapping_faces |
| 3 | preserves intersecting placements | Edge | Offset solids | Twenty-four facets, 30 mm bounds | Integration | ✅ integration/scripts/test_viewer_representation_corpus.py::TestSources::test_preserves_intersecting_placements |
| 4 | produces deterministic control bytes | Happy | Repeated generation | Identical bytes | Integration | ✅ integration/scripts/test_viewer_representation_corpus.py::TestSources::test_produces_deterministic_control_bytes |
| 5 | refuses existing evidence directories | Error | Prior campaign output | Nonzero exit, original evidence unchanged | Integration | ✅ integration/scripts/test_gpu_render_pilot.py::TestCampaign::test_refuses_existing_evidence_directories |
| 6 | retains independent worker failures | Error | Two processes, absent GPU dependency | Distinct child PIDs, raw failures, CPU results, separate statistics | Integration | ✅ integration/scripts/test_gpu_render_pilot.py::TestCampaign::test_retains_independent_worker_failures |
| 7 | stops a cancelled campaign | Error | Native supervision interrupted | One attempt retained, no next worker | Integration | ✅ integration/scripts/test_gpu_render_pilot.py::TestRun::test_persists_cancelled_decline |
