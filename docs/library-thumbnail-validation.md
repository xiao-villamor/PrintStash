# Thumbnail arrival projection

Source inspection found thumbnail subscription acknowledgments invalidating whole browse pages. The initial browser regression fixture incorrectly attributed two development startup requests to this mechanism; request tracing corrected that fixture to measure after rows are displayed. The projection owner must update only missing thumbnail URLs, using a bounded authorized read, while membership, order and edit metadata stay owned by the browse snapshot.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | patches only the requested missing thumbnails | Happy | mixed ordered page; new thumbnail | same row order/names/cursor; thumbnail URL appears; no browse refetch | Frontend unit | ✅ `src/features/library/__tests__/thumbnails.test.tsx::patches only the requested missing thumbnails` |
| 2 | preserves an already confirmed thumbnail | Edge | thumbnail changes while projection request is pending | newer nonempty URL survives older projection | Frontend unit | ✅ `src/features/library/__tests__/thumbnails.test.tsx::preserves an already confirmed thumbnail` |
| 3 | retires presentation when projection authority differs | Error | valid different authorization revision | private rows hidden; cache retired | Frontend unit | ✅ `src/features/library/__tests__/thumbnails.test.tsx::retires presentation when projection authority differs` |
| 4 | ignores a projection from a retired session | Error | session changes while response pending | no private rows restored | Frontend unit | ✅ `src/features/library/__tests__/thumbnails.test.tsx::ignores a projection from a retired session` |
| 5 | bounds a projection to 24 missing thumbnails | Edge | 25 Models missing previews | at most24 deduplicated ids requested | Frontend unit | ✅ `src/features/library/__tests__/thumbnails.test.tsx::bounds a projection to 24 missing thumbnails` |
| 6 | rejects a malformed projection without revoking access | Error | response lacks valid authorization token | no private retirement; no URL publication | Frontend unit | ✅ `src/features/library/__tests__/thumbnails.test.tsx::rejects a malformed projection without revoking access` |
| 7 | thumbnail subscriptions preserve the displayed browser list | Happy | subscribed event while library revision changed | only explicit refresh replaces rows | Playwright | ✅ `tests/e2e/vault.spec.ts::refreshes a changed library deliberately` |
| 8 | retires a refused thumbnail projection | Error | projection403 or authority-change409 | cached private presentation removed | Frontend unit | ✅ `src/features/library/__tests__/thumbnails.test.tsx::retires a refused thumbnail projection` |

## Validation checkpoint

Grid and repository hygiene: 158 passed across 2 files. Authority and thumbnail owners: 35 passed across 2 files. Full Chromium vault suite: 26 passed. App/UI/domain typecheck, lint, formatting and whitespace checks passed for the integration checkpoint. The first four grid authority tests and two denied-projection cases failed before their production fixes. A pending Query publication test was corrected to wait for the confirmed visible thumbnail before releasing the older response.

No timing improvement is inferred from these correctness results. Production performance measurement remains pending.
