# M0 measurement qualification

These requirements were recorded before the observer changes and runs. M0
qualification is recorded below; it does not qualify later implementation goals. The
implementation base is `710e4eb7aafe05642693d6ff2696146e042dca53`, checked out
separately at `/home/local/PrintStash-frontend-baseline`. No production source
changes are allowed in that checkout. Apply the identical observer patch to the
final comparison; record its hash and all dirty paths separately from the base.

Use the existing production nginx/real SQLite startup corpus: distributed folders
and a dense 90-Model collection, desktop 1440×900, EN/ES, active service worker,
30 warm and 20 fresh authenticated contexts per locale/corpus after one discarded
warm-up. Fresh means a new browser context, not a cold OS/database. No timing
threshold is enforced for baseline collection. Retain failed/partial runs.

| #   | Behaviour (test name)                             | Category | Precondition / input             | Observable outcome asserted                                                                                      | Tier                   | Status                                                                                                                                                         |
| --- | ------------------------------------------------- | -------- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | exposes usable library content                    | Happy    | Each corpus and locale           | Visible cards/tree, readiness after content, collection navigation works                                         | Playwright             | ✅ `frontend/tests/performance/library-startup.spec.ts::${distribution} startup (${locale}) with active service worker` — 200 completed samples                |
| 2   | observes decoded visible thumbnails               | Happy    | Dense collection                 | Nonempty viewport-intersecting card set; every image decodes and remains connected with the same source          | Playwright             | ✅ `frontend/tests/performance/library-startup.spec.ts::dense startup (${locale}) with active service worker` — six images decoded per sample                  |
| 3   | distinguishes folder-only thumbnail applicability | Edge     | Distributed root                 | Zero Model images is explicitly reported as not applicable                                                       | Playwright             | ✅ `frontend/tests/performance/library-startup.spec.ts::distributed startup (${locale}) with active service worker` — zero images explicitly N/A               |
| 4   | retains incomplete measurement evidence           | Error    | A sample fails                   | Sample identity, completed checkpoints, network starts/finishes/failures and failure outcome persist             | Playwright measurement | ✅ `frontend/tests/performance/library-startup.spec.ts::dense startup (en) with active service worker` — controlled 5s interruption; partial JSON verified     |
| 5   | records requests still pending at readiness       | Edge     | Startup overlaps secondary reads | Network-start records include requests absent from completed Resource Timing; checkpoint preserves pending count | Playwright measurement | ✅ `frontend/tests/performance/library-startup.spec.ts::${distribution} startup (${locale}) with active service worker` — nonzero pending checkpoints retained |

Network event timestamps and decoded-image completion are observer upper bounds,
not exact browser paint or scheduler queue times. Record resource durations and
server timing independently. This corpus does not measure mobile, deep-tree,
mixed-type, restricted-user or ingest-load performance; those gaps remain explicit
for M6/M11. Functional correctness evidence is reported separately from timing.

## Exact-base timing result

**200/200 samples completed**, plus four separately retained discarded warm-ups.
Two distributed test cases passed in 3.8m; two dense cases passed in 5.3m. Each
locale/corpus has 30 warm and 20 fresh-context samples. Median averages the middle
pair; p95 is nearest rank. No outlier was removed, no retry was used, and budgets
were disabled while collecting the baseline. The legacy result filename says
`after`; the enclosing manifest identifies these files as **M0 baseline**, not a
post-refactor comparison.

| Corpus      | Locale | Cache         |   n | Ready median / p95 (ms) | Decoded images median / p95 (ms) |
| ----------- | ------ | ------------- | --: | ----------------------: | -------------------------------: |
| distributed | en     | warm          |  30 |         283.90 / 356.70 |           N/A — folder-only root |
| distributed | en     | fresh context |  20 |        573.45 / 1636.60 |           N/A — folder-only root |
| distributed | es     | warm          |  30 |         277.65 / 663.40 |           N/A — folder-only root |
| distributed | es     | fresh context |  20 |         559.70 / 592.90 |           N/A — folder-only root |
| dense       | en     | warm          |  30 |         451.85 / 636.00 |                1565.55 / 2376.20 |
| dense       | en     | fresh context |  20 |        788.00 / 1247.20 |                1963.30 / 2468.90 |
| dense       | es     | warm          |  30 |         429.40 / 854.90 |                1535.15 / 1972.70 |
| dense       | es     | fresh context |  20 |        702.00 / 1158.30 |                1852.10 / 2305.80 |

The dense Spanish warm p95 **854.90ms exceeds the historical 800ms reference**.
That is an observed baseline miss, not an M0 collection failure or evidence about
the refactor. The distributed English fresh-context tail reaches p95 1636.60ms;
its median is 573.45ms. Keep these tails in the comparison. Every dense sample
explicitly decoded six visible images; distributed root had zero Model images,
reported as not applicable rather than an image-speed success.

Ready is the independent DOM observer's two-frame content/tree mark. Decode is
an upper-bound observation after the internal readiness check, browser polling,
explicit `HTMLImageElement.decode()` and one frame; it is not the instant the
first bitmap became available. Preserve the exact observer for comparison.

At the later ready-observation checkpoint, median admitted requests are 37 for
distributed and 87 for dense. Maximum simultaneous requests over the _whole sample_,
including the Collection 02 usability interaction, reach 21 and 32 respectively.
These include scripts and API calls, not just images; they cannot select an image
concurrency limit by themselves. Pending-request checkpoints are nonzero in some
samples, so completed Resource Timing alone would omit admitted work.

There were zero observed HTTP statuses >=400 across the 200 samples. There were
197 `requestfailed` notifications in dense samples, predominantly outliner reads;
the recorder does not classify cancellation versus network failure. Do not label
these as 197 application errors or silently discard them. Raw event identity,
path and chronology remain available for M6 diagnosis. Resource Timing and server
timings are retained separately; browser scheduling queue time, JS execution,
render CPU and memory were not measured by this run. None is inferred from bundle
size, request count or decoded-body bytes.

## Provenance and comparability

- Base commit: `710e4eb7aafe05642693d6ff2696146e042dca53`.
- Base tree: `4012d6994bdaabd907198c7a2435df3e29b22746`.
- Chromium `148.0.7778.96`; Node `24.19.0`; actual pnpm `10.18.1`; Python `3.14.8`.
- WSL2 Linux `6.18.40.1-microsoft-standard-WSL2`, same host for the eventual comparison.
- nginx image: `sha256:26b0bf6fbf07297983cb341998d79c831508787de26627dd2a112321b9c3a4af`.
- Observer source SHA-256: `3f8862433e4627ebeeb0778ad4358efedc185554e823516d29de3e7bab73646f`.
- Observer patch SHA-256: `1c3c72437af9245f213b3d49c5712c27f1320f0cef1ce75389691990ecc849c1`.
- Lockfile, seed, proxy, launchers and config hashes are in the retained run manifest.

The frontend uses its own offline-frozen install, with workspace links resolving
inside the baseline checkout. The reused Python third-party environment has an
explicit baseline core `PYTHONPATH`; actual app/core import paths were verified.
Uncommitted test-only overlays are separately identified; production sources were
unchanged. The temporary failure-reproduction fixtures were archived, then removed
before the baseline static/original-suite checks. The observer remains.

Timing ran serially with no other task test gates in parallel. This is a shared
host, not an isolated benchmark machine; initial load averages were 2.31/2.45/2.29
and 2.35/2.39/2.29 for distributed/dense. Authentication and worker installation
occur outside sample timing. The browser's fresh context still shares a warm OS,
backend and image seed. Do not compare these observations directly with the
historical dirty-tree numbers or claim a frontend improvement yet.

## Baseline gates and observer failure handling

After the serial timing run, a **deliberate 5,000ms whole-test deadline** stopped
the dense English warm-up during the Collection 02 usability action. This is a
fault-injection run, not a failed sample in the 200-sample timing distribution.
The unchanged observer retained one incomplete sample, both readiness/decode
checkpoints, and **275 network events**. A separate verification required exit 1,
one incomplete JSON record, nonempty events and checkpoints. No ordinary sample
was retried or removed. The failure log and partial record remain separate.

After removing only the task's archived baseline reproduction overlays, the base
plus observer passed:

| Check                                                                          | Actual result                                                                          |
| ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| App/UI/domain `pnpm typecheck`                                                 | Passed                                                                                 |
| `pnpm lint`                                                                    | Passed, zero warnings                                                                  |
| `pnpm format:check`                                                            | Passed, 658 files checked                                                              |
| Selected original Library/transport/Query/navigation/assets/auth/startup tests | 242 passed, 7 files, 38.89s                                                            |
| UI workspace package tests                                                     | 199 passed, 21 files, 6.79s                                                            |
| Domain workspace package tests                                                 | 60 passed, 6 files, 1.67s                                                              |
| Production startup functional cases                                            | 8 distributed + 9 dense passed                                                         |
| Current integration app/UI/domain types; owned observer lint/format            | Passed against the unchanged observer hash                                             |
| Production Vite build                                                          | Passed through each startup launch; existing locale-shell/chunk-size warnings retained |

This is a focused functional baseline plus both package suites, not the full
frontend suite, full browser suite, coverage floor, or current-refactor qualification.
The failed reproduction assertions are expected evidence about unchanged old code;
no old production bug was fixed to make baseline gates green.

## M0 acceptance

- Inventory: 873 scoped paths reconciled; all 762 historical rows preserved;
  zero omitted paths in the declared roots; 14 pre-existing overlay hashes checked.
- Evidence: all six requested failure families have exact-base runtime receipts;
  setup errors are excluded, historical measurements remain separately labelled.
- Baseline: 501 original unit/package tests, 17 production functional cases and
  200 comparable startup observations, with explicit decoded-image checks.
- Observer: scoped lint, baseline full types/format and deliberate interruption
  establish usable recordings without changing production code.

M0's evidence-freeze criteria are met. The dense Spanish timing miss, incomplete
whole-frontend manual inspection, missing current refetch-race GREEN assertion and
unmeasured CPU/memory scenarios remain assigned to their later plan owners; none
is presented as resolved. M1 may start after this checkpoint is committed. M2–M11
remain prerequisite-gated. Final implementation delivery and remote CI remain M11.
