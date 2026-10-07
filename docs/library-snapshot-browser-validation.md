# Library snapshot browser validation

This bounded acceptance pass covers the browser gaps left after component-level snapshot and session tests. It uses real Chromium, the production React application, and the existing mock HTTP server. HTTP responses are held at Playwright routes; the tests never replace rendered DOM or invoke private cache/session functions.

## Coverage plan

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | keeps one coherent snapshot during rapid collection navigation | Edge | A displayed; B then C requested; lookup, children and browse responses released out of order | Heading, breadcrumb, collection tags, README, child folder and Model card remain A until C is complete; late B cannot replace C; DOM observer sees no mixed snapshot | Playwright | ✅ `tests/e2e/library-snapshot.spec.ts` — named case in Behaviour column |
| 2 | retires private Library history when signing out | Edge | A then C displayed; C pagination held; real Log out control activated | Old private content disappears before logout acknowledgement; late page and browser Back cannot resurrect it; DOM observer sees no reappearance | Playwright | ✅ `tests/e2e/library-snapshot.spec.ts` — named case in Behaviour column |
| 3 | retires private Library history when access changes | Error | A then C displayed; C pagination held; revision endpoint reports a new authorization revision; access revalidation held | Old private content disappears before revalidation completes; late page and browser Back cannot resurrect it after access removal; DOM observer sees no reappearance | Playwright | ✅ `tests/e2e/library-snapshot.spec.ts` — named case in Behaviour column |

## Real production boundaries

Navigation uses the Library Recent menu. Its existing persisted preference contains only fixture collection paths/labels. It does not mutate router history or call internal navigation functions.

Session retirement uses the top-bar Log out menu item, which invokes `AuthProvider.logout`, `clearLogin`, then `POST /api/v1/auth/logout`. The test holds that POST to distinguish synchronous private-state retirement from successful server acknowledgement.

Access retirement uses the actual foreground revision poll. A changed `authorization_revision` reaches `useLibraryAuthority`, which calls `retirePrivateSessionScope` and refreshes `/auth/me`. The test holds revalidation, then returns the same verified account while collection lookup returns 404 and browse is empty under the replacement authority. No synthetic auth endpoint or production test bridge is involved.

Mock HTTP verifies frontend retirement only; backend permission enforcement and cookie issuance remain the responsibility of the existing backend/real-backend tests. These browser assertions do not contribute to Vitest coverage percentages.

## Validation

Prepared command (run only in the coordinated browser window):

```sh
pnpm exec playwright test tests/e2e/library-snapshot.spec.ts --project=chromium --workers=1 --retries=0 --trace=on
```

Initial runtime result: 3/3 passed in 21.6 seconds (Chromium, serial, no retries). Grouping-only cleanup will be verified with the remaining M5 acceptance cases.

## Remaining M5 history acceptance (planned before authoring)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 4 | keeps independent reading positions for repeated Library URLs | Edge | Two distinct history entries share the same collection URL; each leaves at a different reading position | Back/Forward restores the position belonging to that entry, without borrowing the other entry's bookmark | Playwright | ✅ `tests/e2e/library-snapshot.spec.ts::keeps independent reading positions for repeated Library URLs` |
| 5 | restores a paged Library reading position through real detail navigation | Happy | Real SQLite collection with more than one browse page; desktop grid and mobile list | Detail Back restores the same URL, history entry and visible anchor geometry; Forward/Back repeats that restoration | Real-backend Playwright | ✅ `tests/e2e-real/library-navigation.spec.ts::restores a paged Library reading position through real detail navigation` (grid/list) |


M5 browser reconciliation: the serial combined run passed the existing 15
navigation cases and the three snapshot cases (18 passed), with the new same-URL
probe failing its position expectation. That probe left through the Recent menu,
which can move focus/scroll during departure; the earlier position did not isolate
the history contract. The replacement uses the real detail link and header Library
link to create the second visit. Its first run exposed a fixture URL mismatch:
the header restores collection plus stored preferences, not the detail's sort
query. Using the existing default sort for both visits produces the same URL.
The corrected test passed (8.0 seconds; 10.8 seconds total), asserting separate
Router keys and each visit's geometry through Back/Forward. No production change
was needed for this acceptance case. The two failed invocations remain recorded;
neither is counted as a reproduced product defect.


Real-backend result: both desktop-grid and mobile-list cases passed, 13.9 and
10.3 seconds respectively, in a 60.0-second invocation including startup.
FastAPI used a disposable SQLite instance and the existing 502-Model scale
factory. The actual Load more control appended a second page; real Model detail,
Back, Forward and Back preserved URL, entry key and geometry. Each case removed
its unique fixture subtree. These timings describe test execution, not product
performance. Browser lint and formatting passed for all three touched specs.
