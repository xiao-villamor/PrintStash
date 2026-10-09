# PRI-16 delivery status against the original plan

This is the original 33-row acceptance matrix, not a claim that the smaller
adapter test matrix completes the implementation. A row is missing if any part
of its required outcome remains unverified. Existing CPU coverage does not
substitute for a production GPU path.

Research/tooling PR: #428. Browser implementation PR: #429. Production server
integration remains gated by the original acceptance requirements and has no PR.

## Current physical evidence

Revised server candidate 649cd8f2 passes 60/60 prepared cube/torus comparisons
on Windows RTX 5060/Vulkan with identical masks, stable output and zero RGBA
difference. The historical candidate failed quality. The revised candidate
also retires cyclic native wrappers at session cleanup, addressing the
repeated-context memory exhaustion observed in the intermediate candidate.

These results are not Linux/Docker evidence, full STL/3MF quality, complete-flow
speedup, or physical Job failure containment. Intel hardware is not available
in this environment. WSL exposes software Vulkan; the available Docker daemon
has no configured GPU runtime. The native Windows NVIDIA device is usable.

The browser comparison on 42bb9668 retains 120 attempts (119 completed).
Neither control demonstrates interaction-frame improvement. The readiness proxy
is slower with WebGPU; it is not an actual first-visible-frame measurement.
WebGPU remains experimental. Individual native browser conformance covers 33
cases, including camera operations and capture recovery on both backends.

## Original acceptance matrix

| # | Behaviour | Status | Evidence or remaining requirement |
| --- | --- | --- | --- |
| 1 | preserves CPU-only operation | ✅ | Existing CPU-only application suites pass; optional GPU package stays outside default installation. |
| 2 | renders through the GPU adapter | ❌ | Physical prepared cube/torus controls pass; full admitted STL/retained-3MF integration is pending. |
| 3 | produces canonical visual views | ❌ | Pilot supports multiview; full frozen-corpus physical framing qualification is pending. |
| 4 | preserves reference image quality | ❌ | 60 revised physical control comparisons are exact; full corpus/protected-part qualification is pending. |
| 5 | selects adapters by capabilities | ✅ | Vendor-neutral adapter-type/limit selection is implemented; eligibility contract tests pass. |
| 6 | rejects software acceleration claims | ✅ | CPU/unknown adapter tests and no-device diagnostic refuse physical qualification. |
| 7 | recovers from GPU setup failure | ❌ | Typed research adapter refusals exist; production Job CPU retry is not integrated. |
| 8 | enforces allocation boundaries | ✅ | Native allocation preflight and separate retained-geometry ceilings are tested. |
| 9 | releases partial allocations | ✅ | Every attachment/buffer allocation failure point releases prior resources; session wrappers are retired. |
| 10 | recovers after device loss | ❌ | Simulated adapter failure recovery passes; physical production Job recovery is pending. |
| 11 | contains cancellation | ❌ | Production GPU worker cancellation/publication integration is pending. |
| 12 | contains deadline expiry | ❌ | Production GPU deadline/resource-lease integration is pending. |
| 13 | rejects incomplete readback | ✅ | Truncated, nonfinite and invalid face-ID data never publish pixels. |
| 14 | preserves source refusal | ❌ | Existing CPU source policies remain intact; integrated GPU selection path is pending. |
| 15 | bounds concurrent GPU work | ❌ | Process-shared GPU admission and per-device leases are pending. |
| 16 | preserves replay safety | ❌ | GPU-enabled derivative Job replay/publication integration is pending. |
| 17 | invalidates changed recipes | ❌ | No production GPU recipe exists; gated adoption must implement recipe reconciliation. |
| 18 | publishes through supported storage | ❌ | Existing CPU local/S3 flows pass; GPU production publication is pending. |
| 19 | preserves artifact access restrictions | ❌ | Existing access restrictions remain unchanged; GPU-assisted full flow is pending. |
| 20 | completes GPU-assisted ingestion | ❌ | Production GPU ingestion is not enabled or implemented. |
| 21 | initializes the selected browser renderer | ✅ | Native browser WebGPU initialization, usable controls and captures pass. |
| 22 | retains preview on unsupported contexts | ✅ | Unavailable-adapter recovery and physical localhost/HTTPS/LAN compatibility checks pass. |
| 23 | recovers the browser renderer | ✅ | Device destruction recovers a fresh WebGL canvas with the camera preserved. |
| 24 | preserves display modes | ❌ | Solid/x-ray/wireframe export passes; complete baseline visual-parity corpus is pending. |
| 25 | preserves camera controls | ✅ | Native fit/reset/zoom/pan/orbit checks pass for WebGL and WebGPU. |
| 26 | preserves comparison alignment | ❌ | Synchronized comparison cameras pass; full overlay visual-parity qualification is pending. |
| 27 | exports screenshots correctly | ✅ | Native PNG dimensions and independent pixel orientation checks pass at scales 1–3 for both renderers. |
| 28 | restores rendering after capture failure | ✅ | Injected native readback refusal restores state and permits the next capture on both renderers. |
| 29 | discards stale loading results | ✅ | Worker cancellation, stale source handling and late renderer initialization cleanup are covered. |
| 30 | releases viewer resources | ❌ | Individual worker/geometry/device disposal is covered; repeated-cycle steady-state accounting is pending. |
| 31 | preserves G-code preview behavior | ✅ | Existing real BGCODE preview flow passed; G-code renderer implementation remains unchanged. |
| 32 | starts without container GPU access | ❌ | Research image refuses absent hardware correctly; GPU-enabled application CPU recovery is pending. |
| 33 | reports complete benchmark costs | ❌ | Raw samples/failures/phases are retained; upload-to-thumbnail and full browser phase instrumentation remain incomplete. |

## Remaining implementation gates

- Freeze and run the full redistributable STL/3MF workload corpus, including
  independent protected-part assertions, on both required Linux/Docker devices.
- Require the unchanged 1.5x median complete-flow gate for each proposed
  acceleration family, separately for thumbnail and thumbnail-plus-six-view.
- Only after those gates pass, implement production backend settings, shared
  device admission, disposable worker integration, one deadline-bounded CPU
  retry, cancellation containment, recipe reconciliation and atomic publication.
- Complete browser performance and visual-parity qualification. Keep WebGL as
  default and WebGPU/auto experimental until acceptance is proven.

Neither green CI nor the two exact synthetic image controls changes these gates.
