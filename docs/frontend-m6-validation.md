# M6 — startup and protected assets

Status: locally closed after M5 acceptance48bc1eee. Preserved asset work is
reviewed and qualified. No M7–M11 implementation belongs to this pass; final
delivery and CI remain open.

## Preflight coverage matrix

Rows derive from M6 acceptance. The detailed protected-byte matrix remains in
[frontend-state-validation](frontend-state-validation.md#protected-asset-requirements),
rows52–77; thumbnail-only projection has its
[own matrix](library-thumbnail-validation.md). Historical execution does not
substitute for integrated qualification.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | admits visible thumbnails before distant cards | Happy | Held image HTTP; 24 cards span viewport | At most four downloads; distant requests deferred; visible images decode | Playwright | ✅ `tests/e2e/protected-assets.spec.ts::admits visible thumbnails before distant cards` |
| 2 | keeps a mounted image URL under count pressure | Edge | Mounted lease plus405 inactive images | Mounted URL is not revoked | Frontend unit | ✅ `src/lib/__tests__/asset-cache.test.ts::keeps a mounted image URL under count pressure` |
| 3 | bounds inactive image bytes | Edge | Idle blobs exceed32MiB | Old idle URL revoked; current bytes retained | Frontend unit | ✅ `src/lib/__tests__/asset-cache.test.ts::bounds inactive image bytes` |
| 4 | removes abandoned queued image work | Edge | Four active reads, queued lease released | Abandoned HTTP never starts | Frontend unit | ✅ `src/lib/__tests__/asset-cache.test.ts::removes abandoned queued image work` |
| 5 | publishes a restored branch while another remains pending | Edge | Two remembered expanded branches; one HTTP response held | Completed branch visible and usable before slow branch completes | Frontend unit | ✅ `src/components/__tests__/filter-sidebar.test.tsx::publishes a restored branch while another remains pending` |
| 6 | preserves range selection across a page append | Edge | Select in page one; append page two; Shift-click later Model card | Exact expected Model checkboxes selected, no unrelated earlier Model | Playwright | ✅ `tests/e2e/vault.spec.ts::preserves range selection across a page append` |
| 7 | moves a model reached through keyboard pagination beyond 500 entries | Happy | Real paginated outliner; final-page Model | Destination persisted after drag, including editing conflict contract | Real Playwright | ✅ `tests/e2e-real/outliner-pagination.spec.ts::moves a model reached through keyboard pagination beyond 500 entries` (M4-qualified; unchanged source) |
| 8 | reports an image ready only after decode | Happy | Network image complete, decoder pending | Readiness marker waits for decode | Frontend unit | ✅ `src/components/__tests__/protected-thumbnail.test.tsx::reports an image ready only after decode` |
| 9 | releases secondary reads after usable Library content | Happy | Primary reads pending then settled | No unrelated secondary HTTP before content; interaction can explicitly request its own data | Frontend unit / production browser | ✅ `src/lib/__tests__/library-startup-provider.test.tsx::releases secondary reads after both usable surfaces paint`; `tests/performance/startup-behaviour.spec.ts::allows early filter interaction without prematurely loading other secondary reads` |
| 10 | preserves URL filters in the first supported result | Edge | All/Multipart and retired preference values | First browse request carries collection/tag; retired values normalize to All | Production Playwright | ✅ `tests/performance/startup-behaviour.spec.ts` four stored-mode variants; all passed against production backend |
| 11 | pages through all ninety models after the bounded first render | Happy | Dense90-Model collection | First24 rendered; Load more reaches all90 without duplicates | Production Playwright | ✅ `tests/performance/startup-behaviour.spec.ts::pages through all ninety models after the bounded first render` |
| 12 | opens each deferred form on its first use | Happy | Production Library before interaction | Form chunks absent at startup; actual controls open usable Upload/Multipart/Tags/Archive review | Production Playwright | ✅ `tests/performance/startup-behaviour.spec.ts::opens each deferred form on its first use` |

## Measurement protocol

Repeat the unchanged M0 observer SHA256
`3f8862433e4627ebeeb0778ad4358efedc185554e823516d29de3e7bab73646f`:
production nginx and real SQLite, distributed/dense corpora, EN/ES,1440×900,
30 warm plus20 fresh contexts each, active service worker, no retries or overlapping
suites. Keep every sample and failed attempt. Same lockfiles, seed, proxy/config and
launchers were verified by hash. Record the integrated commit plus preserved dirty
upload-workflow files separately; those deferred forms are not used by the timing
flow. Compare readiness, decoded visible images and API/network evidence separately.

Original reference budgets: warm median500ms, warm p95800ms, fresh median1000ms.
M0 itself exceeded the warm p95 reference for dense Spanish (854.9ms); do not waive
that miss or invent a speedup. Four asset downloads and32MiB idle encoded bytes
remain provisional until results are assessed. Encoded Blob bytes are not decoded
GPU/image memory. Deep remembered trees, mobile, restricted users and ingest-load
performance remain separate scenarios requiring explicit qualification or disclosure.

## Inspection at M6 entry

Read the complete cache, protected URL hook, viewport observer, protected thumbnail,
startup coordinator and thumbnail-readiness hook. Rechecked Query outliner readers,
branch composition and expansion persistence, Model/list/Multipart viewport adapters,
the measurement observer/config/launchers and existing image browser assertions.
The current paginated tree has independent branch Query results; the old all-batches
publication barrier is absent. Row5 verifies that observable progressive contract
instead of adding an unnecessary batching abstraction. Current closure gaps are
qualification and measurement, not evidence for a new framework or universal loader.


The unused `getCachedAssetUrl` compatibility export has no production caller;
all display consumers use the lease hook. Its remaining callers are cache tests.
M10 owns the final compatibility API inventory/removal. The unsafe entry-count-only
eviction mechanism is already replaced: only inactive entries can be evicted,
under both count and encoded-byte limits. Mounted bytes remain explicitly live.


## Qualification corrections

The first integrated unit gate returned129 passes and12 failures. Eleven were
existing thumbnail-event tests attempting a private socket without a verified
session after M1 tightened admission; their event assertions are retained, with
an authenticated fixture and session cleanup. The new progressive-tree case
observed its first branch before the slow response, then used a synchronous
lookup while selecting a folder changed the root-query identity. Wait for the
new selected presentation while the other branch stays deliberately pending.
The static gate also rejected Playwright's `exact` option in a Testing Library
query; use its exact string name contract instead. These fixture corrections do
not weaken production admission or introduce retries/timeouts.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 13 | follows only the Models still waiting for a thumbnail | Happy | Verified user; one missing and one existing image | Socket subscribes only to missing Model | Frontend unit | ✅ `src/lib/__tests__/use-thumbnail-arrivals.test.ts::follows only the Models still waiting for a thumbnail` — all12 existing hook cases passed |


The new Shift-after-append browser case reproduced a product defect: the middle
Model remained unchecked. `ModelBrowser.toggleSelect` mutated its anchor ref and
receipt map inside the functional state updater. React's development StrictMode
replays that updater; the second evaluation saw the clicked endpoint as its own
anchor, dropping the intervening range. Capture the gesture's anchor/range once
and update receipt refs outside the pure state transition. Preserve existing
single-toggle and absent-anchor behavior. No new selection store or abstraction
is introduced; browser RED trace and output are retained before this change.
The production startup measurements precede this event-handler-only correction;
no startup path or asset policy is changed by it.

## Integrated qualification

The corrected asset/tree gate passed141 tests across11 files (36.33s). The browser
gate passed2/2 (10.0s): thumbnail admission and the new range-selection regression.
Its first run was1 pass/1 failure; the failure preceded the selection correction.
The production-build, real-SQLite startup suite passed9/9 (1.2m), including offline
bootstrap, all four first-use forms, four stored-mode variants, early filters and
all90 unique Model identities after pagination. Distributed timing is qualified
separately; this functional rerun used the dense corpus.

[The performance comparison](frontend-m6-performance.md) records200 retained
observations and four discarded warmups with unchanged observer/inputs. All eight
current readiness series meet their original median/p95 reference budgets; dense
Spanish fresh-context median is effectively unchanged. Visible-image decode and
API contention improved in these samples, while distributed startup adds three
script requests. Host-load differences and unmeasured scenarios are explicit.

No framework, generic loader or duplicate remote store was introduced. The
selection fix makes React's state updater pure; the existing lease owner excludes
mounted resources from eviction and bounds inactive encoded bytes. A rollback of
the selection fix is independent of asset ownership; reverting assets must retain
session retirement and protected authorization. Final compatibility-export
removal remains the explicitly assigned M10 work.

After the selection correction, the complete ModelGrid mirror passed188/188
(65.80s). Frontend format, lint, app/UI/domain types and diff whitespace checks
passed. The production startup gate built the corrected application. The existing
large-chunk build warning remains; no coverage or final CI claim is made here.
