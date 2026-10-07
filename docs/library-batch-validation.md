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

## M4 interrupted batch receipt — preflight

The final M4 audit confirms the limitation recorded above: `chunks` throws after
an acknowledged earlier chunk, discarding its confirmed receipt. The grid then
shows only an error, cannot offer conditional undo for those successes, and does
not distinguish unconfirmed writes from untouched selections. The same loss can
occur partway through undo. This is within M4 confirmed-mutation recovery.

Keep the existing batch owner and HTTP contract. Return confirmed per-row results
with a discriminated completion (`complete` or `interrupted`); an interruption
records the unconfirmed request, unattempted rows and error. No automatic retry or
fresh-version write is permitted. Undo may target only acknowledged successes.
The UI must expose partial confirmation, retain the original intent for review,
and make unknown outcomes visible without calling them failures or successes.
Session retirement still rejects the whole presentation into the new session.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| I1 | preserves confirmed receipts when a later batch request is unconfirmed | Error | First 500 acknowledged; second request fails; more rows remain | Exact confirmed versions, unconfirmed and unattempted identities; no third request | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::preserves confirmed receipts when a later $label request is unconfirmed` |
| I2 | undoes only confirmed rows from an interrupted batch | Edge | Partial receipt then intentional undo | Only acknowledged ids and versions written | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::undoes only confirmed rows from an interrupted batch` |
| I3 | preserves confirmed undo progress after interruption | Error | Earlier undo acknowledged; next response lost | Exact restored receipts retained; remaining rows untouched | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::preserves confirmed undo progress after interruption` |
| I4 | rejects malformed batch acknowledgements without inventing successes | Error | Response has duplicate, missing, foreign or nonadvancing receipts | Current chunk unconfirmed without invented successes | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::rejects $label batch acknowledgements without inventing successes` |
| I5 | suppresses interrupted batch feedback after session retirement | Error | Session changes during failed request | Rejection; no receipt published in new session | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::suppresses interrupted batch feedback after session retirement` |
| I6 | retains an interrupted batch intent for explicit review | Error | Move/tag acknowledgement lost | Intended change and unresolved models remain visible; no automatic retry | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retains an interrupted tag intent for explicit review; retains an interrupted move destination for explicit review` |
| I7 | offers conditional undo for an interrupted batch's confirmed changes | Edge | Earlier rows confirmed; later request unknown | Confirmed-only undo available; no complete-success claim | Frontend unit | ✅ `src/components/__tests__/library-batch-recovery.test.tsx::retains the original uncertainty after undoing confirmed changes` |
| I8 | reviews an interrupted batch in the browser | Error | Lost batch acknowledgement | Visible uncertainty, retained intent, explicit navigation to current model | Playwright | ✅ `tests/e2e/vault.spec.ts::reviews an interrupted batch in the browser` |

| I9 | retains the original uncertainty after undoing confirmed changes | Edge | Partial initial batch; intentional undo succeeds | Undo result shown separately; original unconfirmed models remain reviewable | Frontend unit | ✅ `src/components/__tests__/library-batch-recovery.test.tsx::retains the original uncertainty after undoing confirmed changes` |
| I10 | suppresses late undo presentation after retirement | Error | Undo response held across logout/unmount | No result or refresh published to the replacement view | Frontend unit | ✅ `src/components/__tests__/library-batch-recovery.test.tsx::suppresses late undo presentation after $label retirement` |
| I11 | stops later destination groups after an interrupted move undo | Error | First original-folder group succeeds; next loses response | Prior receipts retained; later groups never written | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::stops later destination groups after an interrupted move undo` |

Rollback the owner result and its consumers together. Remove throw-away partial
results, not conditional writes. Keep unrelated M5 edits in shared grid files out
of this checkpoint. No backend migration, framework or performance claim.

## Interrupted-batch qualification — 2026-10-07

Eight owner cases failed before the result contract changed; the session controls
passed. The first owner amendment passed16/16. Two grid cases then reproduced the
lost intent: neither unknown tag assignment nor unknown movement opened a review.
The grid now retains that intent in a command-recovery dialog, with exact counts
and links to current Models. It neither fetches a newer version to authorize a
retry nor repeats the unconfirmed request. Confirmed-only undo is available;
its result is separate from the original unknown outcomes. Dismissing explicitly
discards the remaining selection. New sessions and retired views suppress late
undo feedback.

The affected grid/owner/wire/recovery/locale run passed **232/232** (74.87 s).
Two Chromium flows passed **2/2** (16.0 s): interrupted batch review through current
Model navigation, and the existing conditional undo conflict. App/UI/domain types,
frontend lint and **759-file** formatting passed. Initial lint caught an import
placed before the client directive and a deferred assertion style; both were
corrected without changing the behavior contract. These are validation results,
not performance measurements. Final boundary/build checks are recorded separately.

Collections retain their separate existing contract. When a Model batch stops,
subsequent collection moves are not dispatched; this receipt describes Model
results only. The backend batch limit and public response shape are unchanged.
The earlier paragraph identifying lost multi-chunk receipts is historical: this
increment closes that gap. M4 still awaits its wider backend gate and acceptance
consolidation before M5 can begin.

Final owner/recovery/dependency run: **93/93** (15.76 s); three applicable
suite-hygiene checks passed (1.67 s). The unstarted M5 browser file's known nesting
failure remains separately recorded; the full hygiene suite is not claimed green.
Production Vite build passed (2.35 s), retaining existing chunk-size advisories.
The change removes loss of earlier batch receipts and blind whole-operation
feedback, without adding another entity owner or transport cache.
