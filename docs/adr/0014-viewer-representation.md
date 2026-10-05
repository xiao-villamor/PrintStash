# ADR-0014: Qualify viewer representations before replacing STL

Status: Accepted — retain STL; do not automatically adopt the evaluated GLB/LOD candidates.

## Decision

Keep original Artifacts and the current on-demand STL derivative authoritative for the viewer. Retain bounded, isolated representation and simplification pilots with explicit quality failures. No application runtime dependency, derivative recipe, UI, metadata, similarity or original-download contract changes in this decision.

Indexed buffers and actual GPU instancing show useful savings in controlled cells. General replacement fails the evaluated correctness/resource contract, and automatic LOD lacks sufficient independent real-model and browser qualification. A future visual Derivative must have its own recipe and preserve the original geometry for metadata and similarity.

## Browser evidence

Measured on 2026-10-05, Linux x64/WSL, Node 24.19.0, Three 0.186.1, Playwright 1.63.0, Vite 8.3.2, Chromium 153.0.8010.12. WebGL renderer: ANGLE/Vulkan SwiftShader Subzero; this is software rendering. Each observation uses a fresh browser context, disabled HTTP response caching, loopback transfer and alternating representation order. Existing camera helpers, material and lighting are used; latest standalone loaders differ from the product dependency (Three 0.182.0/three-stdlib). This is not a full application navigation or network-distance experiment. The WSL host was not exclusively reserved.

The n100 report precedes the collector’s typed WebGL dispatch, atomic report persistence and Zod input-decoder corrections. Full typed-array upload accounting remains unchanged; focused safety tests qualify the final collector. The series is evidence for the recorded source/candidate bytes and measurement scope, not a latency gate for a later production adapter.

All 800 loads succeeded; no observation or outlier was excluded. Times include fetch, parsing, preparation and `gl.finish()` completion of the first draw. They exclude the later geometry/pixel audit and compositor presentation. p95 is nearest rank (95th of 100). Component medians do not sum to the total median.

| Cell / representation | n | First draw median / p95 ms | Min–max ms | Sample SD ms | Body bytes | Retained geometry + input bytes | Live WebGL buffer bytes | Draw calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| many-instances / STL | 100 | 81.40 / 90.70 | 76.60–115.60 | 6.03 | 1,228,884 | 2,998,356 | 1,769,472 | 1 |
| many-instances / GLB | 100 | 152.00 / 162.80 | 146.30–190.90 | 6.06 | 242,664 | 243,384 | 720 | 2,048 |
| sphere-5120 / STL | 100 | 66.60 / 71.10 | 62.20–78.70 | 2.41 | 256,084 | 624,724 | 368,640 | 1 |
| sphere-5120 / GLB | 100 | 64.90 / 72.40 | 60.60–86.20 | 3.62 | 431,204 | 861,284 | 430,080 | 1 |
| grid-200000 / STL | 100 | 199.75 / 213.50 | 188.90–227.60 | 6.52 | 10,000,084 | 24,400,084 | 14,400,000 | 1 |
| grid-200000 / GLB | 100 | 104.40 / 112.80 | 97.80–120.40 | 3.77 | 4,817,704 | 9,634,528 | 4,816,824 | 1 |
| many-instances-gpu-instanced / STL | 100 | 81.70 / 93.80 | 75.50–106.80 | 5.83 | 1,228,884 | 2,998,356 | 1,769,472 | 1 |
| many-instances-gpu-instanced / GLB | 100 | 72.15 / 89.60 | 66.70–778.60 | 70.81 | 26,280 | 158,072 | 131,792 | 1 |

| Cell / representation | Download median ms | Parse median ms | Prepare median ms | First draw GPU median ms |
|---|---:|---:|---:|---:|
| many-instances / STL | 12.50 | 5.50 | 37.30 | 26.15 |
| many-instances / GLB | 7.00 | 67.95 | 42.55 | 34.45 |
| sphere-5120 / STL | 7.00 | 8.00 | 31.30 | 20.60 |
| sphere-5120 / GLB | 7.90 | 5.20 | 33.00 | 18.75 |
| grid-200000 / STL | 65.75 | 15.50 | 45.65 | 72.50 |
| grid-200000 / GLB | 33.90 | 10.80 | 32.80 | 26.70 |
| many-instances-gpu-instanced / STL | 12.50 | 5.60 | 37.40 | 26.25 |
| many-instances-gpu-instanced / GLB | 5.80 | 9.70 | 36.25 | 21.20 |

Retained ArrayBuffers exclude parser temporaries, JS objects and peak heap. Buffer instrumentation measures live `bufferData` storage; total physical VRAM is unavailable. Shared GLB nodes retain tiny geometry buffers but still make 2,048 draws. The maintained glTF-Transform 4.5.1 [`instance`](https://gltf-transform.dev/modules/functions/functions/instance) transform (`min: 5`, EXT_mesh_gpu_instancing registered) produces 26,280 bytes and one actual draw for the positive-translation control. Both instanced and noninstanced candidates submit 24,576 triangles. The 778.6 ms instanced outlier is retained; modest latency savings must not be generalized from its median alone.

Trial-zero quality passes for all four cells: oriented geometry and normals within 1e-5 mm/1e-6, bounds within 1e-5 mm, nonempty equal foreground masks, RGBA within one component level. Sphere coordinate error is 5.96e-8 mm and one RGBA component differs by one; raw SHA equality is diagnostic rather than a numerical-equivalence criterion. Timing repetition does not imply repeated full pixel/geometry qualification.

### Correctness and admission refusals

A valid 3MF cube shear `x += 0.5*y` has canonical STL bounds `[[0,0,0],[30,20,20]]`. The prototype GLB serializes that affine matrix, but glTF requires node matrices decomposable into TRS; it cannot encode arbitrary shear as a node. The [glTF transformation contract](https://github.com/KhronosGroup/glTF/blob/main/specification/2.0/Specification.adoc#transformations) explains the requirement. This is an invalid candidate encoding, not a Three loader defect. Actual browser bounds differ by 5.513554 mm, all 12 triangles are unmatched and 13,170 foreground pixels change. The harness rejects it, retaining both loader observations. Properly baking unsupported affine placements into owned geometry is a possible future implementation, with winding/normal/precision and resource costs separately qualified.

The public-domain 225,706-facet Benchy source (SHA-256 `6ab57f1c3f8e86bc3cbd302c6fa6270acf06277c6335454e922419c25d42e97e`) is refused under the unchanged admitted `GeometryWork` envelope before and after float32 index-key optimization. Direct facet-equivalence testing passes outside that shared envelope; it does not establish admitted production feasibility. Do not loosen a resource contract to report a conversion win. A separately justified indexed-export profile or bounded partitioned export requires measured peak memory and actual supervised large-model success.

Single native observations for many-instances/sphere/grid respectively: parse 153.17/37.48/879.03 ms, indexed export 935.93/790.71/1955.93 ms, fsynced write 21.91/12.97/54.54 ms, canonical reference 399.56/47.01/1321.18 ms. Combined worker RSS and first-import cost cannot be assigned to one format. These are not n100 native or whole Artifact latency results.

## Simplification evidence

Fast-Simplification 0.2.0 is a development-only candidate evaluated with float64 points, int64 faces, aggression 3, preserve_border true and requested reduction 0.5/0.8. Invalid triangles remain explicit failed candidates; no sanitization or original fallback changes the result. The second candidate is maintained [Meshoptimizer](https://meshoptimizer.org/) 1.3.0 WASM: float32 positions, uint32 indices, `MeshoptSimplifier.simplify(indices, positions, 3, floor(faceCount*(1-reduction))*3, 0.05, ["LockBorder", "ErrorAbsolute"])` after `ready`. Both candidates are evaluated against the same immutable float64 analytic sources, with 512 finite surface samples plus used vertices, component topology, borders/loops and independent hole/crease/thickness/small-part probes.

| Family | Source faces | Fast candidate 50% / 80% requested | Meshoptimizer 50% / 80% requested |
|---|---:|---|---|
| sharp cube | 768 | degenerate / degenerate | 384 / 152; declared checks pass |
| torus hole | 1536 | 1440 / 1440; declared checks pass | 1244 / 1244; declared checks pass |
| open border | 640 | 320 / 256; declared checks pass | 320 / 128; declared checks pass |
| thin solid | 768 | 576 / 576; declared checks pass | 384 / 152; declared checks pass |
| tiny component | 384 | degenerate / degenerate | 192 / 76; declared checks pass |

The torus Meshoptimizer reduction is 19.01%, despite requesting 50% and 80%. Library error settings are not independent error proofs. Surface samples measure triangle interiors bidirectionally; they are not a Hausdorff bound. The Float32 border control retains 128 points, 128 mapped edges and two loops; measured movement 1.1914540600836425e-7 mm exactly matches packing error. Assessment allows that explicit precision error with a strict 1e-6 mm cap; the float64 default stays 1e-8 mm, and a 0.001 mm border shift fails. Source array digests and canonical geometry-t0-v1 fingerprint digests remain unchanged in every case. The ten Meshoptimizer passes support these analytic controls; they do not qualify arbitrary real models, materials, pixel silhouettes or production runtime mutation. Simplifier timing scopes differ and establish no speed ranking.

## Additional private real-model controls

A user-supplied archive contains 85 models (39 3MF, 46 STL). Nine size/format-spread controls were sampled; this is not qualification of all archive entries. Model names, paths and bytes are not published. Four admitted exports completed; three candidates explicitly refused float32-collapsed facets, one hit supervised native memory admission and one exceeded the canonical STL triangle budget. A separate bounded diagnostic worker confirmed the causes without relaxing source/resource policy.

The four successful exports were loaded as STL and GLB in real Chromium with the final collector: eight successful observations. Three pairs pass all declared geometry/pixel checks; the fourth has matching oriented geometry, bounds and foreground but eight differing RGBA components with maximum delta 10, exceeding the declared one-level gate. This is a measured pixel-policy refusal, not missing triangles. These are single quality observations, not latency statistics.

| Anonymous cell | Format | Original bytes | Triangles | STL body bytes | GLB body bytes | Declared quality |
|---|---|---:|---:|---:|---:|---|
| private-01 | 3mf | 43,514 | 3,532 | 176,684 | 199,964 | pass |
| private-03 | 3mf | 1,134,193 | 92,636 | 4,631,884 | 5,407,500 | RGBA component gate refusal |
| private-04 | 3mf | 8,063,202 | 329,264 | 16,463,284 | 27,422,652 | pass |
| private-05 | stl | 104,784 | 2,094 | 104,784 | 154,984 | pass |

These real cells include a 329,264-facet admitted 3MF candidate with full geometry/pixel parity. All four GLBs are larger than their STL references; format choice depends on actual resource/normal repetition and export cost, not extension alone. Native budgets are source/work-profile estimates rather than one universal facet limit. The analytic invalid-shear and refused real-STL cells remain independent adoption blockers.

Private browser report SHA-256: `14dbcb2a1cba0be379fef4aa4e7abb9d7111f3de0804fb2d2c374cefd1b88837`.

## Reconsideration conditions

- Encode the full admitted 3MF affine contract in valid GLB, including normals, reflection, precision and placement identity; reject unsupported visual outputs explicitly.
- Preserve actual instance sharing without a draw-call explosion, using a maintained instancing transform where its TRS contract applies.
- Demonstrate large real-model supervised export within a justified resource envelope and measure complete generation + publication + browser cost.
- Qualify LOD against independent real protected-feature, material and browser silhouette controls; expose attained counts and preserve measurements/similarity from reference geometry.
- Add a distinct durable visual Derivative recipe and additive viewer demand/status/selection contract only after those gates pass.

These conditions are scoped prerequisites for changing the default, not a deferred fix to the existing STL source contract. Retain CPU rendering and original downloads.

## Reproduction and evidence integrity

The locked isolated runtime also uses Zod 4.6.5 to decode external manifest input without coercion. The [qualification guide and complete coverage matrix](../testing/viewer-representation-pilot.md) document the checked-in pilots. Additional maintained-library probes are exploratory and outside normal CI; their exact APIs/settings above and below identify the evaluated alternatives. They are not runtime adapters.

For a disposable installed glTF-Transform 4.5.1 runtime, GPU instancing used `new NodeIO().registerExtensions([EXTMeshGPUInstancing])`, `io.read(sharedNodeGLB)`, `document.transform(instance({min: 5}))`, then `io.writeBinary(document)`. Source STL remains untouched. Only positive translation instances were evaluated through this transform.

The completed raw n100 browser report has SHA-256 `b7d6aff49e6418077110ff205361345e96e6fdb390d652dc1913f47e0bb45684`. Reported source/representation hashes below bind the figures to evaluated bytes; private absolute scratch paths are intentionally omitted.

| Cell | Source SHA-256 | Reference STL SHA-256 | Candidate GLB SHA-256 |
|---|---|---|---|
| many-instances | `583e5025aa40b6175066a68b5a40d0c1860e92e966e549b7be1ca16fc79527b1` | `f51c3836bd2c2bc3cec61d5e9e59ac4ba58db212045409732f9b838218d7bd17` | `93e93fbcbe261a72f348aa739d54c2b8f9515c2b0f1c0ea1d1787a3c70eaeea8` |
| sphere-5120 | `5f85e753fe73b8376179ab2fd073a7211a8d58f19adf7a621499988997205c89` | `08e6eabfbc4494401ce9e361f2aa6df490bcceb6bfa9548987c704d8bb5dddcb` | `f2b202570bd2855805ebb7109803b17b8021ccb610feee97534497e56e1fe76c` |
| grid-200000 | `89c622156316dce0a1c87a630990bcf327ccfd2d0e16daf1ec89e372bf881db3` | `3036a8d1d5bec32d73cb5f7e179bbeb8eacec094d799acea2bcd93367ea07de8` | `5ffe2a861d235b22e573684a723fadb447282cfccd9543c1530256d1fdcc30c4` |
| many-instances-gpu-instanced | `583e5025aa40b6175066a68b5a40d0c1860e92e966e549b7be1ca16fc79527b1` | `f51c3836bd2c2bc3cec61d5e9e59ac4ba58db212045409732f9b838218d7bd17` | `c86bdbf8f8fcd3909dcaf02b24d980ac0e1d84e4f12ee332ce4ff6d4146d448b` |
