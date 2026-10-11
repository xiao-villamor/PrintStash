# GPU rendering qualification

Status: accepted; ModernGL pilot completed; this candidate is not adopted.

GPU execution can reduce triangle raster work, but complete visual cost includes source loading and preparation, context creation, shader compilation, bounded transfers, completed drawing, readback, final processing and encoding. A reused GPU context is compared against reused CPU geometry preparation. Neither a driver being installed nor a faster draw call qualifies an application backend.

The isolated candidate uses ModernGL 5.12.0 and glcontext 3.0.0, with NumPy 2.5.3, Pillow 12.3.0 and Python 3.14.8. ModernGL and glcontext live in the optional `gpu-pilot` extra and are absent from default application requirements. The framework-neutral `DeferredRasteriser` protocol publishes its image once after every visible-face chunk and any silhouette recovery. Common preparation, camera, normals, culling, image processing and encoding remain in their existing owners. The default callable rasterizer remains byte-identical to the frozen CPU controls; the derivative recipe stays unchanged.

Before measuring candidate images, the quality gate was fixed to exact foreground coverage at alpha >= 128 and maximum per-channel RGBA error <= 8/255. This allows bounded colour arithmetic differences while requiring the model silhouette to remain visible. Independent protected-part checks and repeatability within the GPU backend remain separate requirements. GPU adoption requires accepted quality plus at least 1.5 times improvement of the complete visual flow for a declared source family and hardware, with equivalent bounded failure recovery.

Current hardware feasibility was measured inside the existing guarded native supervisor, on a nonexclusive WSL host. Explicit EGL/D3D12 selection reported Microsoft Corporation / D3D12 (NVIDIA GeForce RTX 5060), OpenGL 4.6, Mesa 25.2.8. The device-wide NVIDIA query identified driver 610.88 and 8,151 MiB device memory. A 512 MiB process allowance refused context creation with `eglInitialize failed (0x3001)`; 1 GiB and 3 GiB allowances created real contexts in approximately 996 ms and 811 ms. These are individual feasibility observations, not latency percentiles. Default EGL at 1 GiB exited without a reply; its native cause is unqualified.

Process-tree RSS, logical requested buffer/attachment storage and device-wide memory snapshots are reported independently. An RSS or address-space ceiling does not bound VRAM. Baseline/end device snapshots include other GPU users and cannot establish a context's peak allocation. A released context test cannot establish recovery from a physical device reset; injected native errors cannot establish real device-OOM recovery.

The reproducible command, coverage matrix and actual qualification results are recorded in [the pilot testing document](../testing/gpu-render-pilot.md). No private model bytes, filenames or installation paths belong in public evidence.

Primary API references: [ModernGL contexts](https://moderngl.readthedocs.io/en/latest/reference/context.html), [framebuffers](https://moderngl.readthedocs.io/en/latest/reference/framebuffer.html), [glcontext](https://github.com/moderngl/glcontext).

## Decision

Keep the application on the existing CPU renderer. The optional ModernGL candidate demonstrates useful acceleration on a simple solid, but it does not satisfy the declared visual gate across the intended STL/3MF family. Corrected complete-source estimates also remain below the 1.5× cost gate: 1.418991× with fresh contexts and 1.498073× with reused contexts, thirty samples per method. Canonical 640×480 controls reject a sheared placement and a curved sphere on colour; three private 3MF controls also have one foreground-mask mismatch each. Cold and reused contexts reproduce the same failures. Do not reinterpret these as wholesale missing models, silently widen the tolerance, or automatically enable this backend merely because a GPU is present.

Context reuse and one final readback are retained in the research tool. Adoption would require resolving the image discrepancies against independent controls, qualifying an explicit hardware/driver allocation strategy, and measuring recovery from real device reset/OOM. Current successful cancellation and deadline containment prove process and credit recovery only. The existing CPU path, derivative recipe and installation requirements remain unchanged. A later candidate can reuse the narrow deferred-frame protocol and canonical encoder without duplicating geometry preparation or application orchestration.

This decision qualifies the ModernGL candidate only. A native WebGPU adapter using wgpu-py has not been qualified for rendering quality, complete-flow performance or failure recovery; its candidate implementation is guarded by operation-specific qualification receipts. API availability alone cannot supply those measurements. See the [official wgpu-py guide](https://wgpu-py.readthedocs.io/en/stable/guide.html) for the separate native API.


## Portable compute lifecycle

The [portable compute implementation](../compute.md) uses a host-local broker for persistent wgpu and ONNX WebGPU ownership, bounded immutable array transfers, shared device admission and separately reserved host memory. Source parsing stays disposable. Geometry uploads serve all cameras and may stay cached; canonical framing remains in core through PreparedRasteriser. Canonical postprocessing can execute in portable GPU compute passes after a byte-exact canary, retaining the CPU implementation as fallback.

Automatic selection requires hardware and implementation-specific evidence. Missing evidence is ordinary CPU operation. Thumbnail recipe 14 and qualified-portable-v5 version the candidate independently of hardware. Original ModernGL measurements remain unchanged and cannot qualify this backend.


## Render-only WSL follow-up (2026-10-10)

The maintained Mesa D3D12 OpenGL path works with wgpu-native in Docker/WSL.
It does not require ONNX readiness or the experimental Vulkan/Dozen route.
DXCore hardware evidence resolves unknown GL adapter classification without
vendor-specific rendering. The optional render image includes EGL/GL drivers.

The persistent broker supports bounded ready-frame batches, shared readback
fences, retained geometry/shader resources and a bounded shaded-output cache.
Preview admission is explicit and retains all resource/publication fences;
it produces no qualification receipt. Visual acceptance belongs to the operator.
Historical pilot numbers remain historical evidence; see
[current render evaluation](../testing/compute-render-wsl.md).


## Postprocessing and transport correction

The WebGPU path now retains pixels on-device through separable Lanczos, alpha
conversion and vignette. It downloads final-sized RGBA and encodes in the caller.
The canary requires exact canonical bytes; it does not replace operator review
or relax the rendering gate. Binary validated-geometry references remove repeated
base64 serialization and uploads. Pooled readback copies only completed frames.
The controlled render-only comparison and its limits are in
[the performance record](../testing/compute-render-performance.md).

## Ready-model batching follow-up (2026-10-11)

The earlier 1000-model GPU run recorded 967 frames in 967 singleton completion
batches. Disposable exec lost the parent's priority ContextVar, leaving backfill
rendering eligible for the interactive default. The supervisor/bootstrap now
propagate scheduling priority explicitly, and asynchronous mesh-worker outputs
use a background render scope while retaining their Job/native admission priority.
The broker packs already-ready camera frames within the existing eight-frame,
10 ms and resource limits, with individual cancellation/deadline handling.
New exact-bin model-input/frame histograms expose whether real batching occurs;
fully cached outputs add no batch. Model-input counts do not deduplicate geometry.

Bounded preparation and dispatch corrections accompany this work. The
[diagnosis, aggregate baseline and 53-behaviour matrix](../testing/compute-model-batching.md)
separate complete-pipeline, overlapping admission-wait and broker timings.
The new comparison of the same 1000 cached models, further GPU preparation
candidate and operator visual acceptance remain pending. These corrections do
not alter the historical ModernGL decision or create a qualification receipt.


## Portable geometry preparation follow-up

Large eligible render inputs may now defer smooth normals to the persistent
owner, using guarded portable WGSL accumulation and indexed packing. A tagged
immutable descriptor, separate host/device admission and per-model status at the
shared frame fence preserve CPU recovery and independently publishable outputs.
The selected 20,000-face crossover comes from measured warm preparation/render
work; it does not supply whole-ingestion or mixed-load qualification. Changed
normal arithmetic and corrected canonical WebP encoding bump thumbnail/visual
recipes. The operator retains visual acceptance, and automatic admission still
requires current qualification evidence. See [compute preparation](../compute.md#indexed-packing-and-deferred-normals).


## Native triangles and GPU visual preparation (2026-10-11)

Operator-authorized visual evaluation no longer requires images identical to the
CPU renderer. The historical ModernGL gate and failed evidence remain intact;
they do not certify this new recipe. The portable owner now draws indexed native
triangles, reduces bounds on the GPU and frames rotated bounding-box corners.
Eligible visual position welding and normals move to bounded portable compute
passes, with local flat-normal fallback instead of rejecting the complete model.
These changes version thumbnail14/rasterv5/visualv5/compute-native-v4 together.

Compatible models share mapped upload slabs and one submission/completion fence.
Persistent compact geometry replaces expanded per-face object/projected arrays.
All active scratch, staging, cached geometry and framebuffer allocations remain
in the shared ledger. Production automatic selection still requires matching
performance evidence; preview images remain subject to operator acceptance.
The [new validation matrix](../testing/compute-visual-preparation.md) separates
real-device execution, injected faults and complete-pipeline measurements.
