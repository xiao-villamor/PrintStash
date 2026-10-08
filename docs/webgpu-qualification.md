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
existing `DeferredRasteriser` seam. WebGPU rasterizes normals and coverage into
offscreen attachments; the canonical CPU callback supplies material/lighting.
Camera selection, normal preparation, matte policy, postprocessing and encoding
remain shared. One session owns one pipeline and at most one active frame.

Adapters are selected by capabilities and limits, independently of vendor.
Explicit `--adapter` selection matches an exact device name and refuses ambiguity.
Only `DiscreteGPU` and `IntegratedGPU` adapter types count as physical acceleration.
Software conformance requires `--allow-software`; it never qualifies acceleration.
No device initialization occurs in API startup or request handlers.

Allocations include color/depth attachments, one bounded vertex upload, uniform
storage and one aligned readback buffer. Context/driver overhead is not physical
VRAM accounting. Whole-worker RSS, requested storage and optional device-wide
telemetry are separate measurements; shared Intel memory contributes to host RSS.
Each submission drains through mapping a four-byte copy into the owned staging
buffer before reusing the vertex buffer. Final image readback happens once.
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
preparation. These are **not** thirty fresh worker processes. Run at least thirty
independent command invocations into separate output directories to record fresh
process costs; preserve every result, including failures. The supervised cost
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
| 7 | `test_shades_complete_readback_with_canonical_callback` | Happy | Valid normal readback | Canonical pixels and coverage published | Integration | ✅ |
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
| 29 | Physical quality/performance/recovery gates | Edge | NVIDIA and Intel Linux/Docker | Predeclared complete-flow and recovery gates | Hardware | ❌ awaiting physical access |
| 30 | Production backend selection/publication/admission | Happy | Qualified adapter | Existing Job contracts preserved | Integration/E2E | ⏭️ Stage B prohibited until hardware gates pass |
| 31 | Browser modernization | Happy | Browser renderer preference | Worker loading and compatible recovery | Playwright | ⏭️ separate PR |
