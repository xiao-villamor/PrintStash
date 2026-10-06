# Ingest completion refresh validation

Scope: `refreshVaultAfterIngest` in `frontend/src/lib/query-client.ts` and its mirror. Ingest completion must cancel old reads before refreshing current projections. Session retirement takes priority over that continuation: an earlier completion cannot reset or invalidate the next private scope after waiting for cancellation.

Source trace found an unguarded await between cancellation and publication. Both retirement regressions executed RED: the replacement outliner projection was erased; the same-session control passed. The caller remains responsible for accepting a completion from its own current job/session. This increment fences the helper's internal continuation and does not alter the transport compatibility invalidator.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | preserves replacement-session projections after delayed ingest cancellation | Edge | Hold cancellation, retire login, populate replacement cache, release | Replacement outliner pages remain present; replacement model projection remains fresh | Frontend unit | ✅ `src/lib/__tests__/query-client.test.ts::preserves replacement-session projections after delayed ingest cancellation` |
| 2 | preserves replacement-permission projections after delayed ingest cancellation | Edge | Hold cancellation, retire access scope, populate authorized replacement cache, release | Replacement outliner pages remain present; replacement model projection remains fresh | Frontend unit | ✅ `src/lib/__tests__/query-client.test.ts::preserves replacement-permission projections after delayed ingest cancellation` |
| 3 | refreshes current-session projections after delayed ingest cancellation | Happy | Hold cancellation without retiring scope, release | Old outliner pages removed; model projection invalidated | Frontend unit | ✅ `src/lib/__tests__/query-client.test.ts::refreshes current-session projections after delayed ingest cancellation` |

Migration: retain the public helper and add the existing session-incarnation fence at the asynchronous boundary. Rollback is limited to this helper and its regression mirror. No backend, API, URL, UI, or dependency change is required. Validation and observed reproduction will be recorded after execution.

Validation: the complete QueryClient and session-transport mirrors passed 39/39 in 2.63 seconds after the fence. Dependency-boundary and suite-hygiene gates passed 77/77 in 3.68 seconds. Full app/UI/domain typecheck, full lint, and formatting of both changed TypeScript files passed. The first focused command also named a nonexistent hygiene path; its output correctly contained only two test files. Hygiene was then run under its actual `suite-hygiene.test.ts` path with the dependency gate. No timing, browser, or full migration acceptance is inferred from this helper regression.
