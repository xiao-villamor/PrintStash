# Ordered library client validation

M3 ordered client and grid cutover. Authority revalidation remains pending. Requirements precede tests.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | sends the browse scope to the server | Happy | mode/filter/order/cursor | exact scoped request and returned revision | Frontend unit | ✅ `src/lib/api/__tests__/library-browse.test.ts::sends the browse scope to the server` |
| 2 | reads the lightweight authority revision | Happy | revision request | both authoritative revisions returned | Frontend unit | ✅ `src/lib/api/__tests__/library-browse.test.ts::reads the lightweight authority revision` |
| 3 | appends the server sequence unchanged | Happy | mixed types across two pages | visible sequence matches server order | Frontend unit | ✅ `src/features/library/__tests__/browse.test.tsx::appends the server sequence unchanged` |
| 4 | continues after an empty page | Edge | empty items plus next_cursor | following page is reachable | Frontend unit | ✅ `src/features/library/__tests__/browse.test.tsx::continues after an empty page` |
| 5 | retains displayed pages when continuation requires refresh | Error | next page returns 409 | existing results visible; refresh required | Frontend unit | ✅ `src/features/library/__tests__/browse.test.tsx::retains displayed pages when continuation requires refresh` |
| 6 | keeps one continuation in flight | Edge | double load while response pending | one request; pending signal remains active | Frontend unit | ✅ `src/features/library/__tests__/browse.test.tsx::keeps one continuation in flight` |
| 7 | isolates page sizes in the cache | Edge | two different page sizes | each receives its own page | Frontend unit | ✅ `src/features/library/__tests__/browse.test.tsx::isolates page sizes in the cache` |

First run failed to resolve the new modules (no behaviour executed); this is not
reported as a reproduced production failure. After implementation: 7/7 client
behaviours passed; 89/89 passed in the combined transport/auth/browse lane.
Authority revalidation and full integration are still pending. No server-order or performance completion claim follows from
these focused client tests.

## Grid cutover requirements (before implementation)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 8 | preserves server order when another mixed page arrives | Happy | Zeta then Älpha across pages | existing card stays before appended card | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::preserves server order when another mixed page arrives` |
| 9 | reaches matching results after an empty browse page | Edge | empty page with continuation | Load more reaches matching Model | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::reaches matching results after an empty browse page` |
| 10 | keeps one coherent folder result while the destination loads | Edge | destination page pending | previous folder and cards remain together | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::keeps one coherent folder result while the destination loads` |

| 11 | preserves mixed order through browser pagination | Happy | mixed two-page response | original card stays before appended card | Playwright | ✅ `tests/e2e/vault.spec.ts::preserves mixed order through browser pagination` |
| 12 | continues an empty browse page in the browser | Edge | empty page plus cursor | next page becomes visible | Playwright | ✅ `tests/e2e/vault.spec.ts::continues an empty browse page in the browser` |

Grid validation: 156/156 tests in three files; mock-API Chromium vault 23/23.
The two new component cases first failed against the old separate-list consumer;
after cutover both passed. This confirms the new endpoint is consumed and the
empty-page continuation is reachable; endpoint filtering/order is separately
qualified by backend tests. Query pages now sit under the existing Models key
so legacy successful mutation invalidation still reaches this migration slice.
Conditional publication, controlled refresh, authorization retirement and history
snapshots remain subsequent steps; this commit does not claim their completion.

Removed: separate unbounded Multipart grid fetch, client mixed-card sorting and
offset-based select-all enumeration. Folder prefetch now fills the same ordered
query as navigation; coherent snapshots retain its ordered discriminated items.
