# M10 packages and platform qualification

Status: active by explicit user direction after the notification checkpoint
`f7c6abb4`. M9 remains open; advancing this work does not waive its acceptance
criteria. M11 must reconcile that prerequisite before final completion.

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
