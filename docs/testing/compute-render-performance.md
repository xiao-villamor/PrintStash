# Render performance corrections

The owner retains GPU rasterization and performs canonical Lanczos resampling,
premultiplied-alpha conversion and vignette before downloading final pixels.
A byte-exact startup canary gates this operation independently of rendering.
If unavailable, the reserved parsing caller performs CPU postprocessing.
PNG encoding remains on CPU. Human visual acceptance remains with the operator.

## Coverage plan

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Idle socket polling is immediate | Edge | Live peer with socket timeout | No timeout-sized delay | Contract | ✅ contract/runtime/compute/test_broker.py::TestPeerLiveness::test_checks_idle_peer_without_timeout_delay |
| 2 | Closed peers cancel work | Error | Closed client socket | Disconnect detected | Contract | ✅ contract/runtime/compute/test_broker.py::TestPeerLiveness::test_detects_closed_peer |
| 3 | Pending peer bytes preserved | Edge | Readable live socket | Byte remains available | Contract | ✅ contract/runtime/compute/test_broker.py::TestPeerLiveness::test_preserves_pending_peer_bytes |
| 4 | Geometry references avoid retransmission | Happy | Repeated geometry with changed camera | Second upload count zero | Contract | ✅ contract/runtime/compute/test_client.py::TestBinaryRender::test_reuses_geometry_across_cameras |
| 5 | Corrupt geometry rejected | Error | Incorrect digest or descriptor | Invalid input; owner remains usable | Contract | ✅ contract/runtime/compute/test_client.py::TestBinaryRender::test_isolates_malformed_geometry |
| 6 | Batch outputs retain association | Happy | Concurrent requests | Each response matches its input | Contract | ✅ contract/runtime/compute/test_client.py::TestBinaryRender::test_associates_concurrent_results |
| 7 | Input cache bounds memory | Edge | Multiple idle inputs | Eviction respects budget | Unit | ✅ unit/runtime/compute/test_geometry_cache.py::TestGeometryCache::test_evicts_idle_inputs |
| 8 | Active geometry remains pinned | Edge | All cache entries active | Capacity refusal preserves active entry | Unit | ✅ unit/runtime/compute/test_geometry_cache.py::TestGeometryCache::test_pins_active_inputs |
| 9 | Idle geometry expires | Edge | Expired entry | Next acquisition requires upload | Unit | ✅ unit/runtime/compute/test_geometry_cache.py::TestGeometryCache::test_expires_only_idle_entries |
| 10 | CPU postprocess fallback preserves bytes | Error | GPU postprocess unavailable | Canonical pixels at both sampling factors | Unit | ✅ unit/modules/media/test_compute_render.py::TestCpuPostprocessingFallback::test_finalizes_raw_pixels_in_the_caller |
| 11 | Malformed results rejected | Error | Invalid binary envelope | Normal CPU fallback | Unit | ✅ unit/modules/media/test_compute_render.py::TestCpuPostprocessingFallback::test_refuses_an_invalid_pixel_envelope |
| 12 | GPU postprocessing preserves pixels | Happy | Partial transparency and real meshes | Byte-identical canonical postprocessing | Integration (physical) | ✅ integration/modules/media/webgpu_render_diagnostic.py::TestGpuPostprocess::test_preserves_canonical_mesh_pixels |
| 13 | Pooled readback copies used span | Edge | Eight-frame batch then singleton | Exact singleton-sized readback | Integration (physical) | ✅ integration/modules/media/webgpu_render_diagnostic.py::TestGpuPostprocess::test_limits_readback_after_large_batch |
| 14 | Shader cache retains final outputs | Happy | Repeated finalized render | Same bytes without new submission | Integration (physical) | ✅ integration/modules/media/webgpu_render_diagnostic.py::TestGpuPostprocess::test_reuses_final_shaded_outputs |
| 15 | CPU deployment completes ingestion | Happy | No optional GPU runtime | Durable requested outputs published | E2E | ✅ e2e/test_ingest.py::TestComputePlacement::test_publishes_mesh_outputs_without_gpu_qualification |
| 16 | Finalized rendering scales across 40 inputs | Happy | Private corpus; persistent GPU owner | Timed complete outputs with bounded residency | Manual integration | ✅ Private controlled broker-fixed.json; 160 successful outputs, no fallback |
| 17 | Operator accepts visual quality | Happy | Private comparison gallery | Human acceptance recorded | Manual E2E | ⏭️ N/A — operator decision pending |
| 18 | Automatic performance qualification | Happy | Full ingestion with interactive mixed load | Original end-to-end and p95 gates | E2E | ⏭️ N/A — no receipt issued without measured evidence |
| 19 | Input capacity refusal preserves owner | Error | Full host input cache | Capacity response then healthy result | Contract | ✅ contract/runtime/compute/test_client.py::TestBinaryRender::test_recovers_from_input_capacity_refusal |
| 20 | Missing cached geometry uploads again | Edge | Cache evicted between requests | Successful result with new upload | Contract | ✅ contract/runtime/compute/test_client.py::TestBinaryRender::test_uploads_again_after_cache_eviction |
| 21 | Failed GPU canary yields raw pixels | Error | Injected canary refusal | CPU finalization produces same bytes | Integration (physical) | ✅ integration/modules/media/webgpu_render_diagnostic.py::TestGpuPostprocess::test_falls_back_after_postprocess_canary_refusal |
| 22 | Input queues remain inside host reservation | Edge | Renderer reaches available host capacity | Further allocation refused while queue/cache space retained | Unit | ✅ unit/runtime/compute/test_geometry_cache.py::TestHostReservation::test_preserves_queue_capacity_outside_renderer |
| 23 | Binary rendering accounts GPU workspace | Happy | Finalization-enabled request | Postprocess device buffers charged separately from host input | Unit | ✅ unit/runtime/compute/test_render_policy.py::TestAdmission::test_accounts_for_binary_owner_workspace |
| 24 | Non-object descriptors rejected | Error | Null, scalar or array JSON | Invalid input classification | Unit | ✅ unit/modules/media/test_compute_geometry.py::TestDecode::test_rejects_non_object_descriptors |
| 25 | Malformed cameras rejected | Error | Object, string, wrong shape or nonfinite matrix | Invalid input classification | Unit | ✅ unit/modules/media/test_compute_geometry.py::TestDecode::test_rejects_malformed_cameras |
| 26 | Completed requests release geometry references | Edge | Clients finished; geometry evicted | Arrays become reclaimable without another request | Contract | ✅ contract/runtime/compute/test_client.py::TestBinaryRender::test_releases_completed_geometry_after_eviction |

## Controlled observations (RTX 5060, Docker/WSL)

All 40 prepared inputs are the same ones used in the earlier audit. These timings
include broker IPC and final RGBA retrieval, excluding source parsing, client
geometry packing, encoding, publication and interactive search. They are render
performance evidence, not full ingestion qualification. The process used the
native wgpu OpenGL backend over Mesa D3D12, inside Docker/WSL.

| Work | Previous broker | Corrected broker |
|---|---:|---:|
| Warm sequential, 40 inputs | 12.09 s | 2.53 s |
| Warm, four concurrent clients, 40 inputs | Historical audit only | 2.38 s / 2.43 s |
| First rendering pass, 40 inputs | Historical audit only | 5.07 s |

The corrected warm sequential pass is 4.78× faster than the prior broker pass.
Direct CPU rendering of these prepared inputs previously took 27.29 s median;
its scope excludes IPC, so it is not a matched complete-ingestion comparison.
The first rendering pass includes shader creation and geometry uploads; initial
broker/device startup is separate: 2.94 seconds. Across four passes,
160 outputs completed without fallback; repeated outputs have identical digests.
The initial pass uploaded 130,704,840 bytes to the owner and 130,716,096 bytes to
the GPU. All three warm passes uploaded zero geometry bytes at both boundaries.
The four-client passes used 11 and 16 completion/readback groups for 40 frames.
Accounted final device residency was 324749252 bytes under the 1 GiB ceiling;
host geometry retained 130,704,840 bytes under the 128 MiB cache ceiling.

The real-device diagnostic passes 15 cases, including exact GPU postprocessing
canaries, byte-equal mesh postprocessing at 64, 640 and 641 pixel widths, shaded
cache reuse and eight-frame-to-singleton readback. Fault injection remains
separate evidence from physical device failure. No physical OOM/reset recovery
or AMD/Intel support claim is made. PNG/WebP encoding stays on CPU.

## Final confirmation

After tightening separate host queue accounting, the same physical 40-model
experiment completed another 160 outputs without fallback. Warm sequential work
took 2.24 s (5.40× versus the original broker's 12.09 s); four-client passes took
2.29 s and 2.27 s, using 12 and 11 readback groups. The first render pass took
5.05 s. Input and device geometry uploads remained zero in all warm passes.
The earlier 2.53 s measurement remains above as a separate observation. These
observations are from a nonexclusive host and do not establish latency percentiles.

The complete private corpus repetition published 92/92 requested thumbnails.
All 92 match the previous GPU pipeline pixel-for-pixel; all originals pass content
verification and published metadata matches the prior run. Placement remains
65 GPU, 25 embedded and two CPU paths. It took 625.08 s through the sequential
ASGI ingestion runner. That elapsed time includes parsing and durable publication,
but is not a paired complete-ingestion performance qualification. No human visual
acceptance was recorded by these comparisons.

Local regression selection: 120 compute/media/IPC cases pass; eight additional
client-recovery/CPU-ingestion cases pass; the physical diagnostic passes 15 cases.
Ruff, scoped formatting and pyright pass. Full backend and exact-head CI results
are recorded in the PR validation section after completion.

The retirement regression failed before clearing the scheduler's completed-ticket
references: evicted input arrays remained reachable while the broker was idle.
The scheduler now releases those references after signalling each result; handlers
retain their own ticket and credits through delivery. The real-socket regression
requires the evicted arrays to become reclaimable without another request.

The full backend run began before this final reference-retirement follow-up.
Its focused 120-case selection and exact-head CI cover the follow-up separately;
the regression was observed failing before its fix. Rendering shaders and pixel
semantics are unchanged by this lifetime correction.
