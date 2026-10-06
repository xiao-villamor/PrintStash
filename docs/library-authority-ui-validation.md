# Library freshness presentation

Displayed membership and order remain stable until explicit refresh. A rejected continuation is recoverable from the first page. Verified permission changes suppress private presentation before retiring its session scope. These rows qualify grid integration in addition to the authority owner's separate matrix.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | keeps displayed rows until explicit refresh | Happy | current revision differs from displayed page | original rows retained; refresh replaces them | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::keeps displayed rows until explicit refresh` |
| 2 | restarts a rejected continuation from the first page | Error | cursor answers browse_refresh_required | old rows retained; explicit refresh omits cursor | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::restarts a rejected continuation from the first page` |
| 3 | hides private content when authorization changes | Error | current authorization revision differs | Model and collection labels removed before verification callback | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::hides private content when authorization changes` |
| 4 | retries an unavailable authority check | Error | revision endpoint fails once | visible rows retained; retry clears error without browsing again | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retries an unavailable authority check` |
| 5 | refreshes a changed library deliberately in the browser | Happy | page r1 then authority r2 | list stays stable until button; replacement page starts fresh | Playwright | ✅ `tests/e2e/vault.spec.ts::refreshes a changed library deliberately` |

## Validation checkpoint

Grid and repository hygiene: 158 passed across 2 files. Authority and thumbnail owners: 35 passed across 2 files. Full Chromium vault suite: 26 passed. App/UI/domain typecheck, lint, formatting and whitespace checks passed for the integration checkpoint. The first four grid authority tests and two denied-projection cases failed before their production fixes. A pending Query publication test was corrected to wait for the confirmed visible thumbnail before releasing the older response.

No timing improvement is inferred from these correctness results. Production performance measurement remains pending.
