# GPU rendering qualification

Status: accepted; ModernGL pilot completed; this candidate is not adopted.

GPU execution can reduce triangle raster work, but complete visual cost includes source loading and preparation, context creation, shader compilation, bounded transfers, completed drawing, readback, final processing and encoding. A reused GPU context is compared against reused CPU geometry preparation. Neither a driver being installed nor a faster draw call qualifies an application backend.

The isolated candidate uses ModernGL 5.12.0 and glcontext 3.0.0, with NumPy 2.5.3, Pillow 12.3.0 and Python 3.14.8. ModernGL and glcontext live in the optional `gpu-pilot` extra and are absent from default application requirements. The framework-neutral `DeferredRasteriser` protocol publishes its image once after every visible-face chunk and any silhouette recovery. Common preparation, camera, normals, culling, image processing and encoding remain in their existing owners. The default callable rasterizer remains byte-identical to the frozen CPU controls; the derivative recipe stays unchanged.

Before measuring candidate images, the quality gate was fixed to exact foreground coverage at alpha >= 128 and maximum per-channel RGBA error <= 8/255. This allows bounded colour arithmetic differences while requiring the model silhouette to remain visible. Independent protected-part checks and repeatability within the GPU backend remain separate requirements. GPU adoption requires accepted quality plus at least 1.5 times improvement of the complete visual flow for a declared source family and hardware, with equivalent bounded failure recovery.

Process-tree RSS, logical requested buffer/attachment storage and device-wide memory snapshots are reported independently. An RSS or address-space ceiling does not bound VRAM. Baseline/end device snapshots include other GPU users and cannot establish a context's peak allocation. A released context test cannot establish recovery from a physical device reset; injected native errors cannot establish real device-OOM recovery.

Primary API references: [ModernGL contexts](https://moderngl.readthedocs.io/en/latest/reference/context.html), [framebuffers](https://moderngl.readthedocs.io/en/latest/reference/framebuffer.html), [glcontext](https://github.com/moderngl/glcontext).

## Decision

Keep the application on the existing CPU renderer. The optional candidate has
not met the declared quality and complete-flow cost gates across the intended
STL/3MF family. Do not enable it automatically based on GPU availability.

Context reuse and one final readback remain research tooling. Adoption requires
accepted independent image controls, an explicit hardware and driver support
policy, complete-flow cost qualification, bounded failure recovery and an
operator-controlled rollback path.
