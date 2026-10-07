# Conditional library editing validation

M4 requirements recorded before conditional writer migration.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | sends the editor's Model version | Happy | Model v7 edited | conditional-v1 and exact If-Match at HTTP boundary | Frontend unit | ✅ `src/lib/api/__tests__/models/model.test.ts::sends the editor's Model version` |
| 2 | sends the editor's Multipart version | Happy | Multipart v7 composition edited | exact If-Match at HTTP boundary | Frontend unit | ✅ `src/lib/api/__tests__/multipart-models.test.ts::saves the complete multipart draft atomically` |
| 3 | preserves a Model draft on edit conflict | Error | another editor saved first | draft retained; explicit latest-version review | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::preserves a Model draft on edit conflict` |
| 4 | preserves a Multipart draft on edit conflict | Error | another editor saved first | composition retained; no automatic retry | Frontend unit | ✅ `src/components/__tests__/multipart-model-browser.test.tsx::preserves a Multipart draft on edit conflict` |
| 5 | retains a concurrent change when undo conflicts | Error | acknowledged batch version now stale | conflict preserved without overwriting current value | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::retains a concurrent change when undo conflicts` |
| 6 | confirms an ambiguous Model save before retry | Error | save response lost | authoritative read; no blind second write | Playwright | ✅ `tests/e2e/model-detail.spec.ts::confirms an ambiguous Model save before retry` |
| 7 | preserves the editing base across a background refresh | Edge | authorized refresh during editing | original edit version and draft preserved | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::preserves the editing base across a background refresh` |
| 8 | saves a retained Model draft only after explicit version review | Happy | reviewed v7 | one intentional retry with v7; saved draft shown | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::saves a retained Model draft only after explicit version review` |
| 9 | replaces a Model draft with the reviewed version | Happy | user chooses latest | editor uses latest fields without a write | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::replaces a Model draft with the reviewed version` |
| 10 | keeps a conflicted Model draft when review is dismissed | Edge | user keeps draft | fields retained; save remains blocked | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::keeps a conflicted Model draft when review is dismissed` |
| 11 | requires another review when a reviewed version becomes stale | Error | concurrent write after review | old review closes; draft remains blocked until a new read | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::requires another review when a reviewed version becomes stale` |
| 12 | prevents retry after review reveals lost edit access | Error | latest effective role is view | draft preserved; retry disabled | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::prevents retry after review reveals lost edit access` |
| 13 | reviews a conflicting Model before intentional retry in the browser | Happy | first save conflicts; latest version is v7 | typed draft retained; explicit retry carries v7 and displays confirmed response | Playwright | ✅ `tests/e2e/model-detail.spec.ts::reviews a conflicting Model before intentional retry` |

| 14 | clears a description the user emptied | Edge | Existing description; clear editor | PATCH includes explicit null | Component | ✅ Model detail component mirror |
| 15 | clears all Model tags | Edge | Remove final tag | PATCH includes empty tags array | Component | ✅ Model detail component mirror |
| 16 | blocks another Model write after an unconfirmed response | Error | PATCH transport loss or HTTP503 | Draft retained; Save blocked; review available | Component | ✅ Model detail component mirror |
| 17 | keeps an unconfirmed draft blocked when review fails | Error | Unconfirmed PATCH; latest GET503 | Draft preserved; no second write | Component | ✅ Model detail component mirror |
| 18 | adopts the reviewed result of an unconfirmed save without rewriting | Happy | PATCH applied but response lost; latest contains draft | Explicit use latest adopts exact version; no second PATCH | Component | ✅ Model detail component mirror |

## Checkpoint validation

Model metadata conflict UI: 66 tests across the Model detail, Model wire, and suite-hygiene files passed; the Chromium conflict-review flow passed (1 test). Frontend lint, typecheck (app and packages), and formatting passed. Two additional conflict-review regressions failed before their production fixes.

This is a partial M4 checkpoint: the optional version argument remains for unmigrated callers. Multipart writers, batch undo, ambiguous responses, and detail remote-state ownership still require the remaining rows and migration work. No blanket conditional-edit guarantee is claimed.

## Model unknown-outcome recovery

Six new component cases failed before this increment. Clearing the description previously omitted it from PATCH; removing the last tag likewise omitted `tags`, preserving stale server values. The form now sends explicit `null` / `[]`, matching the existing backend patch contract. The frontend `ModelUpdate.description` type now admits the server's documented null value.

A lost/malformed response, timeout or server failure cannot establish whether the write committed. The editor keeps its draft and blocks the ordinary Save action until the user reviews a fresh authorized read. That review can be explicitly adopted without a second write or used as the base for an intentional conditional retry. There is no automatic retry and no automatic claim of success based on matching field values. A failed review leaves the editor blocked. Definite validation failures retain the ordinary editable recovery path.

Validation: all **59 Model detail component tests passed**. Both Chromium flows passed: a dropped PATCH acknowledgement followed by review/adoption with exactly one write, and a true conflict followed by an intentional conditional retry. Remaining M4 work includes Multipart/provenance writers and final removal of the optional version compatibility path. Earlier batch and detail-owner work has its own qualified matrices; the historical checkpoint paragraph above records that earlier slice, not current outstanding scope.

## Current M4 reconciliation

The checkpoints above are historical validation records. Model, Multipart,
Document, Source and movement clients now require the complete editing identity;
the optional Model version argument has been removed. Multipart composition,
auxiliary editing, batch undo and detail ownership have their own completed
matrices. [Restore qualification](library-restore-editing-validation.md) records
the integrated incarnation-qualified protocol, including explicitly reviewed
cross-history retries. The real Favorites reading-position contract is now qualified in
[mutation validation](library-mutations-validation.md). The sole broad backend expectation failure is corrected with214 passing affected
contract tests. [M4 is locally accepted](frontend-m4-closure-validation.md), and M5
is the next active milestone. Final delivery and remote CI remain open.
