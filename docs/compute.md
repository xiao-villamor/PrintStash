# Optional portable compute

PrintStash selects execution below existing ingestion, derivative and inference interfaces. Default **auto** placement remains CPU unless an operation has a matching qualification receipt. No receipt or GPU package ships in the minimal CPU installation. Discovery does not enable optional AI features or download assets.

The **gpu-render** extra adds only wgpu for rendering. The optional **gpu** extra supplies [wgpu-py](https://wgpu-py.readthedocs.io/en/latest/backends.html) for rendering and [ONNX Runtime's native WebGPU provider](https://onnxruntime.ai/docs/execution-providers/WebGPU-ExecutionProvider.html) for inference. These use different native implementations. This implementation uses bounded CPU readback, canonical preprocessing and upload between them.

## Placement and ownership

| Stage | Owner |
|---|---|
| Downloads, extraction, validation, STEP tessellation, mesh parsing, viewer conversion | Existing CPU/storage and disposable workers |
| Dimensions, volume, topology, exact fingerprints | Existing authoritative CPU algorithms |
| Projection, face culling, shading, triangle rasterization | Qualified WebGPU renderer, otherwise canonical CPU |
| Camera framing, smooth-normal preparation, postprocessing, encoding | Canonical CPU |
| Image, text, point and sparse ONNX inference | Qualified native WebGPU graph, otherwise CPU worker |
| Caption generation | Existing remote API |

A descriptor lock elects one host-local broker per data root. API processes, workers and disposable parsers use its Unix socket. The runtime/compute directory must belong to the service UID with mode 0700; its socket is 0600. Share this local directory between API and worker containers on the same host and UID. Do not place this coordination on remote storage or share it between hosts.

The broker owns execution only. It accepts bounded JSON and validated immutable array frames, never Python mesh objects or file parsing requests. Existing Jobs recover unfinished work after broker/client death. Existing source, generation and attempt fences decide whether results may publish.

Geometry buffers are keyed by content, array descriptors and preparation recipe, and uploaded once for all cameras. Up to eight compatible ready render frames share a completion/readback fence. The scheduler can coalesce independently admitted background Artifacts for at most 10 ms; interactive requests dispatch immediately and aged background work retains service. A batch that cannot fit is re-admitted as individual requests with the original deadlines. No parser is started to fill a batch, and singleton qualification never certifies cross-Artifact batching.

A 32 MiB host cache retains final RGBA frames by full validated input and recipe, avoiding repeated projection/shading/postprocessing. The shared GPU ledger pins active geometries and can keep several models resident; readback slots, projected buffers and attachments are pooled. Shaders are compiled once per renderer lifetime. Canonical postprocessing is shared with CPU, without allocating the CPU rasterizer's screen/depth/shading arrays in the GPU path. Only visual work warms the owner before entering a disposable parser; metadata-only work avoids that startup. Persistent ownership amortizes startup across a 40-model import even when the durable derivative lane is sequential. Shader pipelines, projected buffers and framebuffers stay warm. Submissions complete at bounded frame readback without per-face-chunk synchronization. Idle geometry is evicted before weights and expires after 60 seconds. Model sessions expire after 300 seconds. Active allocations are pinned in one shared device ledger. Separate host reservations use the existing inference RAM partition; parser credits remain held until their processes and buffers die.

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

Portable runtimes do not expose dependable free-device-memory information on every deployment. The conservative fallback is 1 GiB; the memory setting is an operator override. Accounting covers a runtime reserve, model/workspace estimates, geometry and framebuffer/projection bounds. It is admission control, not protection against allocations by other applications.

A staging handshake reserves aggregate queue bytes before clients upload request bodies. Capacity refusal preserves the healthy owner and routes that item back to CPU; it does not start the infrastructure cooldown. Host staging is charged separately from GPU bytes and broker RSS is monitored. Stop all deployment processes before changing native memory policies, as for existing native quotas.

Administrator-only **GET /api/v1/system/compute** reports hardware/runtime identity, readiness reasons, resident model identities, device/host reservations, queue bytes, queue/execution/cold-start times, rendering transfers, batches, cache hits and fallback counts. Render counters include completion batches, submitted frames, shaded-output cache hits and geometry upload bytes. ONNX is initialized only by an inference request; thumbnail startup never probes it. ONNX transfers remain included in execution time rather than reported as separately measured telemetry.

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
uv run --extra gpu-render pytest tests/integration/modules/media/webgpu_render_diagnostic.py -q
~~~

The diagnostic allows software adapters for development and must never generate an automatic-selection receipt. Physical qualification must separately cover Linux, Docker and WSL configurations on representative NVIDIA, AMD and Intel systems. Keep injected allocation errors, killed processes and actual hardware OOM/device-loss evidence separate.

Thumbnail recipe 12 and canonical raster recipe qualified-portable-v3 version output behavior independently of device. Device choice never changes embedding-space identity; changed models, preprocessing or precision still require the existing generation workflow.
