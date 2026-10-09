# Optional portable compute

PrintStash selects execution below existing ingestion, derivative and inference interfaces. Default **auto** placement remains CPU unless an operation has a matching qualification receipt. No receipt or GPU package ships in the minimal CPU installation. Discovery does not enable optional AI features or download assets.

The optional **gpu** extra supplies [wgpu-py](https://wgpu-py.readthedocs.io/en/latest/backends.html) for rendering and [ONNX Runtime's native WebGPU provider](https://onnxruntime.ai/docs/execution-providers/WebGPU-ExecutionProvider.html) for inference. These use different native implementations. This implementation uses bounded CPU readback, canonical preprocessing and upload between them.

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

Geometry buffers are keyed by content, array descriptors and preparation recipe, and uploaded once for all cameras. Shader pipelines, projected buffers and framebuffers stay warm. Submissions complete at bounded frame readback without per-face-chunk synchronization. Idle geometry is evicted before weights and expires after 60 seconds. Model sessions expire after 300 seconds. Active allocations are pinned in one shared device ledger. Separate host reservations use the existing inference RAM partition; parser credits remain held until their processes and buffers die.

Inference batches contain at most eight compatible inputs. Dynamic exports receive tensor batches and batch canaries; fixed exports retain singleton execution. Text batches use length buckets and masks. Interactive tickets have no intentional accumulation delay; background tickets coalesce already-ready work for at most 10 ms. Aged background work receives service under sustained query traffic.

Cached visual thumbnails gather across Artifacts without parsing another mesh. Similarity gathers at most two adjacent components of the same source, parses once, and publishes/checkpoints each component independently. Derivative FIFO, configured concurrency and single-backfill ownership remain in existing Job lanes.

Allocation recovery evicts idle entries and makes one bounded inference split. Valid work returns to CPU under its original deadline. Device failure restarts the broker with a 30-second host-local cooldown. Cancellation and malformed input remain distinct. A batch never grants one member another member's publication authority.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| VAULT_COMPUTE_MODE | auto | auto or cpu |
| VAULT_COMPUTE_ADAPTER | unset | Exact physical adapter name or diagnostic identity |
| VAULT_COMPUTE_MEMORY_MB | 1024 | Shared estimated device ceiling, 256–65536 MiB |
| VAULT_COMPUTE_BATCH_WAIT_MS | 10 | Background accumulation ceiling, 0–10 ms |

Portable runtimes do not expose dependable free-device-memory information on every deployment. The conservative fallback is 1 GiB; the memory setting is an operator override. Accounting covers a runtime reserve, model/workspace estimates, geometry and framebuffer/projection bounds. It is admission control, not protection against allocations by other applications.

Aggregate queued frames are bounded before their bodies are read. Host staging is charged separately from GPU bytes and broker RSS is monitored. Stop all deployment processes before changing native memory policies, as for existing native quotas.

Administrator-only **GET /api/v1/system/compute** reports hardware/runtime identity, readiness reasons, resident model identities, device/host reservations, queue bytes, queue/execution/cold-start times, rendering transfers, batches, cache hits and fallback counts. ONNX transfers remain included in execution time rather than reported as separately measured telemetry.

## Linux, Docker and WSL

Source install:

~~~sh
cd backend
uv sync --frozen --extra full --extra gpu
~~~

Optional local image:

~~~sh
docker build --build-arg PRINTSTASH_VARIANT=gpu -t printstash-api:gpu backend
~~~

Ordinary full/lite images remain CPU-compatible. The GPU image adds Vulkan loader/Mesa packages; host drivers and device exposure are still required.

Linux AMD/Intel deployments need a hardware Vulkan driver and access to the appropriate /dev/dri/renderD* node through the service UID's groups. NVIDIA containers need the supported [NVIDIA Container Toolkit configuration](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html), including graphics capabilities for Vulkan. A successful nvidia-smi command proves neither Vulkan nor native WebGPU readiness.

In WSL, qualify inside the actual distribution/container. Windows drivers or working CUDA utilities do not prove a hardware Vulkan/Dozen path. CPU, llvmpipe and unknown adapters are refused as acceleration. Both runtimes must identify the same physical vendor/device and run real canaries. Native Windows/macOS broker deployments are outside this POSIX implementation.

**No NVIDIA, AMD or Intel deployment combination is fully qualified by this change.** Follow-up testing reached the RTX 5060 with both runtimes natively on Windows and with experimental wgpu rendering through a locally built Dozen driver in WSL. ONNX WebGPU still rejects Dozen's missing fullDrawIndexUint32 feature. Some thumbnail comparisons also remain outside the unchanged visual gate. See the [physical RTX 5060 investigation](testing/compute-rtx5060-investigation.md) for measured results and deployment blockers.

## Qualification and rollout

Receipts are a JSON array in the private data root's runtime/compute/qualification.json, validated by backend/app/runtime/compute/qualification.py. Missing, malformed, stale or nonmatching receipts keep the operation on CPU. Runtime identity includes package versions and implementation sources; device identity includes driver/backend identity.

Each receipt records device/runtime identities from diagnostics, operation (render/dense/sparse), exact recipe, measured work-unit interval, peak device and host bytes, passed quality gate, accepted-Artifact-to-durable-publication speedup at least 1.5, and mixed-load interactive p95 ratio at most 1.1.

Rendering recipes include portable and canonical recipe, resolution, view count and matte flag. Units are faces multiplied by cameras. Inference recipes use verified model manifest identity; units are batch inputs. Receipt ranges must cover measured workload classes only, including shape and transfer-size envelopes. Do not extrapolate toy or draw-only timings to ingestion.

Keep raw cold-start, warm-singleton, bulk-import and concurrent-search measurements. Include preparation, queueing, both runtime transfers, encoding and durable publication. Re-run the frozen rendering corpus: exact foreground mask at alpha ≥128, maximum RGBA difference ≤8, protected-part visibility and repeatability. The ModernGL pilot remains unqualified; its tolerances are unchanged.

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
uv run --extra gpu pytest tests/integration/modules/media/webgpu_render_diagnostic.py -q
~~~

The diagnostic allows software adapters for development and must never generate an automatic-selection receipt. Physical qualification must separately cover Linux, Docker and WSL configurations on representative NVIDIA, AMD and Intel systems. Keep injected allocation errors, killed processes and actual hardware OOM/device-loss evidence separate.

Thumbnail recipe 12 and canonical raster recipe qualified-portable-v3 version output behavior independently of device. Device choice never changes embedding-space identity; changed models, preprocessing or precision still require the existing generation workflow.
