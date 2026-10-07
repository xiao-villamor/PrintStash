# M10 packages and platform qualification

Status: locally accepted after M9. Full migration qualification, review completeness and remote CI remain M11.

## First bounded step: static delivery and obsolete chunks

Inspected complete files: `frontend/public/sw.js`, `src/lib/pwa.ts`,
`src/lib/lazy-component.ts`, `tests/repo/service-worker.test.ts`,
`src/lib/__tests__/lazy-component.test.tsx`, `tests/e2e/pwa-production.spec.ts`,
`tests/e2e/pwa.spec.ts`, `playwright.config.ts`, `playwright.startup.config.ts`.
Also inspected dependency policy/tests and the transport compatibility entry points;
remaining consumers have not yet been qualified for removal.

The lazy-import latch is cleared by any successful lazy import, which can permit
repeated reloads when an unrelated chunk succeeds between failures. Keep one
automatic recovery per browser tab session; a persistent failure must surface.
The worker currently intercepts and remembers every same-origin non-API GET,
which exceeds its public-static ownership contract. Restrict it to navigation,
build assets and explicitly owned public files; never handle bearer requests.
Rollback these two changes independently without changing routing or deployment.
No new runtime, cache layer, locale split or framework is required.

## Behaviour matrix (before implementation)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| P1 | keeps recovery bounded across successful imports | Error | Retry used; another chunk succeeds; later chunk fails | Original failure surfaces without another reload | Frontend unit | ✅ `src/lib/__tests__/lazy-component.test.tsx::keeps recovery bounded across successful imports` |
| P2 | leaves non-shell reads to the browser | Error | Unknown same-origin resource or private API | Worker provides no response and creates no cached entry | Frontend unit | ✅ `tests/repo/service-worker.test.ts::leaves non-shell reads to the browser` |
| P3 | leaves authenticated static requests to the browser | Error | Bearer header on otherwise static path | Worker does not intercept or cache | Frontend unit | ✅ `tests/repo/service-worker.test.ts::leaves authenticated static requests to the browser` |
| P4 | delivers bootstrap when cache storage rejects | Error | Cache open/write failure | Network response remains usable | Frontend unit / Playwright | ✅ `tests/repo/service-worker.test.ts; tests/e2e/pwa-production.spec.ts::delivers bootstrap when cache storage rejects` |
| P5 | excludes private responses from worker caches | Error | Authenticated JSON/image; subsequent offline read | No private cache entry or offline replay | Playwright | ✅ `tests/e2e/pwa-production.spec.ts::excludes private JSON responses from worker caches; excludes private image bytes from worker caches; refuses offline replay of private responses` |
| P6 | delivers the production bootstrap offline | Happy | Installed production worker; network unavailable | Cached bootstrap delivered | Playwright | ✅ `tests/e2e/pwa-production.spec.ts::delivers the production bootstrap offline` |
| P7 | surfaces a persistently missing route chunk | Error | Production lazy asset unavailable after retry | Bounded reload and visible recovery error | Playwright | ✅ `tests/e2e/pwa-production.spec.ts::recovers a persistently missing route through explicit reload` |
| P8 | recovers a route after explicit reload | Happy | Chunk available again; operator reloads | Requested route renders successfully | Playwright | ✅ `tests/e2e/pwa-production.spec.ts::recovers a persistently missing route through explicit reload` |
| P9 | accepts published editing and Build interfaces | Happy | External component imports reviewed feature entry points | No boundary violation; other feature internals remain private | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::accepts published editing and Build interfaces` |

This step does not close M10: compatibility invalidation removal, package/extension
qualification, localization and complete ownership/review reconciliation remain.

## Static delivery checkpoint

Three new regressions failed before production changes (19 controls passed,
1.59 s). The worker now limits interception to navigation and owned public static
paths, rejects Authorization-bearing requests, and advances its shell cache to v6
so prior entries are retired. Lazy recovery permits one automatic reload per tab
session, including when unrelated modules succeed. The eagerly loaded RouteError
provides localized reload/back navigation without depending on the failed chunk.

The first three-file run passed 25 cases and failed one stale v5 literal in the
registration test; the literal now reflects the deliberate cache revision. The
expanded five-file gate passed 106 cases and failed the repository graph with
five existing imports from three undeclared feature interfaces. Full inspection
confirmed their intended public contracts; the manifest now registers those
specific interfaces. No blanket exception was added. The final dependency gate
passed 76 cases in 2.36 s (73 existing plus three interface cases), so the two
runs qualify 110 distinct cases across the five files.

Production Chromium: eight cases passed in 13.4 s, including the real shipped
worker, Cache Storage rejection, private-response/offline isolation, and a missing
Settings chunk followed by explicit recovery. Static-cache rejection is induced
at the browser storage boundary; the route test deliberately serves a missing
static asset and then restores delivery. Backend behavior is outside these tests.
App/UI/domain types and frontend lint passed; the production browser command built
the application successfully with the existing locale-shell/large-chunk warnings.
These are correctness results, not performance comparisons.

## Transport compatibility removal — next bounded step

`GetJsonOptions.fresh` has been a no-op since M1 removed transport caching. Remove
it from the contract and every first-party call; preserve AbortSignal and network
freshness. Query owns deduplication/freshness. The separate mutation invalidation
bridge is not removed in this step: its remaining callers need explicit owners.
Rollback is API-shape-only; never restore transport caching.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| T1 | returns fresh JSON on every transport read | Happy | Repeated endpoint reads | Second server result observed | Frontend unit | ✅ `request.test.ts` |
| T2 | keeps concurrent transport reads independent | Edge | Concurrent responses finish out of order | Each caller gets its own response | Frontend unit | ✅ `request.test.ts` |
| T3 | ignores an old session's 401 on a read | Error | Session replaced before response | Current identity remains; obsolete read rejected | Frontend unit | ✅ `request.test.ts` |
| T4 | requests network revalidation without a cache mode option | Happy | Plain JSON request | Browser no-store policy sent | Frontend unit | ✅ `request.test.ts` |
| T5 | cancels the canonical printer choice read on disposal | Edge | Observer unmounts during read | Signal aborted | Frontend unit | ✅ `queries.test.tsx` |

No-op freshness option removal qualified:124 tests across request, shared queries and config API passed in3.94s. Types passed; all callers preserve cancellation. No cache behavior changed.

## Explicit mutation effects (pre-implementation matrix)

Remove the transport-to-Query dependency and path parser only after consumers
own their effects. Tags and collections still depend on that bridge; printer
creation/deletion also does. Existing Model/Multipart publications, grid explicit
refresh, durable ingest completion, profiles, Inbox, settings and fleet owners
retain their policies. No HTTP URL is a cache invalidation contract.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| C1 | leaves Query state untouched after transport writes | Happy | JSON/form/action ACK | Seeded Query data stays fresh | Unit | ✅ `request.test.ts` |
| C2 | publishes a created tag to shared choices | Happy | Create tag ACK with two observers | Both show new tag | Unit | ✅ `taxonomy.test.tsx` |
| C3 | removes a confirmed tag from shared choices | Happy | Delete tag ACK | Choices and dependent model labels revalidate | Unit | ✅ `taxonomy.test.tsx` |
| C4 | revalidates collection projections after a confirmed change | Happy | Create/move/rename/delete/tags/readme ACK | Catalog and dependent metadata revalidate | Unit | ✅ `taxonomy.test.tsx` |
| C5 | preserves taxonomy after a failed command | Error | Server refuses write | No false confirmation or changed read | Unit | ✅ `taxonomy.test.tsx` |
| C6 | fences taxonomy publication after session replacement | Error | Pending command then logout | Replacement cache unchanged | Unit | ✅ `taxonomy.test.tsx` |
| C7 | refreshes printer choices after a confirmed catalog change | Happy | Create/delete ACK | Catalog/dashboard updated | Unit | ✅ `features/printers/__tests__/queries/catalog.test.tsx` |
| C8 | prohibits transport imports from Query | Error | Transport attempts Query dependency | Boundary diagnostic without migration exception | Repository | ✅ `dependency-boundaries.test.ts` |

Rollback this cutover as one owner/consumer change; do not restore TTL caching.
Delete legacy invalidation-only tests with the removed URL parser; replace their
product assertions at feature boundaries. Keep ingest completion tests.

Additional affected contracts identified while tracing bridge consumers:

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| C10 | preserves acknowledged detail while refreshing derived metadata | Happy | Model/Multipart ACK | Exact detail remains fresh; totals stale; controlled browse untouched | Unit | ✅ `metadata.test.ts` |
| C11 | leaves replacement-session metadata unchanged | Error | Old publication scope | New totals remain fresh | Unit | ✅ `metadata.test.ts` |
| C12 | refreshes fleet projections after queue commands | Happy | Queue ACK | Queue/summary projections become stale | Unit | ✅ `send-to-buttons.test.tsx` |
| C13 | revalidates spools after changing the connection | Happy | Confirmed Spoolman config | Spool choices come from current connection | Unit | ✅ `spoolman-connect-card.test.tsx` |

The first effect-owner selection passed95 tests but produced six unhandled errors:
the legacy printer test harness returned a Printer DTO for catalog GETs. The new
owner correctly reaches the provider's QueryClient; its fake must serve the actual
listing/dashboard contracts. This run is not accepted as green.

## Executable test entry points (requirements before tests)

A declared fast-lane member must exist and retain the isolated-process assumptions
of that lane. Every checked-in Playwright configuration needs a discoverable package
script or CI invocation; an excluded suite with no execution entry point silently
loses qualification. Restore the storage preset lane to the storage script and Deep
CI, and remove nonexistent old fast-lane paths. No production feature is added.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| Q1 | discovers every declared fast-lane member | Error | Current fast config | Every declared file exists | Repository | ✅ `test-entry-points.test.ts` |
| Q2 | keeps the shared-worker lane free of mutable global fixtures | Error | Fast-lane sources | No DOM/global mock/fake-timer fixture | Repository | ✅ `test-entry-points.test.ts` |
| Q3 | exposes an execution entry point for every browser configuration | Error | Checked-in Playwright configurations | Each appears in package scripts or CI | Repository | ✅ `test-entry-points.test.ts` |

## Integrated M10 results

The compatibility policy is removed: no transport Query import, no path parser,
no requestMutation alias and no invalidateApiCache setup calls. Feature owners
explicitly reconcile taxonomy, derived Library metadata, printer catalogs, fleet
and Spoolman readers. The dependency exception list is empty. The shared API
export facade remains an actively consumed endpoint interface, not a second owner.
React Router navigation adapters also remain deliberately under the chosen router.

Validation invocations (overlap is intentional; do not add counts):

- Transport regression:3 genuine RED failures before removal; final request and
  dependency selection144 passed with one missing test-header convention failure.
  Corrected that header; repository hygiene/entry-point selection7/7 passed.
- API and migrated command owners:888 tests in60 files passed,53.88s.
- Taxonomy/metadata/printer consumers: initial95 passes with6 malformed test-GET
  errors; corrected listing/dashboard responses. A subsequent selection passed55
  but had one remaining overridden fake and a parse error in a new test helper.
  Corrected both; final printer/send selection32 passed,8.66s. The other affected
  selections include the10 taxonomy,3 metadata,2 printer-catalog and19 Spoolman cases.
- Library/grid/Multipart/Inbox:304 passed and3 duplicate-read regressions failed.
  Explicit grid refresh no longer starts a duplicate taxonomy read; all14 refresh
  cases passed11.90s after correction. Detail/upload consumers93 passed22.17s.
- Workspace UI199 and domain78 tests passed. Extension272 tests/13 files passed;
  its production source is unchanged from the earlier three-browser build checkpoint.
- Browser proxy flows2/2 passed8.0s (printer list and collection Back). Restored
  storage preset lane1/1 passed1.3m, including real WebDAV source persistence.
- Entry-point regressions3 genuine REDs, then7 repository cases passed. Removed5
  nonexistent fast members and the localStorage-mutating error test from that lane;
  its authoritative isolated suite remains intact. Fast lane68 cases passed with
  shuffle seed20261007; the repeat with seed20261008 also passed68/68 in0.80s.
- App/UI/domain types and frontend lint passed. Formatting passed790 files and the production build passed1.63s on the integrated
  tree; existing chunk warnings are
  not performance evidence.

Prior static-delivery and extension browser matrices remain applicable; this
cutover changes neither their runtime policy nor their independently qualified
production source. M11 still owns full review-ledger reconciliation, comparable
performance observations and final PR checks. No performance improvement is claimed.
