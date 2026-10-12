# Optional portable compute

PrintStash selects execution below existing ingestion, derivative and inference interfaces. Default **auto** placement remains CPU unless an operation has a matching qualification receipt. No receipt or GPU package ships in the minimal CPU installation. Discovery does not enable optional AI features or download assets.

The **gpu-render** extra adds only wgpu for rendering. The optional **gpu** extra supplies [wgpu-py](https://wgpu-py.readthedocs.io/en/latest/backends.html) for rendering and [ONNX Runtime's native WebGPU provider](https://onnxruntime.ai/docs/execution-providers/WebGPU-ExecutionProvider.html) for inference. These use different native implementations. This implementation uses bounded CPU readback, canonical preprocessing and upload between them.

## Placement and ownership

| Stage | Owner |
|---|---|
| Downloads, extraction, validation, STEP tessellation, mesh parsing, viewer conversion | Existing CPU/storage and disposable workers |
| Dimensions, volume, topology, exact fingerprints | Existing authoritative CPU algorithms |
| Projection, face culling, shading, triangle rasterization | Qualified WebGPU renderer, otherwise canonical CPU |
| Eligible visual position welding and smooth normals | Portable WGSL with bounded local flat-normal recovery |
| Resampling, premultiplied-alpha rounding and vignette | Canary-checked portable WGSL, otherwise canonical CPU |
| Bounds and camera framing | Portable WGSL reductions over cached geometry |
| Lossless image encoding | CPU in the owning Job under host/CPU credit, or existing combined worker |
| Image, text, point and sparse ONNX inference | Qualified native WebGPU graph, otherwise CPU worker |
| Caption generation | Existing remote API |

A descriptor lock elects one host-local broker per data root. API processes, workers and disposable parsers use its Unix socket. The runtime/compute directory must belong to the service UID with mode 0700; its socket is 0600. Share this local directory between API and worker containers on the same host and UID. Do not place this coordination on remote storage or share it between hosts.

The broker owns execution only. It accepts bounded JSON and validated immutable array frames, never Python mesh objects or file parsing requests. Existing Jobs recover unfinished work after broker/client death. Existing source, generation and attempt fences decide whether results may publish.

Geometry buffers are keyed by content, array descriptors and preparation recipe, and uploaded once for all cameras. Up to eight compatible ready render frames share a completion/readback fence. Packing counts cameras across requests and checks device/readback budgets: two four-view requests can share a batch, while a third waits for the next one. The scheduler coalesces independently admitted background work for at most 10 ms from the original ready-work window; interactive requests dispatch immediately and aged background work retains service. No parser is started to fill a batch, and singleton qualification never certifies cross-Artifact batching.

Scheduling priority is explicitly propagated across disposable-worker exec. Thumbnail/derivative work in the mesh worker also enters a background render scope, allowing its prepared frames to coalesce while the Job and native admission keep their original priority. Direct interactive rendering retains interactive compute dispatch. A ContextVar default alone cannot preserve the parent's priority across exec.

Members are revalidated individually before execution. An expired or cancelled member receives its own result without invalidating live members; the shared watchdog accommodates the remaining live deadlines. Each output keeps its original deadline, cancellation and publication fences. Retired decoded geometry is released before its handler can return queue credits. Final admission can split an over-budget batch into individually admitted requests. See the [ready-model batching diagnosis and coverage matrix](testing/compute-model-batching.md) for preparation corrections and the [complete final 1000-source validation](testing/compute-final-gpu-1000.md). The final run preserves all expected outcomes but does not establish a throughput improvement.

A 32 MiB host cache retains final RGBA frames by full validated input and recipe, avoiding repeated projection/shading/postprocessing. The shared GPU ledger pins active geometries and can keep several models resident; readback slots and attachments are pooled; vertex shaders fetch compact indexed geometry directly. Shaders are compiled once per renderer lifetime. Canary-checked GPU postprocessing preserves the shared pixel processing semantics without allocating the CPU rasterizer's screen/depth/shading arrays. Only visual work warms the owner before entering a disposable parser; metadata-only work avoids that startup. Persistent ownership amortizes startup across a 40-model import even when the durable derivative lane is sequential. Shader pipelines, compact geometry buffers and framebuffers stay warm. Submissions complete at bounded frame readback without per-face-chunk synchronization. Idle geometry is evicted before weights and expires after 60 seconds. Model sessions expire after 300 seconds. Active allocations are pinned in one shared device ledger. Separate host reservations use the existing inference RAM partition; parser credits remain held until their processes and buffers die.

Inference batches contain at most eight compatible inputs. Dynamic exports receive tensor batches and batch canaries; fixed exports retain singleton execution. Text batches use length buckets and masks. Interactive tickets have no intentional accumulation delay; background tickets coalesce already-ready work for at most 10 ms. Aged background work receives service under sustained query traffic.

Cached visual thumbnails gather across Artifacts without parsing another mesh. Similarity gathers at most two adjacent components of the same source, parses once, and publishes/checkpoints each component independently. Derivative FIFO, configured concurrency and single-backfill ownership remain in existing Job lanes.

Allocation recovery evicts idle entries and makes one bounded inference split. Valid work returns to CPU under its original deadline. Device failure restarts the broker with a 30-second host-local cooldown. Cancellation and malformed input remain distinct. A batch never grants one member another member's publication authority.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| VAULT_COMPUTE_MODE | auto | auto or cpu |
| VAULT_COMPUTE_BACKEND | auto | auto, vulkan or opengl; auto selects GL on WSL, otherwise runtime defaults |
| VAULT_COMPUTE_RENDER_POLICY | qualified | qualified requires measured receipts; preview enables bounded render evaluation for human review |
| VAULT_COMPUTE_ADAPTER | unset | Exact physical adapter name or diagnostic identity |
| VAULT_COMPUTE_MEMORY_MB | 1024 | Shared estimated device ceiling, 256–65536 MiB |
| VAULT_COMPUTE_BATCH_WAIT_MS | 10 | Background accumulation ceiling, 0–10 ms |

Portable runtimes do not expose dependable free-device-memory information on every deployment. The conservative fallback is 1 GiB; the memory setting is an operator override. Accounting covers a runtime reserve, model/workspace estimates, compact geometry, preparation scratch, upload staging and framebuffer bounds. It is admission control, not protection against allocations by other applications.

A staging handshake reserves aggregate queue bytes before clients upload request bodies. Capacity refusal preserves the healthy owner and routes that item back to CPU; it does not start the infrastructure cooldown. Host staging is charged separately from GPU bytes and broker RSS is monitored. Stop all deployment processes before changing native memory policies, as for existing native quotas.

Administrator-only **GET /api/v1/system/compute** reports hardware/runtime identity, readiness reasons, resident model identities, device/host reservations, queue bytes, queue/execution/cold-start times, rendering transfers, batches, cache hits and fallback counts. Render counters include completion batches, submitted frames, shaded-output cache hits and geometry upload bytes. `render_interactive_requests` and `render_background_requests` count admitted render requests. `render_batch_model_counts` and `render_batch_frame_counts` contain eight exact bins for one through eight model inputs or frames per completed readback batch. Model inputs count requests with uncached output, not unique geometry keys; completely cached outputs add no batch. The histograms distinguish multiple cameras of one input from multiple inputs in a batch. ONNX is initialized only by an inference request; thumbnail startup never probes it. ONNX transfers remain included in execution time rather than reported as separately measured telemetry.

## Linux, Docker and WSL

Source install:

~~~sh
cd backend
uv sync --frozen --extra full --extra gpu-render
~~~

Optional local image:

~~~sh
docker build --build-arg PRINTSTASH_VARIANT=gpu-render -t printstash-api:gpu-render backend
~~~

Ordinary full/lite images remain CPU-compatible. GPU image variants add Vulkan and EGL/OpenGL Mesa packages; host drivers and device exposure are still required.

Linux AMD/Intel deployments need a hardware Vulkan driver and access to the appropriate /dev/dri/renderD* node through the service UID's groups. NVIDIA containers need the supported [NVIDIA Container Toolkit configuration](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html), including graphics capabilities for Vulkan. A successful nvidia-smi command proves neither Vulkan nor native WebGPU readiness.

In WSL, the application remains in Linux containers and uses the host bridge:

~~~sh
docker run --rm --device /dev/dxg \
  --mount type=bind,src=/usr/lib/wsl,dst=/usr/lib/wsl,readonly \
  -e LD_LIBRARY_PATH=/usr/lib/wsl/lib \
  -e GALLIUM_DRIVER=d3d12 -e MESA_LOADER_DRIVER_OVERRIDE=d3d12 \
  -e XDG_RUNTIME_DIR=/tmp \
  -e VAULT_COMPUTE_RENDER_POLICY=preview \
  -v printstash-gpu-review:/data -p 8001:8000 printstash-api:gpu-render
~~~

This example creates a separate evaluation vault. Add the same device/library
exposure to the broker-owning service in a split deployment. `auto` selects
wgpu's GL backend when `/dev/dxg` exists. Mesa translates GL to D3D12; no NVIDIA
API, Windows helper, patched Dozen driver or relaxed adapter flag is used.
OpenGL is a best-effort wgpu backend, so test the actual deployment.

Unknown GL adapters are accepted only when their exact D3D12 renderer matches
one unambiguous DXCore `IsHardware` record. Vendor/device IDs and the host driver
version become part of qualification identity. Missing DXCore, software adapters
and ambiguous matches remain CPU. A real compute canary is required; native
Windows/macOS broker deployments remain outside this POSIX implementation.

The RTX 5060 has executed rendering through this route in Docker/WSL. This is
not qualification for AMD/Intel, all drivers, performance or visual acceptance.
See [render evaluation](testing/compute-render-wsl.md) and the earlier
[runtime investigation](testing/compute-rtx5060-investigation.md).

## Qualification and rollout

Receipts are a JSON array in the private data root's runtime/compute/qualification.json, validated by backend/app/runtime/compute/qualification.py. Missing, malformed, stale or nonmatching receipts keep the operation on CPU. Runtime identity includes package versions and implementation sources; device identity includes driver/backend identity.

Each receipt records device/runtime identities from diagnostics, operation (render/dense/sparse), exact recipe, measured work-unit interval, maximum qualified batch items (defaults to one), peak device and host bytes, passed quality gate, accepted-Artifact-to-durable-publication speedup at least 1.5, and mixed-load interactive p95 ratio at most 1.1.

Rendering recipes include portable and canonical recipe, resolution, view count and matte flag. Units are faces multiplied by cameras. Inference recipes use verified model manifest identity; units are batch inputs. Receipt ranges must cover measured workload classes only, including shape and transfer-size envelopes. Do not extrapolate toy or draw-only timings to ingestion.

`preview` is an explicit operator evaluation policy, reported as `reason: preview`.
It never creates a receipt or claims the performance gate passed. It preserves
hardware validation, resource admission, deadlines, cancellation and CPU fallback.
Visual acceptance belongs to the operator; comparisons and pixel differences are
review evidence, not an assistant's approval.

Keep raw cold-start, warm-singleton, bulk-import and concurrent-search measurements. Include preparation, queueing, both runtime transfers, encoding and durable publication. Retain the historical frozen-corpus measurements (foreground mask at alpha ≥128, RGBA differences, protected-part visibility and repeatability) as diagnostics. Record operator visual acceptance separately. The historical ModernGL pilot evidence is unchanged.

Software gates:

~~~sh
cd backend
./scripts/test.sh full -q
uv run pyright
uv run ruff check app/ tests/
uv run pytest packages/printstash-core/tests/mesh -q
~~~

Explicit runtime diagnostic (requires the GPU extra and an exposed adapter; missing prerequisites fail):

~~~sh
uv run --extra gpu-render pytest tests/integration/modules/media/webgpu_render_diagnostic.py tests/integration/modules/media/gpu_visual_normals_diagnostic.py -q
~~~

The diagnostic allows software adapters for development and must never generate an automatic-selection receipt. Physical qualification must separately cover Linux, Docker and WSL configurations on representative NVIDIA, AMD and Intel systems. Keep injected allocation errors, killed processes and actual hardware OOM/device-loss evidence separate.

Thumbnail recipe 15 and canonical raster recipe qualified-portable-v6 version output behavior independently of device. Device choice never changes embedding-space identity; changed models, preprocessing or precision still require the existing generation workflow.


## Render transfer and postprocessing lifecycle

The parser sends a bounded descriptor containing the geometry digest, dimensions
and cameras. The owner acknowledges either a cached geometry reference or an
admitted binary upload. It validates array bounds and digest before scheduling.
The host geometry cache is capped at 128 MiB, pins active entries and expires
idle entries after 60 seconds. Input upload queues and output queues each have a separate 192 MiB ceiling.
Both are included in the owner host reservation; they are not GPU allocations.
The device residency ledger still covers geometry, shaders' runtime allowance,
framebuffers, readback and compute postprocessing buffers together.

Canonical Lanczos resampling, premultiplied-alpha rounding and the vignette run
as portable WGSL compute passes before readback. Coefficients and vignette data
are uploaded once per resolution. A byte-exact startup canary checks partial
transparency and both supersampling factors against the pinned CPU algorithm.
If this canary fails, the parser's existing reservation covers CPU finalization
of raw pixels; it does not occupy the GPU owner's CPU thread. Image encoding
remains in the existing CPU path. No inference runtime is started by rendering.

Only the used readback span is copied, even if the retained pool was grown for
a larger batch. At 640×480 the GPU-finalized path downloads 1,228,800 bytes per
frame instead of 4,915,200 supersampled bytes. Geometry, compiled shaders and
framebuffers stay warm across independently admitted Artifacts. The existing
eight-frame ceiling and 10 ms ready-work wait remain in force.

Administrator diagnostics include render_postprocess_gpu, geometry_input_bytes,
geometry_input_cache_hits and geometry_input_cache_bytes. Input transfer counters
measure accepted uploads; device upload counters measure GPU transfers separately.
Runtime identity includes the postprocessing implementation, so older qualification
receipts cannot silently qualify it. Postprocessing preserves the pinned pixel
semantics; the later preparation and encoding changes are versioned below.
See [correction evidence and coverage](testing/compute-render-performance.md).


## Compact geometry and visual preparation

The native-triangle recipe keeps positions, indices, normal identities and smooth
normals in VRAM: `12V + 12F + 4V + 12K + 32` bytes per cached geometry. It avoids
the previous 96-byte-per-face object array and 96-byte-per-face projected buffer.
Vertex shaders fetch the three indexed vertices, transform them and shade native
triangles with hardware depth/coverage. A GPU reduction computes bounds once per
upload; camera framing then operates on those bounds. Default framing uses the
rotated AABB corners and may differ from the previous exact projected-vertex fit.
Source coordinates remain centered before float32 conversion to retain precision
for models far from the world origin.

Eligible models with at least 20,000 faces send only relative float32 positions
and uint32 triangle indices, tagged `gpu-visual-v2`. The GPU welds exact float32
positions through a hash table with 64 bounded probes. Three passes weld,
accumulate angle-weighted normals and normalize them without CPU sorting or a
normal readback. Core integer atomics use a 2**16 visual accumulation scale and
at most 4096 contributions per welded position, bounding integer overflow without
optional vendor features. Congested, overfull, cancelling or degenerate local
neighborhoods use flat normals. They do not trigger a whole-model CPU retry.
Both encoded and equivalent canonical input sizes remain below 48 MiB.

Small inputs retain CPU smooth-normal preparation. The older `gpu-angle-csr-v1`
wire representation remains accepted, with its bounded canonical retry behavior.
Invalid indices and nonfinite or unsafe coordinates remain input errors. Device
failure, unsupported work and failed startup canaries preserve CPU fallback.
None of these visual approximations change topology, volume, exact fingerprints,
source bytes or STEP tessellation.

Already-admitted compatible models share mapped upload slabs bounded to 64 MiB
per slab, a uniform arena, command encoder, submission and completion readback.
Normal scratch and upload slabs stay pinned through completion or a failure
fence. The shared ledger charges all slabs together; the slab ceiling alone is
not a batch memory limit. Mapping the upload buffers avoids allocating another
implicit queue-write staging copy for each slab. Host arena/conversion copies
are admitted separately. These estimates still cannot bound other applications'
GPU memory or opaque driver allocations.

A one-time real shader canary covers visual welding and a degenerate face before
the persistent pipeline accepts production work. Renderer statistics distinguish
uploaded geometries, GPU normals, legacy CPU-normal retries, local fallback
vertices, reduced vertices, upload calls and command submissions. Transfer and
normal-preparation seconds are host intervals, not GPU hardware busy time.
The legacy `geometry_packing_min_faces` field is `null`: compact rendering has
no packing threshold. The 20,000-face threshold above applies only to smooth-normal
preparation; smaller models still use compact native-triangle rendering.

Thumbnail recipe 15, raster recipe `qualified-portable-v6`, visual recipe
`canonical-views-media-thumbnail-v6` and compute recipe `webgpu-native-raster-v4`
version the changed framing, shading and encoding behavior. Exact geometry
recipe 12 and fingerprint/point semantics remain unchanged. Changed visual-input
semantics use the existing generation workflow; choosing a device alone never
changes embedding identity. No AI feature or model download is enabled.

## Thumbnail custody across parser and GPU

A ready, authorized GPU renderer can receive a bounded geometry packet after the
isolated parser exits cleanly. The Job retains source and publication authority;
its synchronous codec runs under a dedicated process-shared completion slot and
an explicit host-memory reservation. This permits one encoder in addition to the
configured parser slots, so an already rendered frame cannot queue behind a
parser that requests all native RAM. Codec entry requires retained host custody
and released parser credit; no additional executor or child process is created. A successful handoff uses one parser and no
encoder child. Only GPU refusal invokes an isolated CPU renderer from that same
packet, without reparsing the source. Cancellation and the original deadline do
not trigger retries.

Readiness checks the already-running physical device, initialized GPU postprocessor
and available render capability without launching a broker from the parser.
Qualified routing reuses the existing exact device/runtime/recipe receipt and
checks actual face counts again before staging.

Cold or unavailable renderers, unqualified face counts, insufficient host
headroom and optional fingerprint requests retain the combined guarded worker.
The first combined render can initialize the GPU postprocessor. Native parser
estimates, durable Job concurrency, FIFO/backfill priority, exact measurements
and publication fences remain unchanged.

Host staging is capped at four slots and 384 MiB, and no more than one quarter of
known physical headroom after existing geometry and inference partitions. Packet,
readback and codec workspace are charged through their real lifetimes; error
tracebacks cannot retain buffers after that credit is released. This is application
admission, not an OS reservation. The broker keeps its separate existing budget.
The packet also remains charged to prepared-source bytes and disk capacity.
Source, host and native descriptors remain held through guardian cleanup; unknown
physical limits retain the combined path.

`thumbnail_handoff_complete` reports packet read, dispatch, codec queue, codec and
CPU recovery intervals. `thumbnail_handoff_supervision` associates the handoff
with parser and fallback execution IDs. Codec admissions use the separate
`thumbnail-codec` pool; native admissions describe parser/fallback work. Neither
admission count is a child-process count. These
phase and supervision intervals overlap; they are not additive wall-clock costs.

The older implementation that spawned a finalizer per prepared source was retired
after the first 200-model candidate regressed 28.5%. Its selective 54-finalizer
variant did not establish an end-to-end gain either. The subsequent combined-worker
version reduced broker execution but regressed by about 5% under matched
continuous input. The new direct-codec handoff
has a different lifecycle and requires its own matched measurement; no throughput
improvement is inferred from the lifecycle change. See the
[active handoff contracts and coverage](testing/compute-thumbnail-handoff-v2.md),
[historical experiment](testing/compute-thumbnail-handoff.md),
[matched pipeline evidence](testing/compute-native-pipeline-200.md) and
[visual preparation validation](testing/compute-visual-preparation.md).


## Thumbnail encoding

Preview profile 4 encodes WebP with `lossless=False`, `exact=False`, `method=2`,
`quality=85` and `alpha_quality=100`. Visible RGB is compressed with loss; alpha
remains exact. RGB under fully transparent pixels is not retained. CPU and GPU
paths share this encoder, including normalized embedded previews. PNG and the
six analytic view pixels retain their prior behavior. Mesh thumbnail recipe 15,
G-code thumbnail recipe 3 and the updated visual recipe schedule replacements
through existing derivative/index generation workflows.

Publication validates dimensions, decoded alpha, nonempty content and a
transparent border, then preserves already encoded generated WebP. It does not
reframe or encode that output again. `VAULT_MODEL_THUMBNAIL_WIDTH=512` requests
512×384 previews; the default remains 640×480. Both sizes retain 2× supersampling
and full geometry within the existing render budget. This reduces pixel work,
not source geometry or authoritative measurement accuracy.

The [codec and 1000-source comparison](testing/compute-codec-throughput.md)
records the codec-only pilot and complete-pipeline measurements separately.
The [previous lossless experiment](testing/compute-encoding.md) remains historical
evidence rather than a claim about the current encoder.

### Control-path lifetime

Compute source/dependency identity is attested once per interpreter, keyed by PID; policy settings are still hashed on each connection. Deploy code/dependency changes by restarting every API, worker and broker together. Editing source files underneath a running interpreter is not a supported upgrade. Explicit supervisor warm-up gets up to 30 seconds before parser limits are installed; ordinary readiness probes retain the five-second cap and work requests keep their original deadline.

Admission continues to prove every ticket’s live descriptor lock on every scan. Only decoding is cached, using inode, size, mtime and ctime; changed/dead receipts invalidate that cache. OBJ preflight retains 8 MiB/4096-byte limits, with a 250 ms thread-CPU allowance and one-second wall watchdog checked every 128 lines plus EOF. Unknown or unvalidated sources still claim the whole pool. The container compiles application bytecode at build time while retaining disposable guarded parser processes.
