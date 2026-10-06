# Conditional library editing validation

M4 requirements recorded before conditional writer migration.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | sends the editor's Model version | Happy | Model v7 edited | conditional-v1 and exact If-Match at HTTP boundary | Frontend unit | ✅ `src/lib/api/__tests__/models/model.test.ts::sends the editor's Model version` |
| 2 | sends the editor's Multipart version | Happy | Multipart v7 composition edited | exact If-Match at HTTP boundary | Frontend unit | ❌ missing |
| 3 | preserves a Model draft on edit conflict | Error | another editor saved first | draft retained; explicit latest-version review | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::preserves a Model draft on edit conflict` |
| 4 | preserves a Multipart draft on edit conflict | Error | another editor saved first | composition retained; no automatic retry | Frontend unit | ❌ missing |
| 5 | rejects undo after another writer changed the Model | Error | acknowledged batch version now stale | conflict preserved without overwriting current value | Frontend unit | ❌ missing |
| 6 | confirms an ambiguous Model save before retry | Error | save response lost | authoritative read; no blind second write | Playwright | ❌ missing |
| 7 | keeps an edited Model draft through refetch | Edge | derivative refresh during editing | original edit version and draft preserved | Frontend unit | ❌ missing |
| 8 | saves a retained Model draft only after explicit version review | Happy | reviewed v7 | one intentional retry with v7; saved draft shown | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::saves a retained Model draft only after explicit version review` |
| 9 | replaces a Model draft with the reviewed version | Happy | user chooses latest | editor uses latest fields without a write | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::replaces a Model draft with the reviewed version` |
| 10 | keeps a conflicted Model draft when review is dismissed | Edge | user keeps draft | fields retained; save remains blocked | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::keeps a conflicted Model draft when review is dismissed` |
| 11 | requires another review when a reviewed version becomes stale | Error | concurrent write after review | old review closes; draft remains blocked until a new read | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::requires another review when a reviewed version becomes stale` |
| 12 | prevents retry after review reveals lost edit access | Error | latest effective role is view | draft preserved; retry disabled | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::prevents retry after review reveals lost edit access` |
| 13 | reviews a conflicting Model before intentional retry in the browser | Happy | first save conflicts; latest version is v7 | typed draft retained; explicit retry carries v7 and displays confirmed response | Playwright | ✅ `tests/e2e/model-detail.spec.ts::reviews a conflicting Model before intentional retry` |

## Checkpoint validation

Model metadata conflict UI: 66 tests across the Model detail, Model wire, and suite-hygiene files passed; the Chromium conflict-review flow passed (1 test). Frontend lint, typecheck (app and packages), and formatting passed. Two additional conflict-review regressions failed before their production fixes.

This is a partial M4 checkpoint: the optional version argument remains for unmigrated callers. Multipart writers, batch undo, ambiguous responses, and detail remote-state ownership still require the remaining rows and migration work. No blanket conditional-edit guarantee is claimed.
