# PRI-16 mesh preview rendering

Settings → GPU (/settings?section=gpu) controls mesh rendering in the current
browser. WebGL remains the default, including for preferences saved by older
versions. Automatic and WebGPU are experimental choices. HTTPS or localhost is
required for WebGPU; insecure LAN HTTP and browsers without WebGPU select WebGL.

The public STL viewer props and control callbacks are unchanged. Durable viewer
STL preparation still happens through the existing API. A disposable Worker
parses its downloaded STL and transfers geometry buffers to the viewer. Source
replacement aborts downloading/parsing and discards obsolete results.

The mesh-only adapter owns native initialization, capability limits, asynchronous
readback and cleanup. It lazy-loads Three.js WebGPURenderer. An unavailable adapter,
initialization failure or lost device remounts one fresh compatibility Canvas,
retaining the camera and loaded source. Recovery is bounded to one remount.
The Canvas exposes the effective backend and fallback reason as diagnostic data
attributes. Renderer disposal is shared with Fiber and waits for pending capture.

Screenshots preserve output color space, normalize native pixel orientation and
WebGPU row padding, enforce texture limits and restore the scene background and
render target before asynchronous readback. Failed captures retain a usable view.
The existing G-code viewer keeps its WebGL renderer.

## Dependencies

The compatible set selected at implementation start is Three.js 0.186.1,
@types/three 0.186.0, Fiber 9.8.1 and Drei 10.7.9. The package-manager override
enforces a single Three.js version. No vendor-specific browser code is used.

## Verification and rollout

Run focused software conformance with **cd frontend && pnpm test:mesh**.
These browser integration tests use Chromium's native APIs through SwiftShader;
they do **not** qualify physical acceleration. They run separately from application
E2E because some inject native-boundary failures. A Vulkan-enabled software
surface is required for WebGPU presentation in headless Chromium.

The real-backend tests/e2e-real/viewer-stl.spec.ts additionally cover durable
preparation and retained source refusals. Test fixtures do not enter the production
bundle. WebGPU code remains lazy-loaded with the mesh viewer.

Before promoting Automatic to the default, freeze a standard and large-mesh
corpus, hashes, viewport, quality, browser/driver versions and tested commit.
Retain at least 30 raw observations including failures. Measure download, parsing,
first visible frame, interaction frame times and screenshot completion separately.
Compare the current WebGL baseline against WebGPU on the same physical device.
Require at least 20% lower p95 interaction frame time on every declared large case,
with median and p95 first-visible-frame time within 10% of baseline.
Software timings, dev-server timings and warm-context-only results cannot supply
this evidence. Validate HTTPS, localhost and ordinary LAN HTTP with real browsers.

Roll back at any time through Settings → GPU → WebGL compatibility.
Server thumbnail acceleration has its own qualification gate and is not enabled
by the browser preference.
