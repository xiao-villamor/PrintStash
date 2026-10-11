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

Pending a fresh, frozen-image execution of the complete 1000-source cohort.
The corrected harness changes only its private namespace relative to the interrupted
attempt and retains the same cohort, ordering, timing and admission policy.

| Behaviour | Category | Input | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| Process the complete cohort | Pipeline | All 1000 cached sources in original order | Terminal outcomes for all sources | Deployment | ⏭️ Corrected run pending |
| Preserve valid and invalid outcomes | Integrity | 973 historically valid and 27 invalid sources | No new failures; expected failures retained | Deployment | ⏭️ Pending |
| Preserve originals and measurements | Integrity | Source digests and authoritative geometry | Exact prior evidence retained | Deployment | ⏭️ Pending |
| Avoid storage requests | Cost | Local cache and internal network | Zero R2 requests; unchanged ledger | Deployment | ⏭️ Final verification pending |
| Bound cancellation reads without delaying deadlines | Recovery | Fragmented transport and admission polling | 200 ms probe cadence, per-callback deadlines and forced boundaries | Unit/integration | ✅ 112 focused tests |
| Report recovery paths honestly | Recovery | Broker refusal or unusable frame | Client CPU recovery distinguished from broker fallback counters | Measurement | ⏭️ Final counters pending |
| Approve visual appearance | Visual | Paired rendered outputs | Operator review | Manual | ⏭️ Pending user review |
| Qualify interactive latency and other hardware | Performance/portability | Mixed search traffic, AMD/Intel, actual device faults | Separate qualification evidence | Deployment | ⏭️ Not claimed |
