# Library filter ownership validation

This checkpoint gives Library URL filters one feature owner. The existing Library location owner continues to resolve mode, sort and document/model section; the Search codec retains its separate URL contract. ModelBrowser consumes one effective filter projection for browse/facets, controls, saved-view creation and comparison. Folder navigation continues to push history; filter changes and canonical repair replace the current entry with `scroll: false`.

The Library owner accepts positive, safe integer printer identifiers in decimal digit form. `backend/app/schemas/models.py` declares `ModelFilters.printer_id` with `gt=0`; the browser additionally requires a safe integer so a URL cannot silently round a distinct identity. `backend/app/modules/library/model_views/browse.py` rejects printer constraints for a non-superuser, and `model_views/filters.py` gives an explicit printer identifier precedence over presence. Effective member filters therefore omit both printer constraints. Viewing an existing saved view never rewrites its stored DTO; new saves and duplicates use the effective authorized filter state.

The owner filters closed enum values and accepts only `yes`/`no` for optional booleans. Invalid controlled values are removed by canonical replacement, preserving unrelated parameters. Free-text repeated values retain order and duplicates. Existing upload date values and history parsing remain unchanged; this checkpoint does not broaden date/range validation or change Search defaults.

## Intended interface

`features/library/filters.ts` reads URL parameters plus resolved Library location and printer authorization. It returns effective saved-view filters, structured control values, model request filters and canonical parameters. Saved-view serialization and semantic comparison reuse that normalization. No server read, React hook, generic repository or navigation side effect belongs in this module.

Owned paths: the new owner and its adjacent test, ModelBrowser and its adjacent test, one public dependency-manifest entry, and this validation document. Existing Library URL fixtures and real saved-view headline are reused for regression evidence.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | preserves accepted repeated filters | Happy | Enum and free-text arrays, tags with duplicates | Accepted order and duplicates survive projections | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::preserves accepted repeated filters` |
| 2 | omits unknown enum values | Edge | Valid and unknown file/status/outcome/storage values | Only accepted values reach URL, controls and request | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::omits unknown enum values` |
| 3 | decodes optional boolean filters | Happy | yes/no printed and similarity values | Explicit true/false reach request and saved filters | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::decodes optional boolean filters` |
| 4 | omits malformed optional booleans | Edge | Unknown printed or similarity values | No false constraint is invented | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::omits malformed optional booleans` |
| 5 | accepts positive safe printer identities | Happy | Decimal printer IDs including safe upper bound | Exact identity reaches request and saved filters | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::accepts positive safe printer identities` |
| 6 | removes invalid printer identities | Edge | Empty, zero, negative, decimal, exponent, nonnumeric, unsafe IDs | Canonical URL and effective projections omit identifier | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::removes invalid printer identities` |
| 7 | gives an explicit printer precedence | Edge | Valid printer ID plus presence | Only the explicit identity is effective | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::gives an explicit printer precedence` |
| 8 | retains presence after an invalid printer identity | Edge | Invalid ID plus valid presence | Valid presence remains effective | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::retains presence after an invalid printer identity` |
| 9 | removes inaccessible printer constraints | Edge | Member reads admin bookmark | Requests, controls and new-save projection omit constraints | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::removes inaccessible printer constraints` |
| 10 | preserves a stored admin view when a member applies it | Edge | Member applies saved printer view | DTO unchanged; browse request authorized; no PATCH | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::preserves a stored admin view when a member applies it` |
| 11 | saves only effective member filters | Edge | Member opens printer bookmark then saves | POST omits inaccessible printer constraints | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::saves only effective member filters` |
| 12 | duplicates only effective member filters | Edge | Member duplicates saved printer view | New POST omits constraints; original DTO remains unchanged | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::duplicates only effective member filters` |
| 13 | scopes folder requests without a search | Happy | Folder path, blank query | Collection retained; direct children requested | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::scopes folder requests without a search` |
| 14 | searches recursively with a trimmed query | Happy | Folder path, padded query | Trimmed q; direct false; same saved projection | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::searches recursively with a trimmed query` |
| 15 | preserves history filter parsing | Edge | Dates, zero minimum, invalid and bounded durations | Existing date and duration semantics retained | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::preserves history filter parsing` |
| 16 | round trips complete Library filters | Happy | Complete accepted saved filter payload | Serialize/read preserve effective filters and date-desc contract | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::round trips complete Library filters` |
| 17 | compares equivalent saved filters | Edge | Optional absence, tag order, implicit defaults | Equivalent views are not marked modified | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::compares equivalent saved filters` |
| 18 | detects an effective filter change | Happy | Any effective filter changes | Selected saved view marked modified | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::detects an effective filter change` |
| 19 | preserves unrelated URL parameters during repair | Edge | Invalid filter alongside upload/docs/unknown params | Repair changes only controlled invalid filters | Frontend unit | ✅ `src/features/library/__tests__/filters.test.ts::preserves unrelated URL parameters during repair` |
| 20 | replaces a malformed printer bookmark | Edge | Invalid printer ID with history probe | Canonical URL repaired without a new history entry | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::replaces a malformed printer bookmark` |
| 21 | shares effective filters across Library requests | Happy | Accepted filters in URL | Browse and facet requests carry matching effective filters | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::shares effective filters across Library requests` |
| 22 | preserves URL mode and sort contracts | Edge | Explicit/default/legacy modes and sorts | Existing Library URL fixtures pass unchanged | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts` (all 11 cases) |
| 23 | preserves folder history navigation | Happy | Folder push, filter replace, Back | Existing folder navigation assertions pass unchanged | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::moving between folders` and `collection navigation` |
| 24 | preserves the real saved-view lifecycle | Happy | Save, reload, apply, rename, duplicate, delete | Existing real browser headline restores canonical URL | Playwright | ✅ `tests/e2e-real/saved-views.spec.ts::save current filters as a view, apply it restores the canonical URL` |

## Validation

The matrix preceded tests and production changes. A corrected focused RED reproduced five defects: malformed booleans executed false constraints, invalid printer identities reached browse, member views displayed inaccessible printer chips, and member saves/duplicates retained those constraints. One initial test setup passed an auth factory instead of invoking it; that fixture was corrected before the genuine RED pass.

- Focused new owner/component behavior: 33 passed.
- Whole ModelBrowser, owner and existing Library URL fixtures: 203 passed (3 files).
- App, UI and domain TypeScript checks: passed after removing an unsupported test-only query option.
- Full frontend lint: passed.
- Owned formatting: passed (5 source/test/manifest files).
- Dependency boundaries: 73 passed. Suite hygiene requested the new test file's contract comment; it was added, and all 4 hygiene checks passed.
- Real-backend saved-view lifecycle: passed (31.5 seconds; 1.6 minutes with a fresh backend). The run reused the verified official BGCODE converter through the supported executable setting; startup and test timeouts were unchanged.

Full repository integration and required remote CI remain the parent task's responsibility.
