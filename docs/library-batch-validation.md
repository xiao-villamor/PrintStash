# Conditional Library batch edits

The selected snapshot supplies edit preconditions. Undo uses versions acknowledged by the original write, never a later GET or an inferred increment. A session retirement must stop the rest of a multi-request workflow.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | moves with every selected version | Happy | two selected Models | request contains exact expected_versions and contract header | Frontend unit | ✅ `src/lib/api/__tests__/models/batch.test.ts::moves with every selected version` |
| 2 | tags with every selected version | Happy | selected Model | request contains expected_versions and contract header | Frontend unit | ✅ `src/lib/api/__tests__/models/batch.test.ts::tags with every selected version` |
| 3 | undoes a move against its acknowledged versions | Happy | successful move returns versions with gaps | undo targets original collections using exact acknowledged versions | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::undoes a move against its acknowledged versions` |
| 4 | undoes tags against the acknowledged version | Happy | successful tag write | PATCH restores original tags with exact If-Match | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::undoes tags against the acknowledged version` |
| 5 | retains a concurrent change when undo conflicts | Error | another writer changed the Model after batch acknowledgment | conflict returned; no unconditional retry | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::retains a concurrent change when undo conflicts` |
| 6 | excludes failed rows from undo | Edge | partially successful batch | only acknowledged successful ids are written | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::excludes failed rows from undo` |
| 7 | stops remaining chunks after session retirement | Error | session ends while first chunk pending | no second chunk dispatched | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::stops remaining chunks after session retirement` |
| 8 | refuses an undo from a retired session | Error | original receipt belongs to previous login | no write dispatched | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::refuses an undo from a retired session` |
| 9 | preserves partial results across the batch boundary | Edge | 501 selected Models | 500+1 bounded requests; receipts cover confirmed rows | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::preserves partial results across the batch boundary` |
| 10 | quick tag editing uses its original version | Happy | Model v3 in quick tag editor | POST carries v3 | Frontend unit | ✅ `src/components/__tests__/model-tags-dialog.test.tsx::assigns an existing tag` |
| 11 | reports a conflicting batch undo in the browser | Error | Model changed after tagging | skipped warning; no success claim or unconditional retry | Playwright | ✅ `tests/e2e/vault.spec.ts::reports a conflicting batch undo` |
| 12 | publishes the confirmed tag version | Happy | tag response acknowledges v9 | parent receives v9 rather than an inferred version | Frontend unit | ✅ `src/components/__tests__/model-tags-dialog.test.tsx::publishes the confirmed tag version` |

Remaining qualification: multi-chunk transport interruption after earlier chunks succeed needs explicit partial-result recovery; collection moves still use the existing backend contract without Model edit versions. These are not covered by the conditional Model guarantee.

## Validation checkpoint

The five-file run passed all 180 feature tests and three hygiene checks, then reported the required hygiene ratchet reduction (122 conjunction names, previous cap 123). After lowering the cap, the affected three-file run passed 19 tests. The Chromium conflicting-undo flow passed (1 test). Initial wire regressions failed twice before expected_versions was added. Lint, typecheck and formatting were run for this checkpoint.

The grid now delegates Model move/tag receipts to one workflow owner. Old local grouping and unconditional tag undo are removed; successful tag assignment publishes the acknowledged version back to the detail editor. Collection undo remains a separate existing contract.
