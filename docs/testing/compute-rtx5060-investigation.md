# RTX 5060 qualification investigation — 2026-10-09

This investigation establishes physical execution and identifies blockers. It does not qualify automatic acceleration or claim an ingestion speedup.

## Environment and physical execution

- GPU: NVIDIA GeForce RTX 5060, vendor 0x10de, device 0x2d05, Windows driver 610.88.
- Windows Vulkan: physical discrete adapter, Vulkan 1.4.341.
- WSL: Ubuntu 24.04, WSL 3.0.1, kernel 6.18.40.1.
- Runtimes: wgpu 0.32.0, ONNX Runtime 1.30.0, native WebGPU plugin 0.4.0.
- Native Windows: real WGSL storage-buffer arithmetic returned 42. Native ONNX WebGPU MatMul returned [[7, 10], [15, 22]] with CPU fallback disabled, matching the reference.
- Stock Ubuntu Mesa 25.2.8 has no Dozen ICD. Default enumeration remains software-only.
- Built upstream Mesa 26.2.4 Dozen in an isolated Ubuntu 24.04 container. With /dev/dxg and read-only /usr/lib/wsl exposed, unprivileged vulkaninfo identifies Microsoft Direct3D12 (NVIDIA GeForce RTX 5060), Vulkan 1.2.354. No system driver packages were replaced.

## Why installing Dozen is insufficient

Upstream Dozen reports fullDrawIndexUint32=false and conformanceVersion=0.0.0.0. Default wgpu refuses the nonconformant adapter. ONNX's Dawn implementation rejects it with:

~~~text
Vulkan fullDrawIndexUint32 feature required.
Failed to get a WebGPU adapter: No supported adapters
~~~

This is an upstream feature mismatch, not a missing PrintStash qualification receipt. Dozen's source declares the feature false; Dawn requires it even for its compatibility feature level. Changing a receipt or pretending the driver supports that feature would not establish correctness.

Sources: [Mesa source](https://chromium.googlesource.com/external/gitlab.freedesktop.org/mesa/mesa/+/4e4a1d181be6f7bb1a6e1b6edf813b1466487828/src/microsoft/vulkan/dzn_device.c), [Dawn adapter validation](https://dawn.googlesource.com/dawn/+/c294f092edae33d036dee4b8640f5c3560fc5da2/src/dawn/native/vulkan/PhysicalDeviceVk.cpp), [official Mesa archives](https://archive.mesa3d.org/).

## Rendering investigation

For diagnostics only, wgpu's AllowUnderlyingNoncompliantAdapter instance flag allows its supported subset to execute on the physical GPU. PrintStash production discovery does not set this flag.

The physical canary succeeded. The original three renderer diagnostics passed, but expanded 224-pixel orthographic tests exposed shared-edge opacity cracks with RGBA differences of 51. Directly recomputing the third barycentric weight was insufficient: different arithmetic order across adjacent triangles still produced cracks.

The corrected shader evaluates each shared edge in a canonical endpoint order and reverses its sign for the other triangle. It evaluates all three weights directly. This prevents floating-point contraction from giving inconsistent shared-edge coverage. The new regression failed before the correction and passes afterward. The portable rendering qualification recipe is now webgpu-canonical-raster-v2; runtime source fingerprints also invalidate older evidence.

Validation after correction:

| Behaviour | Result |
|---|---|
| Physical RTX 5060 renderer diagnostics, including six orthographic shared-edge views | ✅ 4 passed |
| Same diagnostics on llvmpipe | ✅ 4 passed; software evidence only |
| CPU dispatch, geometry protocol and qualification contracts | ✅ 16 passed |
| Twelve existing visual controls: one thumbnail and six matte views each | ✅ 82/84 views meet the existing gate |
| sphere-5120 thumbnail | ❌ Maximum RGBA difference 228; foreground mask equal |
| real-benchy thumbnail | ❌ Maximum RGBA difference 22; foreground mask equal |
| Repeatability of the 84 views | ✅ All repeated output bytes identical |
| Full protected-part corpus and workload coverage | ❌ Not complete; many-instances reference run was interrupted, grid-200000 excluded from the bounded follow-up |
| Accepted Artifact to durable publication, mixed-load latency, hardware fault recovery | ❌ Not measured |

The 84-view comparison uses existing deterministic source builders and the unchanged compare_pixels policy in gpu_render_measurement. It measures prepared rendering only, not ingestion throughput. No receipt was generated, no quality threshold changed, and automatic routing remains CPU for this deployment.

## Remaining deployment work

Native Windows can execute both runtimes on this GPU. Using that from the current WSL deployment requires a Windows compute worker with authenticated local transport, managed lifecycle and resource accounting. That transport is not implemented by the POSIX broker.

A native Linux deployment with a conformant hardware Vulkan driver is the other route to testing both runtimes without Dozen. Either path must still complete real model compatibility, the full visual corpus, durable ingestion performance, mixed-load latency and recovery qualification. AMD and Intel hardware remain untested.
