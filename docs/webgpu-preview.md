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

Chrome 154.0.8037.98 on the available Windows RTX 5060 also passed the native
capture control through a non-fallback NVIDIA Blackwell adapter. That development
check is not performance qualification. The complete public STL viewer also
exported 1280×960 PNGs on localhost and HTTPS through WebGPU, and on ordinary
private-IP HTTP through WebGL. HTTPS used a local self-signed test certificate
accepted only by that browser test context; no system trust settings changed. Physical performance qualification remains outstanding. Roll back at any time
through Settings → GPU → WebGL compatibility. Server thumbnail acceleration has
its own qualification gate and is not enabled by the browser preference.

## Coverage matrix

Statuses describe executed software contracts; pending hardware/visual
qualification is explicit. Parameterized variants share one observable behavior.

| Behaviour (test name) | Category | Input | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| enforces the production repository graph | Edge | Viewer and Worker imports | No runtime or type dependency cycles | Repo | ✅ |
| retains the renderer choice | Happy | webgl/auto/webgpu | Stored preference is read back | Unit | ✅ |
| keeps legacy preferences on the compatibility renderer | Edge | Old settings | WebGL selected, quality retained | Unit | ✅ |
| refuses an unsupported mesh renderer preference | Error | Invalid value | WebGL selected | Unit | ✅ |
| opens GPU settings from section navigation | Happy | GPU navigation | Renderer selector displayed | Frontend | ✅ |
| saves the renderer choice from the GPU section | Happy | Select WebGPU | Browser preference persisted | Frontend | ✅ |
| retains explicit WebGL preference on capable devices | Happy | Capable secure context | WebGL selected | Unit | ✅ |
| uses WebGPU for capable secure contexts | Happy | auto/webgpu | WebGPU selected | Unit | ✅ |
| recovers insecure LAN HTTP | Edge | Insecure context | Compatibility selection with cause | Unit | ✅ |
| recovers absent browser support | Edge | No navigator GPU | Compatibility selection with cause | Unit | ✅ |
| normalizes WebGL bottom-up pixels | Happy | Two known rows | Top-down pixels | Unit | ✅ |
| preserves WebGPU top-down pixels | Happy | Two known rows | Orientation unchanged | Unit | ✅ |
| removes WebGPU row alignment padding | Edge | Padded readback | Packed pixels | Unit | ✅ |
| refuses truncated readback | Error | Incomplete bytes | No capture returned | Unit | ✅ |
| transfers source bytes to its worker | Happy | Source response | Transferred geometry returned, worker closed | Unit | ✅ |
| terminates parsing on cancellation | Error | Abort while parsing | Worker terminated | Unit | ✅ |
| releases the worker after native failure | Error | Worker error | Failure surfaced, worker closed | Unit | ✅ |
| refuses failed downloads before creating a worker | Error | HTTP 403 | Download refusal | Unit | ✅ |
| discards obsolete parsing results after source replacement | Edge | Out-of-order replies | Current geometry only | Frontend | ✅ |
| releases loaded geometry on unmount | Edge | Viewer closes | Geometry disposed | Frontend | ✅ |
| exports a correctly oriented WebGL screenshot | Happy | Native API, scales 1/2/3 | Dimensions and red/blue control rows | Browser integration | ✅ |
| exports a correctly oriented WebGPU screenshot | Happy | Native API, scale 2 | Dimensions and red/blue control rows | Browser integration | ✅ |
| restores rendering after asynchronous capture failure | Error | Readback rejects | Next native capture succeeds | Browser integration | ✅ |
| contains a capture during disposal | Edge | Pending native readback | No capture after disposal | Browser integration | ✅ |
| recovers the current camera after device loss | Error | Destroy native device | WebGL camera retained, screenshot usable | Browser integration | ✅ |
| recovers from an unavailable WebGPU adapter | Error | No adapter | Compatibility viewer usable | Browser integration | ✅ |
| releases initialization completed after unmount | Edge | Delayed native device | Device destroyed, no Canvas resurrected | Browser integration | ✅ |
| releases the device when the viewer closes | Edge | Native device initialized | Device destroyed | Browser integration | ✅ |
| shares concurrent viewer preparation | Happy | Real application and 3MF | Durable STL ready, viewer controls enabled | E2E | ✅ |
| retains a memory refusal while keeping the original downloadable | Error | Refused 3MF | Refusal survives reload; original accessible | E2E | ✅ |
| exports the selected display mode | Happy | solid/xray/wireframe on WebGPU | Usable 1280×960 PNG | Browser integration | ✅ |
| fits the camera after zooming | Happy | Zoom then fit | Original fitted pose restored | Browser integration | ✅ |
| preserves the original through real layer inspection | Happy | Actual BGCODE upload and viewer | Layer inspection and original download succeed | E2E | ✅ |
| parses ASCII STL through its native worker | Happy | ASCII control | Ready geometry retains expected dimensions | Browser integration | ✅ |
| synchronizes comparison camera movement | Happy | Two matching meshes | Peer pixels follow primary zoom | Browser integration | ✅ |
| retires controls while changing renderer preference | Edge | Delayed native initialization | Readiness false until replacement is ready | Browser integration | ✅ |
| validates physical interaction/first-frame gates | Performance | Frozen physical corpus | Required thresholds | Qualification | ❌ pending |
| qualifies baseline visual parity | Happy | Fixed scene/browser matrix | Reference visual parity | Qualification | ❌ pending |
| selects the compatible backend for actual origins | Edge | localhost, self-signed HTTPS, private-IP HTTP | Native WebGPU or WebGL; usable PNG | Manual native-browser | ✅ |

Primary references: [Three.js WebGPURenderer](https://threejs.org/manual/pages/webgpurenderer),
[Fiber asynchronous renderer factory](https://r3f.docs.pmnd.rs/api/canvas),
[WebGPU secure contexts](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/gpu),
[Chromium SwiftShader](https://chromium.googlesource.com/chromium/src/+/main/docs/gpu/swiftshader.md).
