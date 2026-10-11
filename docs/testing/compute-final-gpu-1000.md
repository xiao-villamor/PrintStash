# Final cached GPU pipeline validation: 1000 sources

The operator requested a final execution of all 1000 cached sources: **481 STL,
486 OBJ and 33 STEP**, in the original order. Selection SHA256:
`9cc1871cb4b085a0dd54bda6eb5c6f7503c3caed9039d1aa5ff44edfbe7add6e`.
The historical cohort has 973 renderable sources and 27 invalid sources; invalid
inputs remain in the workload and must retain their expected terminal failures.
No CPU or old-GPU reference is rerun. No R2 request is permitted.

## Execution policy

The private Docker/WSL deployment renders on the physical RTX 5060 with portable
native wgpu through Mesa D3D12. The first four inputs include broker cold startup.
The remaining inputs use a bounded continuous window: at most 40 pending requested
outputs and four uploads plus nonterminal ingestion Jobs. Only an HTTP 507 with
exact `staging_capacity_exceeded` detail can retry, with bounded one-second backoff;
other upload errors abort. Sources come exclusively from the immutable local cache,
and Docker uses an internal network. User visual review remains separate.

Record WSL monotonic, UTC and independent Windows QPC intervals separately. The
historical GPU reference prewarmed its broker and used closed waves of four, and
has no QPC measurement. It is a same-cohort historical observation, **not a matched
control for a causal speedup claim**. Broker execution, transfer, Job queue and
codec intervals overlap; their sums are not complete-pipeline wall time. Device
reservations are admission estimates, not measured VRAM peaks.

## Interrupted diagnostic and correction

The first attempt used `81e65ca400423902dd0b74c16c70a3021186f720` and was stopped
when inspection found forced cancellation probes on every 64 KiB transport block
and every host/codec admission poll. Each forced probe queried the Job database,
defeating the existing 200 ms throttle. The cost share was not measured; it cannot
be inferred from accumulated dispatch or queue time.

The partial attempt accepted 367 sources: 320 thumbnails were ready and seven had
historical input failures, with no new failure at the last snapshot. This is an
**incomplete diagnostic**, not a final 1000-source result or a performance comparison.
Its image, harness, counters, clocks and stop state remain in the
[aggregate evidence](compute-final-gpu-1000.json). The private measurement processes
and container were stopped; the user's deployment was left unchanged.

The correction restores the existing cancellation cadence during transport and
admission polling. Every callback still checks the absolute monotonic deadline.
GPU dispatch/result, codec entry/exit, CPU fallback and delivery boundaries force
an immediate cancellation check. Sticky cancellation, memory custody and transactional
publication fences remain unchanged. All 112 focused tests passed; Ruff and scoped
production Pyright passed. See the [exact behavior matrix](compute-thumbnail-handoff-v2.md).

## Final corrected run

The complete run on `fd76e2de57f3f8ae1be4a2d1082c7ff901831bed` finished with
**973 metadata/thumbnail pairs ready and the same 27 `invalid_source` failures**.
Every source's terminal states and failure reasons match the reference. All 1000
original hashes/sizes were verified; all 973 available authoritative measurements
match exactly. All 973 saved 640×480 frames decode and contain alpha ≥128 foreground.
This is a nonempty-frame check, not operator visual approval.

The first four cold inputs reached durable outputs in 12.744 seconds. The complete
interval runs from submission through terminal requested outputs; it excludes
image collection and report generation after publication. No CPU or historical
GPU benchmark was repeated. The internal-network run and verification made zero
R2 requests; the request ledger remained unchanged.

| Observation | WSL monotonic | WSL UTC | Independent Windows QPC |
| --- | ---: | ---: | ---: |
| Historical CPU, reused | 4050.997 s | Not included here | Not measured |
| Historical GPU (`26b8f4b4`) | 2070.994 s | 2169.074 s | Not measured |
| Final GPU-enabled pipeline (`fd76e2de`) | 2334.300 s | 2477.664 s | 2479.009 s |

The final observation is **longer than the historical GPU result**, by 12.7% in
recorded monotonic time and 14.2% in UTC. The historical policy differs as described
above; neither ratio isolates the implementation, GPU device, batching or this
cancellation correction. No throughput improvement or automatic qualification is
claimed. The reused CPU measurement is historical context, not a new matched
control. QPC brackets the interval with recorded process-invocation gaps; the
clock disagreement is retained without an inferred correction.

Image identity: `sha256:e89d0d15f6623d7c6975974c716a9fcf7f3f8791c3c58ca2b72f78e89fc28bae`.
The exact harness, source-tree and selection hashes and boundary observations are
in the [sanitized aggregate](compute-final-gpu-1000.json). The frozen selection and
submission order match all 250 historical four-source wave sets; concurrent HTTP
acceptance and publication order can differ.

## Rendering, batching and remaining costs

The broker recorded **967 frames in 938 batches/submissions**: 911 singleton
batches, 26 batches of two and one batch of four. Thus 56 frames shared a batch
with another model, while 97.1% of batches were singletons. All render requests
were background work. Forty pending Artifacts did not supply forty prepared
geometries at once within the 10 ms coalescing window.

There were 963 completed GPU handoffs with matched supervision identities,
zero completed handoff CPU recoveries and zero CPU fallback children. The broker
also recorded zero infrastructure fallbacks and zero canonical CPU normal retries.
These are separate counters. Of the 973 valid images, **967 used GPU rendering
and six used the combined CPU route**. The four initial cold inputs account for
the GPU work outside the 963 handoffs. The remaining six are large STL meshes:
their canonical-equivalent admission envelope exceeds the existing 48 MiB
bound before its optional component term. The compact representation itself
could fit for five of them, but current eligibility also bounds the canonical
recovery representation. This is a conservative software admission limit, not
insufficient physical VRAM. They are rejected before broker dispatch and
use canonical CPU rendering. They are not handoff recoveries or evidence of a
hardware failure. The final aggregate retains the route classification and
conservative packet-size evidence without exposing source names.

Exactly 1000 disposable executions were supervised. The 1963 native admissions
include those source workers plus 963 inline codec CPU slots; they do not mean
1963 processes. There was no additional encoder child. All workers were reaped
with `exited_zero`; the 27 invalid-source results remain structured domain failures.

| Recorded scope | Count | Accumulated seconds | Median | p95 |
| --- | ---: | ---: | ---: | ---: |
| Child load phase | 1000 | 1361.014 | 1.209 | 1.683 |
| Exact measurements | 1000 | 119.824 | 0.009 | 0.527 |
| Worker supervision | 1000 | 3522.229 | 3.246 | 4.716 |
| Handoff packet read | 963 | 18.249 | 0.014 | 0.049 |
| Handoff dispatch | 963 | 405.095 | 0.424 | 0.743 |
| Handoff codec admission | 963 | 1295.678 | 0.067 | 5.895 |
| Inline codec | 963 | 132.525 | 0.132 | 0.210 |
| Broker execution | 967 frames | 29.652 | Not measured | Not measured |
| Broker transfer/readback | 967 frames | 8.961 | Not measured | Not measured |

These accumulated intervals overlap across requests and nested scopes; **do not
sum them as wall time or assign an unmeasured residual to imports**. The codec
admission interval includes waiting for a native CPU slot. Its p95 is much longer
than the measured codec call; supervision also substantially exceeds explicitly
instrumented parser phases. Those are remaining scheduling/preparation costs,
not GPU cold starts. Lower broker time alone did not improve this full workload.
The load and exact-measurement phases remain CPU work; the GPU performs eligible
visual preparation, normals, bounds, framing, rasterization and postprocessing.

Broker startup was 2.577 seconds and was reused. Rendering recorded 967 GPU
geometry uploads and 168 GPU normal uploads, with no canonical CPU normal retries.
The local shader normal-recovery counter covers 6099 vertices and is distinct
from a CPU retry. No geometry-input or rendered-image cache hits were observed,
so no cache-hit speedup is inferred for this unique-source, single-view workload.
The final device reservation was 168,695,444 bytes under a 1 GiB budget; it is not
a physical VRAM peak. Maximum supervised worker-tree RSS was 1,040,977,920 bytes,
which excludes parent codec and separate broker memory.

Whole-adapter one-second observations reported GPU utilization median 0%, p95 1%
and maximum 15% over 2330 samples. They include unrelated Windows activity and can
miss short rendering bursts. They do not measure process-specific GPU occupancy.
The observed low utilization and uncommon multi-model batches remain consistent
with the pipeline supplying ready geometry infrequently; filling VRAM alone does
not eliminate source preparation, admission and publication costs.

Two uploads received the allowed pre-admission staging-capacity refusal and
retried once each, adding two accumulated seconds of bounded backoff inside the
timed interval. No source was replaced. Database Job-retention limits leave 582
creation-to-terminal intervals available for the later per-Job diagnostic; the
418 missing intervals are not imputed. The full terminal/outcome and source
comparisons still cover all 1000 inputs.

## Validation and remaining scopes

The cancellation correction passed 112 focused tests, Ruff and scoped production
Pyright. All 17 CI checks passed for the measured code commit in
[run 38176054698](https://github.com/xiao-villamor/PrintStash/actions/runs/38176054698).
The earlier expanded custody validation remains in the
[behavior matrix](compute-thumbnail-handoff-v2.md); overlapping test selections
are not summed into a unique test count.

| Behaviour | Category | Input | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| Process the complete mixed cohort | Pipeline | Same 1000 cached STL/OBJ/STEP inputs | 1000 metadata and 1000 thumbnail terminal results | Deployment | ✅ |
| Preserve valid and invalid outcomes | Integrity | 973 valid and 27 invalid sources | All 1000 states and failure reasons agree; no new failures | Deployment | ✅ |
| Preserve originals and measurements | Integrity | Source digests and authoritative geometry | 1000 originals verified; 973/973 exact measurements | Deployment | ✅ |
| Avoid storage requests | Cost | Local cache and internal network | Zero R2 requests; ledger unchanged | Deployment | ✅ |
| Exercise real cross-model batching | GPU | Independently admitted requests | 26 two-model batches and one four-model batch | Deployment | ✅ |
| Retain phase cancellation and deadlines | Recovery | Fragmented transport and admission polling | Throttled probes, per-callback deadlines and forced boundaries | Unit/integration | ✅ 112 focused tests |
| Separate GPU, CPU and recovery paths | Recovery | Linked handoff/child events and bounded geometry | 967 GPU renders, six oversized STL CPU renders; no handoff recovery | Measurement | ✅ |
| Decode visible saved output | Correctness | 973 saved 640×480 images | All decode with alpha ≥128 foreground | Deployment | ✅ |
| Demonstrate better complete-pipeline throughput | Performance | Historical GPU comparison | Final observation longer; policies differ | Deployment | ❌ Improvement not established |
| Approve visual appearance | Visual | Paired rendered outputs | Operator review | Manual | ⏭️ Pending user review |
| Qualify interactive latency and other hardware | Performance/portability | Mixed search traffic, AMD/Intel, actual device faults | Separate physical qualification | Deployment | ⏭️ Not claimed |
