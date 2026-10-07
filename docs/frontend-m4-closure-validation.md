# M4 acceptance consolidation

2026-10-07. **M4 is locally accepted after M0–M3.** M5 is the next and only
active milestone. This is neither global completion nor a remote CI result.
The completed broad invocation and its corrected assertion are recorded separately
below; no failed invocation is relabeled green.

## Scope and ownership

M4 makes confirmed Library mutations survive earlier reads, and protects
first-party Model, Multipart and Document editing against stale intents. Source
metadata and covers share the Model editing identity; movement and batch undo
capture that identity at the gesture or original acknowledgement. Query remains
the authorized remote owner. Drafts, recovery decisions and command receipts are
local interaction state, not another remote cache.

The editing identity is the existing database incarnation plus the aggregate's
positive edit version. Physical restore renews the incarnation; ordinary database
transfer preserves it. Atomic conditional writes compare both fields. The
unreleased `conditional-v1` client contract was updated as one reader/writer
cutover, with no new server runtime or schema column for the incarnation.

The existing authenticated compatibility contract remains explicit: legacy
requests without conditional opt-in advance versions but cannot detect stale
legacy intent. They are not included in a blanket conflict-protection claim.
First-party Model/Multipart/Document writers require the complete editing base;
the optional Model-version bypass and id-only native drag payload are removed.
Collection commands retain their existing separate contract.

## Assessed behaviour coverage

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | rejects a late page after a confirmed star | Edge | Earlier continuation finishes after mutation acknowledgement | Confirmed state remains canonical | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::rejects a late page after a confirmed star` |
| 2 | preserves the reading position after a confirmed favorite removal | Happy | Real successful unstar after scrolling | Pending card remains; confirmed removal preserves neighbor position | Playwright real | ✅ `tests/e2e-real/favorites.spec.ts::preserves the reading position after a confirmed favorite removal` |
| 3 | keeps a favorite when unstar fails | Error | Rejected command | Card stays starred with error | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::keeps a favorite when unstar fails` |
| 4 | permits only one atomic editor | Edge | Two same-base writers | One write wins; one conflicts | Integration | ✅ `integration/modules/library/test_edit_preconditions.py::TestClaim::test_permits_only_one_atomic_editor` |
| 5 | reviews a competing edit before saving the retained draft | Error | Another editor commits first | Draft retained; explicit reviewed retry | Playwright real | ✅ `tests/e2e-real/documents.spec.ts::reviews a competing edit before saving the retained draft` |
| 6 | confirms a committed save after its acknowledgement is lost | Error | Server commit then lost response | Explicit authorized review without blind resubmission | Playwright real | ✅ `tests/e2e-real/documents.spec.ts::confirms a committed save after its acknowledgement is lost` |
| 7 | rejects revoked actor | Error | Permission lost before conditional write | 403; no metadata overwrite | Integration | ✅ `integration/modules/library/test_edit_preconditions.py::TestClaim::test_rejects_revoked_actor` |
| 8 | preserves the editing base across a background refresh | Edge | Dirty draft; newer server read | Original editing identity remains attached to draft | Frontend unit | ✅ `src/components/model-detail/__tests__/index.test.tsx::preserves the editing base across a background refresh` |
| 9 | moves a model reached through keyboard pagination beyond 500 entries | Happy | Sidebar-only Model; conflicting editor | Real412; original destination retained; intentional retry persists | Playwright real | ✅ `tests/e2e-real/outliner-pagination.spec.ts::moves a model reached through keyboard pagination beyond 500 entries` |
| 10 | rejects a draft from the previous restored history | Error | Integer version recurs after physical restore | Prior-history draft cannot overwrite restored entity | Integration | ✅ `integration/modules/backups/backup/test_restore.py::TestRestoredEditingIdentity::test_rejects_a_draft_from_the_previous_restored_history` |
| 11 | preserves confirmed receipts when a later request is unconfirmed | Error | Earlier chunk acknowledged; next response lost | Exact prior receipts survive; later chunks stop | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::preserves confirmed receipts when a later $label request is unconfirmed` |
| 12 | undoes only confirmed rows from an interrupted batch | Edge | Partial receipt; user takes undo | Exact acknowledged ids/versions only | Frontend unit | ✅ `src/features/library/__tests__/batch-edits.test.tsx::undoes only confirmed rows from an interrupted batch` |

This table is the acceptance index. Complete per-command happy/edge/error matrices
and original red/green receipts remain in their canonical records:

- [Stars and confirmed-removal reading position](library-mutations-validation.md).
- [Model editing and unknown-outcome review](library-editing-validation.md),
  [canonical Model detail](library-detail-state-validation.md).
- [Multipart composition and auxiliary editing](library-multipart-validation.md).
- [Source metadata and covers](library-provenance-editing-validation.md).
- [Movement, native/sidebar intent and recovery](library-move-validation.md).
- [Batch receipts, exact-version undo and interruption](library-batch-validation.md).
- [Restored editing identity and cross-history publication](library-restore-editing-validation.md).
- [Backend contracts and legacy compatibility](library-contracts-validation.md).

## Qualification and unresolved gate

The restore checkpoint `baf54104` qualified the integrated editing identity through
SQLite, real PostgreSQL restore, forward recovery, repeated restore, database
transfer, bounded reads, OpenAPI, affected frontend owners and browser flows. The
individual run counts and failed harness attempts are in its record, not summed
as unique tests.

`8263b328` qualifies the real Favorites reading anchor: a 346 px confirmed-removal
jump reproduced, then 35 focused tests and the real-browser flow passed. A queued
scroll-event regression also failed before correction.

`1d315d16` qualifies interrupted batch recovery: eight owner failures and two grid
failures before fixes; 232 affected tests, 93 final owner/recovery/boundary tests,
three applicable hygiene checks and two Chromium flows passed. Frontend lint,
formatting, app/UI/domain types and production build passed. Runs overlap and
are not summed. The known unstarted M5 browser nesting failure remains outside
these focused hygiene checks.

The broad backend fast invocation finished with **13,351 passed, 1 failed,
148 warnings in 4,237.13 s**. The sole failure was
`TestOutlinerModels::test_returns_only_the_fields_the_tree_needs`: its exact-field
set omitted `edit_epoch`, which the approved M4 editing identity now requires.
The narrow invocation reproduced it (**84 passed, 1 failed**,45.01 s). Updating
that expectation and its obsolete four-field description preserves the exact
lightweight-projection assertion. No production code or backend schema changed.

The complete listing/outliner/conditional-edit/OpenAPI selection then passed
**214/214** (69.86 s), including that corrected assertion. Backend test lint passed.
The original broad run remains a failed invocation; its unchanged production
source and all other passing cases are retained as evidence. This deterministic
contract-expectation correction does not justify another identical hour-long
local run. The broad corpus is reconciled with this affected-contract rerun;
final full backend, frontend and remote CI gates still belong to M11.

All M4 acceptance behaviours now have named assertions and applicable executions.
The missing star anchor, restored-history collision and interrupted-batch receipt
were closed within M4 rather than passed to M5. No additional milestone is closed
by this acceptance.

## Removals, limits and rollback

Removed per-card star overrides, optimistic whole-detail copies, unconditional
first-party Model PATCH, id-only Model drag payloads, inferred batch undo versions,
throw-away partial batch receipts and Source's competing remote-state effects.
Local drafts and conditional command receipts remain intentionally separate from
Query entity data. The legacy invalidation bridge has its already named M10
cutover deadline; no claim of its global removal belongs to M4.

The new restore editing identity adds no per-row lookup, database table, runtime
or entity cache. Favorite position recovery reuses the existing entry/session
metadata and waits for actual card removal; native scroll bounds still apply.
Interrupted-batch review never automatically retries unknown writes or obtains a
fresh editing identity to authorize them. No end-user latency or startup
improvement is claimed from these correctness results.

Rollback conditional readers/writers as one contract, retaining additive durable
columns and migrations. Roll back batch outcome/consumers together. Preserve
later qualified checkpoints and pending M5/M7 edits; they are not included in
M4 completion. Complete manual review of all remaining frontend files, package
boundaries and production performance remains the later plan's stated scope.
