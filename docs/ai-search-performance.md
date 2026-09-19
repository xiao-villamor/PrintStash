# AI Search performance acceptance

Run these measurements on an otherwise idle host, after correctness and coverage
checks finish. Keep each output directory and its `result.json`. Record the Git
commit, physical hardware, operating system and available memory alongside the
report. A VM result does not establish physical Raspberry Pi or ARM acceptance.

## Native-vector feasibility

From `backend/`:

```sh
uv run python -m tests.fakes.vector_scale \
  --directory /tmp/ai-vector-scale-500k \
  --count 500000 --dimension 384 --queries 32
```

The destination must be new. The harness reserves capacity for durable floats,
the native derivative and a separate durable-only snapshot. It measures actual
`sqlite-vec` exact KNN against the shipped float scanner, using identical vectors
and both unrestricted and restricted SQL scopes. It records recall@10, p50/p95,
build time, database size and resource use. A fresh SQLite connection then reads
the snapshot without loading any vector extension and verifies its digest and
neighbors. This numeric feasibility result does not measure model quality or
the production backup HTTP workflow; those have separate tests.

## End-to-end query latency

Explicitly preplace the pinned BGE export described in
[the model study](ai-search-model-study.md), including its reviewed manifest.
The harness performs no model acquisition or remote inference.

```sh
uv run python -m tests.fakes.search_scale \
  --directory /tmp/ai-search-scale-100k-numpy \
  --model-directory /absolute/path/to/bge-export \
  --count 100000 --queries 32 --backend numpy

uv run python -m tests.fakes.search_scale \
  --directory /tmp/ai-search-scale-100k-native \
  --model-directory /absolute/path/to/bge-export \
  --count 100000 --queries 32 --backend sqlite_vec
```

Each run creates a separate installation, completes public setup, and calls the
real search HTTP endpoint with normal authentication, authorization, lexical
ranking, local ONNX inference and result materialization. The query-vector cache
is cleared before measuring the distinct frozen queries, so none uses a cached
query vector. Pass-through timing
records embedding, vector retrieval, fusion and Model materialization, as well
as the entire request. The initial request and subsequent warmup are separate;
the normal background warmer may finish during fixture construction, so the
first request is not automatically classified as a cold encoder measurement.

The result retains all timings and exits nonzero if warm p95 exceeds **300 ms**.
Only a 100,000-passage run with the declared hardware satisfies that scale check.
Smaller `--count`/`--queries` settings validate the harness, not the latency gate.

The frozen corpus has **32 distinct engineering texts**, replicated to the
requested cardinality with unique Model identities. Durable document vectors
come from the versioned measured BGE fixture; query vectors come from the real
encoder. Replication preserves realistic passage and authorization structure but
does not supply 100,000 independent semantic labels. The report records the
corpus/query digests and distinct-text count so those claims remain separable.

## Ingestion under backfill

```sh
uv run python -m tests.fakes.search_ingest_load \
  --directory /tmp/ai-search-ingest-load \
  --model-directory /absolute/path/to/bge-export \
  --count 10000 --uploads 20
```

The acceptance limit is declared before measurement: **at most 25% additional
p95 upload-to-completion latency, no failed ingests, and actual inference
backfill running throughout every loaded upload**. Baseline and loaded phases
use fresh installations and the same ordered G-code corpus. Unique comments
prevent duplicate detection from bypassing ingestion; each phase records the
exact file hashes. The seeded library has 32 distinct texts, replicated to
10,000 unembedded passages. Both phases warm the same local model beforehand. The loaded phase allows up
to 900 seconds for initial full-library reconciliation before inference starts;
that preparation duration is recorded separately as `backfill_startup_seconds`.
It is outside the per-upload measurement and does not relax the 25% limit.

The report contains per-upload completion latency, generation progress before
and after each upload, and sampled RSS/CPU for the application and its worker
processes, including children spawned by executor threads. Sampling accompanies
job polling, so its RSS maximum is labelled as sampled rather than an exact
kernel high-water mark. A run where backfill finishes before the uploads does
not satisfy the overlap requirement. The same generation must remain in backfill
at every recorded boundary, its indexed count must never regress, and it must
publish more vectors during the loaded sample. A stalled status label cannot
satisfy the check. The command preserves phase reports and exits nonzero when
comparison fails.

The completed pre-correction run at `40b9cb3` failed this gate: all 40 uploads
succeeded and indexed vectors advanced from 4 to 80 during the loaded sample,
but p95 increased from **203 ms to 735 ms (3.62×)**. Median latency increased
from 185 ms to 370 ms. Initial reconciliation took 617.75 seconds, separately
from upload timing. The raw result and sampled process metrics are retained in
`backend/tests/fixtures/search/ingest-backfill-10k-x86-before.json`.
This failure motivated a regression test for per-vector publication: adding
1,000 unrelated passages raised its SQLite work from 200 to 43,800 instructions
while the publisher held the writer lock. The correction preserves source and
lease fences while correlating eligibility to the current passage. The follow-up measurements below retain both the intermediate failure and the
foreground-priority result.

Physical Pi 5 backfill and independent human query/photo quality acceptance
require separate measurements; these harnesses do not substitute for them.


## Measured 500,000-vector feasibility on x86

Measured on 2026-09-12 at `0364db751861bdecb122679ffa998181417c93d9`:
Linux x86_64, QEMU/KVM, four virtual CPUs, 11,677 MiB RAM, one OpenBLAS thread,
SQLite 3.53.1 and sqlite-vec 0.1.6. Existing user services remained running;
no other test or benchmark was run concurrently. This is a VM measurement,
not the physical ARM result required by S2.

| Eligible vectors | Native p50 / p95 | Float scanner p50 / p95 | Native recall@10 |
|---|---|---|---:|
| 500,000 | 2.247 / 2.281 s | 6.870 / 6.957 s | 1.0 |
| 50,000 (10% SQL scope) | 1.787 / 1.921 s | 1.826 / 1.887 s | 1.0 |

All 32 seeded queries in each scope matched the float top 10 exactly. Building
both tables took 182.22 seconds. The source database occupied 1,808,351,232 bytes;
the durable-only restored database occupied 1,026,580,480 bytes, including
768,000,000 bytes of vector payload. Snapshot copying took 9.60 seconds. The
restored connection had no vector extension loaded; every row was digest-checked
and both scopes returned the same neighbors. Peak process RSS was 68,536 KiB.
The complete run took 621.76 seconds.

The native index improved unrestricted retrieval but did not improve the
restricted-scope p95 in this experiment. It stays opt-in. Neither result meets
or substitutes for the separate 100,000-passage HTTP target: these timings have
no query encoder, application authorization or response materialization.
Raw timings, versions, input digest and source context are retained in
`backend/tests/fixtures/search/sqlite-vec-500k-x86-{vectors,context}.json`.

## Query-path corrections found by the 100k run

The initial portable run was interrupted after repeated 8.3–8.7 second warm
requests exposed a real regression; it is not a completed p95 measurement.
Profiling found repeated worst-candidate sorting, whole-library permission scans
for small result sets, and writer contention during periodic projection repair.
The corrected scorer keeps a bounded competitive cutoff, while SQL preserves
fresh owner/contributor visibility and restricts card/result checks to candidate
identities. Periodic repair now processes one Subject per stream per transaction;
embedding bursts retain their independent batch budget.

Regression tests compare the scorer against an independent full-sort oracle and
measure SQLite instruction counts before/after adding 1,000 unrelated Models or
Passages. Limited, joined, distinct and empty authorization scopes retain exact
membership. PostgreSQL tests recheck contributor grants and trash state. The
query-deadline regression measures its one-second tolerance from actual provider
admission; cold SQL compilation/authentication belongs to the separate complete
HTTP latency benchmark, whose 300 ms budget remains unchanged.

Before the final vector-store correlation change, a complete 32-query native
run measured p50 **4.059 s**, p95 **4.479 s** and first-request **5.557 s**. That
run remains in the task's measurement output. Like the other early native runs
below, it used the incomplete native fixture and is not native-scale acceptance evidence.

## Earlier HTTP measurements and native-fixture correction

The query implementation is `cb080c6af780103d3eb2e136f1f82f222a33b3b3`.
The same four-vCPU QEMU/KVM host, 11,677 MiB RAM and one-thread pinned local BGE
encoder were used. No correctness tests or other benchmarks ran during the
measured queries; existing user services remained running. Each backend serves
32 distinct uncached queries over 100,000 replicated Model identities with
normal authentication, authorization, lexical ranking and Model cards.

| Backend | Warm p50 | Warm p95 | First HTTP request | 300 ms p95 gate |
|---|---:|---:|---:|---|
| Portable NumPy | 2.348 s | 3.024 s | 9.825 s | Failed |
| Optional sqlite-vec (incomplete native fixture) | 1.780 s | 2.320 s | 2.599 s | Invalid native-scale measurement |

Portable results: [complete observations](../backend/tests/fixtures/search/search-100k-numpy-x86.json)
and [source/hardware context](../backend/tests/fixtures/search/search-100k-x86-context.json).
Native results: [complete observations](../backend/tests/fixtures/search/search-100k-native-x86.json).
All 64 responses across both runs returned HTTP 200 with no leg errors and actual
local query embedding. A later cardinality audit found only **22 native rows**
in that native-run database, despite 100,000 durable rows. A subsequent run
measured p95 2.355 s but held only **14 native rows**. Both native timings are
invalid for native-scale acceptance and cannot establish a speedup. The
[second invalid result](../backend/tests/fixtures/search/search-100k-native-priority-incomplete-x86.json)
is retained. The portable measurement remains valid and misses the unchanged
300 ms target. Neither fixture supplies independent semantic labels.

The fixture defect was an early-ready race: individual seed factory commits let
normal background repair complete a small native table before direct bulk
replica insertion. Calling repair again on a ready index does not repopulate it.
The corrected query harness explicitly prepares the derivative after all direct
fixture inserts, copies the measured full-dimension floats into the real native
table, and asserts **durable and native counts before and after all queries**.
This is query-fixture preparation; it does not measure production backfill
throughput. Its regression reproduces early readiness and checks that all native
IDs match the completed durable fixture.

The first 10,000-passage ingest attempt completed all 20 baseline uploads (p95
222 ms) but exceeded the harness's original 120-second generation-start limit
before any loaded uploads ran. Profiling one reconciliation partition found
128 distinct Subjects, 3,205 SQL statements and 5.94 seconds with profiling
active; only 0.27 seconds were in SQLite execution. Most measured cost was
Python/ORM extraction and statement construction. The harness now records
preparation separately and permits its bounded 900-second startup window. The
failed attempt is not counted as a completed load comparison.

## Foreground-priority backfill follow-up

Correlating publication eligibility reduced the fresh-install loaded p95 from
735 ms to **469 ms**, but its 211 ms baseline still meant **2.22×**, above the
unchanged 1.25× limit. All 40 uploads completed with actual backfill progress.
Initial preparation took 813.37 seconds with correctness checks running during
preparation only; that startup duration is not an uncontended benchmark. The
[complete intermediate failure](../backend/tests/fixtures/search/ingest-backfill-10k-x86-bounded.json)
is retained.

Foreground admission now covers the complete mutating ASGI request, including
upload staging and cleanup after its response. Search defers new background
work during those requests, yields before text-vector publication without
holding a database transaction or compute slot, and keeps ordinary repair
transactions to one Subject. Restore admission and cancellation remain enforced.

The subsequent comparison cloned the same stopped, partially indexed library
for each phase: **10,020 passages**, including the prior twenty completed
uploads. The baseline cancelled its building generation; the loaded phase
resumed it. Both warmed the pinned local BGE model and ingested the same twenty
new G-code payloads. All **40 uploads completed**, with loaded indexed vectors
advancing **88 → 104** while the generation stayed in backfill throughout.
Baseline/loaded median latency was **175/190 ms** and p95 was **227/233 ms**:
**1.025×**, passing the unchanged 1.25× comparison. No correctness tests or other
benchmarks ran during the timed uploads.

[The complete prepared-fixture result](../backend/tests/fixtures/search/ingest-backfill-10k-x86-priority.json)
records every payload hash, observation, process sample and production-source
digest. This follow-up measures ingestion during resumed backfill; it does not
repeat fresh-install preparation, establish physical ARM performance, or erase
the earlier failures. AI remains optional and disabled by default.

The first populated native rerun verified 100,000 rows before its query loop
and completed all 32 HTTP queries, but its final report writer accessed an ORM
object after the seeding session had expired it. No p95 result is accepted from
that interrupted report. A separate read of the stopped database verified
100,000 durable and 100,000 native rows. The harness now retains observations
before final validation and re-reads the generation by its stable ID in a fresh
session; the new regression covers that session boundary.

## Populated native 100,000-vector HTTP result

The corrected run used production `a92720eb` and verified **100,000 durable
and 100,000 native vectors before and after all 32 uncached queries**. Every
request returned HTTP 200 with no leg errors and real local BGE embedding.
On the same four-vCPU QEMU/KVM host, warm p50 was **1.462 s**, warm p95
**2.136 s** and the initial HTTP request **3.620 s**. Fixture preparation took
72.05 seconds; the complete run took 131.25 seconds. The unchanged **300 ms
p95 gate fails**. This valid scale measurement replaces the incomplete native
fixtures as evidence, while retaining their raw failures and explanations.

[Complete populated-native observations and source/hardware context](../backend/tests/fixtures/search/search-100k-native-populated-x86.json).
No correctness tests or other benchmarks ran during measured requests. Stopped
diagnostic databases were removed during preparation, before the first request,
to recover disk space. This VM result does not establish physical ARM acceptance.

The implementation is available on the feature branch. Full plan acceptance
remains open for 100k query latency, physical Pi 5 backfill, the physical ARM
point canary, independent held-out visual recall and a demonstrated quality
improvement at every offered visual profile. These gaps are explicitly retained
in the coverage matrix; the feature is not presented as release-accepted.
