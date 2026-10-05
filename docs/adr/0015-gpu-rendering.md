# GPU rendering qualification

Status: accepted; measured pilot completed; no application GPU adoption.

GPU execution can reduce triangle raster work, but complete visual cost includes source loading and preparation, context creation, shader compilation, bounded transfers, completed drawing, readback, final processing and encoding. A reused GPU context is compared against reused CPU geometry preparation. Neither a driver being installed nor a faster draw call qualifies an application backend.

The isolated candidate uses ModernGL 5.12.0 and glcontext 3.0.0, with NumPy 2.5.3, Pillow 12.3.0 and Python 3.14.8. ModernGL and glcontext live in the optional `gpu-pilot` extra and are absent from default application requirements. The framework-neutral `DeferredRasteriser` protocol publishes its image once after every visible-face chunk and any silhouette recovery. Common preparation, camera, normals, culling, image processing and encoding remain in their existing owners. The default callable rasterizer remains byte-identical to the frozen CPU controls; the derivative recipe stays unchanged.

Before measuring candidate images, the quality gate was fixed to exact foreground coverage at alpha >= 128 and maximum per-channel RGBA error <= 8/255. This allows bounded colour arithmetic differences while requiring the model silhouette to remain visible. Independent protected-part checks and repeatability within the GPU backend remain separate requirements. GPU adoption requires accepted quality plus at least 1.5 times improvement of the complete visual flow for a declared source family and hardware, with equivalent bounded failure recovery.

Current hardware feasibility was measured inside the existing guarded native supervisor, on a nonexclusive WSL host. Explicit EGL/D3D12 selection reported Microsoft Corporation / D3D12 (NVIDIA GeForce RTX 5060), OpenGL 4.6, Mesa 25.2.8. The device-wide NVIDIA query identified driver 610.88 and 8,151 MiB device memory. A 512 MiB process allowance refused context creation with `eglInitialize failed (0x3001)`; 1 GiB and 3 GiB allowances created real contexts in approximately 996 ms and 811 ms. These are individual feasibility observations, not latency percentiles. Default EGL at 1 GiB exited without a reply; its native cause is unqualified.

Process-tree RSS, logical requested buffer/attachment storage and device-wide memory snapshots are reported independently. An RSS or address-space ceiling does not bound VRAM. Baseline/end device snapshots include other GPU users and cannot establish a context's peak allocation. A released context test cannot establish recovery from a physical device reset; injected native errors cannot establish real device-OOM recovery.

The reproducible command, coverage matrix and actual qualification results are recorded in [the pilot testing document](../testing/gpu-render-pilot.md). No private model bytes, filenames or installation paths belong in public evidence.

Primary API references: [ModernGL contexts](https://moderngl.readthedocs.io/en/latest/reference/context.html), [framebuffers](https://moderngl.readthedocs.io/en/latest/reference/framebuffer.html), [glcontext](https://github.com/moderngl/glcontext).

## Decision

Keep the application on the existing CPU renderer. The optional candidate demonstrates useful acceleration on a simple solid, but it does not satisfy the declared visual gate across the intended STL/3MF family. Corrected complete-source estimates also remain below the 1.5× cost gate: 1.418991× with fresh contexts and 1.498073× with reused contexts, thirty samples per method. Canonical 640×480 controls reject a sheared placement and a curved sphere on colour; three private 3MF controls also have one foreground-mask mismatch each. Cold and reused contexts reproduce the same failures. Do not reinterpret these as wholesale missing models, silently widen the tolerance, or automatically enable this backend merely because a GPU is present.

Context reuse and one final readback are retained in the research tool. Adoption would require resolving the image discrepancies against independent controls, qualifying an explicit hardware/driver allocation strategy, and measuring recovery from real device reset/OOM. Current successful cancellation and deadline containment prove process and credit recovery only. The existing CPU path, derivative recipe and installation requirements remain unchanged. A later candidate can reuse the narrow deferred-frame protocol and canonical encoder without duplicating geometry preparation or application orchestration.
