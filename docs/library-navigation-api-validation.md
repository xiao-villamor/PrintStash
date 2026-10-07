# Navigation API validation

The application navigation wrapper exposes React Router push, replace, Back and
Forward. This cleanup removes ignored scroll arguments and no-op route refresh
and prefetch methods. URL construction, library reading-position ownership,
Query prefetch owners, presentation and scroll policy stay unchanged.

Production manifest: `src/lib/navigation.ts`, `src/components/model-grid.tsx`,
`src/components/model-card.tsx`, `src/components/model-detail/index.tsx`,
`src/components/library-search.tsx`, `src/pages/search.tsx`, and
`src/components/settings-panel.tsx` (all below `frontend/`).

The matrix was prepared before test edits. Existing coverage was audited; missing
history and deletion destination assertions were identified explicitly. Paths in
Status are relative to `frontend/`.

| #   | Behaviour (test name)                                                  | Category | Precondition / input                                        | Observable outcome asserted                               | Tier          | Status                                                                                                                    |
| --- | ---------------------------------------------------------------------- | -------- | ----------------------------------------------------------- | --------------------------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------- |
| 1   | keeps router identity stable across consumer rerenders                 | Edge     | Consumer rerender without navigation                        | Same router object                                        | Frontend unit | ✅ `src/lib/__tests__/navigation.test.tsx::keeps router identity stable across consumer rerenders`                        |
| 2   | pushes a destination onto history                                      | Happy    | Existing /library entry; push /models/7?tab=files#preview   | Exact destination; Back restores /library                 | Frontend unit | ✅ `src/lib/__tests__/navigation.test.tsx::pushes a destination onto history`                                             |
| 3   | replaces the current history entry                                     | Happy    | /earlier then /library; replace /search?q=bracket           | Exact replacement; Back reaches /earlier                  | Frontend unit | ✅ `src/lib/__tests__/navigation.test.tsx::replaces the current history entry`                                            |
| 4   | returns to the previous history entry                                  | Happy    | /library then /models/7                                     | Back shows /library                                       | Frontend unit | ✅ `src/lib/__tests__/navigation.test.tsx::returns to the previous history entry`                                         |
| 5   | advances to the next history entry                                     | Happy    | /library before /models/7                                   | Forward shows /models/7                                   | Frontend unit | ✅ `src/lib/__tests__/navigation.test.tsx::advances to the next history entry`                                            |
| 6   | keeps the current route at the history boundary                        | Edge     | Back at first entry; Forward at last entry                  | Location stays on the only entry                          | Frontend unit | ✅ `src/lib/__tests__/navigation.test.tsx::keeps the current route at the $direction history boundary`                    |
| 7   | returns to the deleted Model destination                               | Happy    | Confirmed deletion; collection parts/tools or no collection | Exact encoded collection URL or root                      | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::returns to the deleted Model $label destination`                |
| 8   | stays on the page when the delete is refused                           | Error    | DELETE rejected with 409                                    | Error toast; detail URL remains /models/1                 | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::stays on the page when the delete is refused`                   |
| 9   | opens the Model detail from its card                                   | Happy    | Visible ModelCard; activate Model link                      | Location is /models/1                                     | Frontend unit | ✅ `src/components/__tests__/model-card.test.tsx::opens the Model detail from its card`                                   |
| 10  | does not convert on hover                                              | Edge     | ModelCard with mesh; pointer hover                          | No STL conversion HTTP request                            | Frontend unit | ✅ `src/components/__tests__/model-card.test.tsx::does not convert on hover`                                              |
| 11  | opens a collection from its grid card                                  | Happy    | Library grid collection card                                | Chosen collection is rendered                             | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::opens a collection from its grid card`                                  |
| 12  | does not add history when the current collection breadcrumb is clicked | Edge     | Current collection breadcrumb                               | Back reaches preceding collection                         | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::does not add history when the current collection breadcrumb is clicked` |
| 13  | writes a sort choice into the URL                                      | Happy    | Grid sort selection                                         | URL contains chosen sort                                  | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::writes a sort choice into the URL`                                      |
| 14  | restores URL-owned mode on history navigation                          | Edge     | Navigation between grid modes                               | History restores selected mode                            | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::restores URL-owned mode on history navigation`                          |
| 15  | follows the document tab through browser history                       | Edge     | Navigate through documents tab                              | History restores document tab                             | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::follows the document tab through browser history`                       |
| 16  | puts the view's filters into the URL when it is chosen                 | Happy    | Choose saved library view                                   | URL carries saved filters                                 | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::puts the view's filters into the URL when it is chosen`                 |
| 17  | clears all filters while retaining sort                                | Edge     | Filtered library with chosen sort                           | Cleared filter route retains sort                         | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::clears all filters while retaining sort`                                |
| 18  | warms a folder hovered in the list view                                | Happy    | Hover collection in list                                    | Real Query prefetch warms destination                     | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::warms a folder hovered in the list view`                                |
| 19  | returns to the library when clearing a submitted search                | Happy    | Submitted search; clear                                     | Root library URL                                          | Frontend unit | ✅ `src/components/__tests__/library-search.test.tsx::returns to the library when clearing a submitted search`            |
| 20  | clears only the library query                                          | Edge     | Library query plus filters; clear                           | Filters preserved in URL                                  | Frontend unit | ✅ `src/components/__tests__/library-search.test.tsx::clears only the library query`                                      |
| 21  | keeps Enter in the library with existing filters                       | Happy    | Library lexical search; Enter                               | Query URL preserves library filters                       | Frontend unit | ✅ `src/components/__tests__/library-search.test.tsx::keeps Enter in the library with existing filters`                   |
| 22  | opens AI results from the search bar                                   | Happy    | Enabled AI search; submit                                   | Submitted search URL                                      | Frontend unit | ✅ `src/components/__tests__/library-search.test.tsx::opens AI results from the search bar`                               |
| 23  | preserves the selected search mode when changing sort                  | Happy    | Search results; sort selection                              | Request preserves search mode                             | Frontend unit | ✅ `src/pages/__tests__/search.test.tsx::preserves the selected search mode when changing sort`                           |
| 24  | applies Subject filters through the canonical URL                      | Happy    | Search results; Subject filter                              | Canonical Subject query                                   | Frontend unit | ✅ `src/pages/__tests__/search.test.tsx::applies Subject filters through the canonical URL`                               |
| 25  | moves the section into the URL when one is chosen                      | Happy    | Settings overview; choose Trash                             | Trash section renders                                     | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::moves the section into the URL when one is chosen`                  |
| 26  | encodes a complete return view in native item links                    | Happy    | Library origin with filters; Model anchor                   | Native href contains complete encoded return URL          | Frontend unit | ✅ `src/features/library/__tests__/navigation.test.tsx::encodes a complete return view in native item links`              |
| 27  | preserves modified-link navigation                                     | Edge     | Ctrl-click, Meta-click, middle-click                        | Native event remains unprevented; current route unchanged | Frontend unit | ✅ `src/features/library/__tests__/navigation.test.tsx::preserves modified-link navigation %j`                            |

## Validation runs

This section preserves the original worker checkpoint. Final integrated M5
acceptance is recorded below; no push or PR is implied by local qualification.

All Vitest commands used `--maxWorkers=1 --no-file-parallelism` from `frontend/`:

- The three edited mirrors (`src/lib/__tests__/navigation.test.tsx`,
  `src/components/__tests__/model-card.test.tsx`,
  `src/components/model-detail/__tests__/index.test.tsx`) plus the existing native
  link contract (`src/features/library/__tests__/navigation.test.tsx`): **4 files,
  99 tests passed**, 30.35 seconds. The native modified-link cases emit three
  jsdom `Not implemented: navigation to another Document` diagnostics; the run
  completed without test failures or unhandled errors.
- `src/components/__tests__/library-search.test.tsx` and
  `src/pages/__tests__/search.test.tsx`: **2 files, 52 tests passed**, 12.10 seconds.
- Named navigation regressions in `src/components/__tests__/model-grid.test.tsx`
  and `src/components/__tests__/settings-panel.test.tsx`: **2 files, 10 tests
  passed**, 11.08 seconds. Vitest reported **321 skipped** from name-filter
  deselection; no skips were added. The selection covered grid collection entry,
  current breadcrumb history, sort URL, mode history, document-tab history,
  saved-view filters, clearing filters, real Query prefetch on list folder hover
  and focus, and settings section selection.

Total: **161 tests passed**. The matrix has 27 covered rows and no missing rows.

Static checks:

- `pnpm exec oxfmt --check` naming the seven production files and three edited
  test mirrors: passed, **10 files**.
- `pnpm lint`: passed, **zero diagnostics**.
- `pnpm typecheck`: passed for the application, `@printstash/ui` and
  `@printstash/domain`.
- Source audit found no remaining `NavOptions`, ignored `scroll` arguments,
  `router.refresh` or `router.prefetch` references under `frontend/src`.

A documentation-formatting invocation from `frontend/` initially rejected the
parent-relative path: `PATH must not contain ".."`. Re-running the formatter from
the repository root with `docs/library-navigation-api-validation.md` succeeded;
this was a command-path error, not a code or test failure.


## Integrated M5 acceptance

The final navigation/entry/reading/card/detail/search/grid/hygiene gate passed
368 tests across 10 files in99.78 seconds. Full lint, formatting and app/UI/domain
types passed. The browser acceptance covers 19 mock cases and two real-backend
cases, with the initial new-fixture failures explicitly reconciled in the
[M5 closure record](frontend-m5-closure-validation.md). No production performance
claim follows from these checks.
