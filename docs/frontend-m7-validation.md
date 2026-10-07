# M7 — asynchronous workflow qualification

> Closure correction (2026-10-07): the missing printer-settings contract is now
> implemented and qualified below. Earlier async evidence is preserved separately.
> M7 is locally accepted; final delivery and required CI remain open. See the
> [M5–M8 reassessment](frontend-milestone-reassessment.md) for the remaining M8 gaps.


Status: locally closed after M6 acceptance e1746a46. Final delivery and CI remain open. This pass reconciles preserved M7
work only: Inbox/nav, Task Center, uploads/archive review, events and
printers/fleet/queues. M8–M11 remain pending. Existing per-owner matrices in
[frontend state](frontend-state-validation.md),
[feature ownership](frontend-feature-ownership-validation.md) and
[Model uploads](model-upload-workflow-validation.md) remain the detailed contracts.

## Acceptance matrix before additional tests

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | shares Inbox freshness across consumers | Happy | Nav badge and Inbox observe confirmed import | Same authoritative pending state | Frontend unit | ✅ `src/pages/__tests__/inbox.test.tsx::shares confirmed Inbox dismissal across consumers` |
| 2 | prevents a printer reconnect after disposal | Edge | Ticket/close callback after unmount | No new authenticated connection | Frontend unit | ✅ `src/components/__tests__/printer-detail.test.tsx::does not reconnect a disposed printer page` |
| 3 | discards an obsolete event connection | Edge | Old ticket resolves after replacement subscription | No old-session delivery | Frontend unit | ✅ `src/lib/__tests__/events.test.ts::ignores callbacks from a disposed event connection` |
| 4 | recovers remote Job state after missed events | Edge | Event connection drops while server Job progresses | Authorized resync converges to persisted completion | Playwright real | ✅ `tests/e2e-real/uploads.spec.ts::recovers remote Job state after missed events` — passed10.4s |
| 5 | preserves local transfer cancellation | Happy | Pause a multipart upload then reload/resume | Local transfer stops; durable accepted chunks resume without duplicate Model | Playwright real | ✅ `tests/e2e-real/uploads.spec.ts::@critical resumes a multipart upload after a browser reload` — passed18.3s |
| 6 | bulk upload waits for three terminal jobs and loads every WebP | Happy | Three unique real meshes, accepted form unmounted | All tasks complete and all three decoded images appear | Playwright real | ✅ `tests/e2e-real/uploads.spec.ts::bulk upload waits for three terminal jobs and loads every WebP` — passed21.9s after scroll arrangement correction |

## Ordered closure work

1. Diagnose the preserved bulk-upload browser failure using its retained SQLite
   and error output, then a bounded traced rerun. Do not mask a missing arrival
   behind page reloads or longer timeouts.
2. Qualify the captured-session upload workflow, same-session unmount continuation
   and retired-session dispatch suppression. Remove component-local execution.
3. Reconcile remote owners/timers and the event/reconnect/visibility contracts
   against the detailed matrices; add only uncovered acceptance behaviours.
4. Run affected unit/browser/static gates, update ownership/contributor docs and
   commit M7 acceptance before beginning M8.

The preserved bulk failure has no network trace. All three thumbnail files exist
in its retained throwaway data root; the first card remained a placeholder while
the other two rendered. This narrows the investigation but does not establish the
frontend or backend cause. No production change follows from that observation alone.

## Review ledger for this ordered pass

- Complete `lib/model-upload-workflow.ts`: captured session for sequential uploads,
  local byte progress, terminal Job waiter, linked G-code, one final publication.
- Complete `lib/queries/inbox.ts`: distinct authorized history/pending snapshots,
  one Query cache, detail-to-list reconciliation, mutation admission/ACK fences.
  Navigation intentionally excludes completed history; the history API remains
  unbounded and is not redesigned by this frontend migration.
- `lib/task-center.ts:985–1129`: one completion-chained active-Job poll with
  visibility/online wakeup, bounded failure backoff and pending-event wakeup.
  Retain this process coordinator; it is not a duplicate Query timer.
- Complete `lib/archive-upload.ts` and `components/archive-review.tsx:1–218`:
  local transfer progress/cancellation is separate from durable inspection.
  Review still copies a Job manifest in an effect and only offers Cancel after
  read failure; its dedicated cancellation/recovery/session cases need assessment.
  Remaining JSX is not yet counted as manually reviewed in this pass.

### Bulk trace diagnosis

The bounded repeated flow failed on the same first image. Its API already returned
that Model's thumbnail URL; neither a thumbnail request nor an HTTP failure
existed for that image. The screenshot puts its third card below the viewport
in the two-column layout. Ranked explanations: (1) expected viewport admission
means the test must scroll to this card; prediction: one scroll starts its download;
(2) a lost derivative notification would leave the URL absent, contradicted by the
recorded browse response; (3) a failed image transfer would leave a request/error,
absent from the trace. Amend only the browser action to reveal each tested card,
then retain every terminal-Job, decoded-image and WebP response assertion.

The corrected two-flow browser gate passed2/2 in1.3m. No production image
change was needed: revealing the third card starts its authorized transfer and
all three decoded previews appear. The original and traced failures remain
archived. The new Job recovery test drops real event delivery and browser Job
reads while the server finishes an actual upload, then restores the connection;
it must show terminal completion in the existing Task Center without reload.

Integrated upload/event/task/shell gate:245/245 across8 files (10.27s).
The separate real Job recovery flow passed10.4s/45.1s total.
At the upload checkpoint, archive review/transfer recovery remained an M7
acceptance gap. The ordered qualification below closes that gap.

## Archive review increment — requirements before tests

The immutable completed ZIP manifest and the Task Center progress summary have
different contracts. Task Center remains the only live Job progress coordinator.
Query will own the completed manifest projection under its Job identity; it will
not poll or copy the Job's progress. The dialog owns selection/search/destination
and command feedback. Changing Job/session must retire the whole review draft.
Use the existing Query provider and keyed composition; no universal workflow hook
or additional timer. The cancellable Job transport is shared with existing callers.
Remove the component's effect-owned manifest/error fetch and obsolete requests.
Rollback this dialog/transport increment together, preserving session fences.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| A1 | retries an unavailable ZIP manifest | Error | First GET503; user retries after recovery | Review files become usable | Frontend unit | ✅ `src/components/__tests__/archive-review.test.tsx::retries an unavailable ZIP manifest` |
| A2 | cancels an abandoned ZIP manifest read | Edge | Dispose while GET is held | Actual HTTP signal aborted | Frontend unit | ✅ `src/components/__tests__/archive-review.test.tsx::cancels an abandoned ZIP manifest read` |
| A3 | retires the previous ZIP draft when the Job changes | Edge | Selected old archive; replacement read held | No old files/selection under new Job | Frontend unit | ✅ `src/components/__tests__/archive-review.test.tsx::retires the previous ZIP draft when the Job changes` |
| A4 | shares a completed ZIP manifest read | Happy | Two consumers of same Job | One GET, both show same manifest | Frontend unit | ✅ `src/components/__tests__/archive-review.test.tsx::shares a completed ZIP manifest read` |
| A5 | rejects a ZIP gesture from a retired session | Edge | Session replaced before old import click | No selection POST for replacement identity | Frontend unit | ✅ `src/components/__tests__/archive-review.test.tsx::rejects a ZIP gesture from a retired session` |
| A6 | ignores a ZIP receipt after its review closes | Edge | Selection POST held; review replaced | Late receipt cannot close replacement dialog | Frontend unit | ✅ `src/components/__tests__/archive-review.test.tsx::ignores a ZIP receipt after its review closes` |

Existing folder navigation, supported-file selection, destination role rules,
empty/error manifests and exact selection payload assertions remain mandatory
regressions. No change to archive wire/storage/backend contracts is proposed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| A7 | removes a refused cached ZIP manifest | Error | Loaded manifest; background GET403/404 | Old private files and selection controls disappear | Frontend unit | ✅ `src/components/__tests__/archive-review.test.tsx::removes a refused cached ZIP manifest` |
| A8 | preserves ZIP selection through a temporary read failure | Error | Loaded selected manifest; background GET503 | Selection remains; Retry offered | Frontend unit | ✅ `src/components/__tests__/archive-review.test.tsx::preserves ZIP selection through a temporary read failure` |
| A9 | retires ZIP transfer feedback with its session | Edge | ZIP HTTP pending; logout/login | No old task or toast in replacement session | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::retires ZIP transfer feedback with its session` |

The first archive-review gate was6 failed/13 passed (5.64s). After replacing
the effect-owned read with a keyed immutable-manifest Query and retiring command
feedback on disposal/session change,19 dialog plus12 Job transport cases passed
(31/31,5.38s). New permission/transient recovery branches require A7/A8 before
acceptance. A9 assesses the adjacent local-transfer feedback lifetime without
adding a new Job owner.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| A10 | resumes Job synchronization when the tab becomes visible | Edge | Active Job; tab hidden while server finishes | No hidden polling; completed snapshot on visibility return | Frontend unit | ✅ `src/lib/__tests__/task-center.test.ts::resumes Job synchronization when the tab becomes visible` |

A9 passed against unchanged archive-transfer code: session-fenced transport and
toast cancellation handling already suppress retired feedback. Retain it as a
regression, without attributing a new transfer fix. The first combined gate was
282passed/3failed: new background manifest assertions read before Query's scheduled
React notification. Await the actual alert/Retry presentation; no retry count or
production behavior changes. Static checking caught two unsupported Testing
Library `exact` options; exact string names already provide that contract. The
corrected22-case archive mirror passed11.12s and full static checks passed.

### ZIP handoff race — requirements before regression tests

The real ZIP lifecycle failed before opening review: the generic discovered
`Prepare ZIP` row was Ready while the named upload row remained pending.
The event can discover the Job before the upload HTTP receipt attaches its id.
Both rows then claim one Job and the first one wins subsequent snapshots.
Do not change the browser assertion to accept the wrong row. The narrow correction
belongs to Task Center attachment and existing duplicate reconciliation.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| A11 | attaches a ZIP receipt after Job discovery | Edge | Generic running Job discovered before upload receipt | One named workflow row retains destination/tags and reaches Ready | Frontend unit | ✅ `src/lib/__tests__/task-center.test.ts::attaches a ZIP receipt after Job discovery` |
| A12 | attaches a ZIP receipt after terminal discovery | Edge | Generic Job already completed before upload receipt | Named workflow adopts known terminal result immediately | Frontend unit | ✅ `src/lib/__tests__/task-center.test.ts::attaches a ZIP receipt after terminal discovery` |
| A13 | repairs a persisted duplicate ZIP task | Edge | Reload contains generic and named archive rows | One named review-ready workflow survives sync | Frontend unit | ✅ `src/lib/__tests__/task-center.test.ts::repairs a persisted duplicate ZIP task` |

A bounded traced browser run reproduces the race. The competing hypotheses were
a backend Job still running, a stale label selector and duplicate projection
ownership. The snapshot contains the same Job twice with conflicting states;
the loop below targets attachment after discovery, retaining the actual server
state and the workflow's review metadata.

## Ownership and timer assessment

| Surface | Remote owner and retained lifecycle | Removed mechanism / acceptance evidence |
|---|---|---|
| Inbox + navigation | `lib/queries/inbox.ts`; separate pending/history projections, shared keys, active detail polling | Page/badge effect caches and intervals removed; shared dismissal, draft/session and failed-read contracts pass |
| Task Center | `lib/task-center.ts`; local command metadata plus session-owned Job snapshots, shared waiters, one completion-chained poll | No per-upload Job poll; reconnect, offline backoff, hidden-tab pause, late-session and terminal monotonicity contracts |
| Events | `lib/events.ts`; one shared ticket/socket incarnation | Pending tickets/handlers/timers retire together; old callbacks cannot reopen or deliver |
| Printer detail / fleet | `features/printers/queries.ts`; keyed resource readers and explicit ACK reconciliation; printer stream remains disposable per printer | Component remote result copies and ad hoc reads replaced; selected printer's commands and editable drafts remain local |
| Administrative work | `features/work/queries.ts`; overview and queue are separate explicit projections with event wakeup and10s visible fallback | No page-owned Job poll; partial error, denial, unknown queue and confirmed-write contracts retained |
| Model uploads | `lib/model-upload-workflow.ts`; one initiating session over accepted sequential work | Component-local execution removed; byte progress remains in `artifact-upload.ts` |
| ZIP | Task Center owns live progress; Query owns completed immutable manifest; dialog owns selection/destination | Effect-owned manifest fetch removed; shared cancellation/retry; one named Task adopts early-discovered Job |

Source search finds no `setInterval`/`setTimeout` in Inbox pages, navigation badges,
UploadModal, ArchiveReview, BackgroundWorkPanel or FleetPanels. This is an ownership
check, not a performance measurement. Task Center's active-work/failure fallback,
events reconnect and printer-stream reconnect are justified remaining timers.
The historical detailed owner matrices and manual inspection ledgers remain
linked; this pass does not claim a new whole-file audit from a timer search.

ZIP attachment regression:3/3 failed before the Task Center correction (2.26s).
Attachment now preserves the initiating workflow's name/destination/tags, removes
the competing discovered row and adopts an already-known terminal result. Existing
reconciliation also repairs persisted duplicates identified by retained local
archive metadata. The combined Task Center/archive/upload/shell/task-list/hygiene
gate passed206/206 (6 files,8.98s); visibility separately passed109/109 before
adding the three handoff cases. The unchanged real ZIP lifecycle passed13.8s; failed-ZIP retained-input recovery
passed6.0s (2/2,56.5s total). All final format/lint/app/UI/domain type checks and
production build passed; the existing500kB chunk warning remains.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| A14 | preserves a discovered Job after local task removal | Edge | Original local row cleared before attachment | Still-live discovered server task is retained | Frontend unit | ✅ `src/lib/__tests__/task-center.test.ts::preserves a discovered Job after local task removal` — passed2.23s with112 unrelated cases deselected |

## Rollback, scope and remaining limits

Revert an owner together with its consumer cutover; retain private-session fences
and the explicit archive handoff regression. No backend/runtime/deployment change
was needed. Completed-manifest Query cache and live Task Center progress are
distinct projections, not competing freshness owners. No new independent Job
poller or generic workflow layer was added.

The integrated285-case run initially passed282 and failed three newly written
manifest notification arrangements; the corrected22-case affected file passed.
Later handoff changes were qualified by the206-case Task Center/archive/upload/
shell/task-list/hygiene gate. Results are not described as one uninterrupted
285-case green run. Final PR CI remains M11. Inbox history remains an unbounded
backend projection, and fleet maintenance still performs two reads per printer;
these existing limits are disclosed, without unsupported performance claims.

M7 acceptance follows completed M0–M6. All M7 acceptance rows above have named
coverage and current qualification. M8 is next; no M8 implementation belongs to
this closure. No full-suite coverage, final CI or performance improvement is claimed.

## Reopened printer settings contract

The settings aggregate must reject stale edits independently of live telemetry.
The backend now compares a captured settings version atomically; both first-party
editors retain their draft, review authorized current settings and explicitly resubmit.
Legacy clients remain additive-compatible; their writes invalidate captured bases.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| PE1 | rejects a competing settings edit | Error | Two edits share a base | Second PATCH returns 412; first name remains | Integration | ✅ `backend/tests/integration/api/v1/printers/test_editing.py::test_rejects_a_competing_settings_edit` |
| PE2 | requires the advertised precondition | Error | conditional-v1 without If-Match | 428; name unchanged | Integration | ✅ `backend/tests/integration/api/v1/printers/test_editing.py::test_requires_the_advertised_precondition` |
| PE3 | rejects malformed editing bases | Error | Wrong entity, epoch, version, contract | Refused without changing settings | Integration | ✅ `backend/tests/integration/api/v1/printers/test_editing.py::test_rejects_malformed_editing_bases` |
| PE4 | invalidates a base after a legacy edit | Edge | Unconditional PATCH then old conditional PATCH | Legacy accepted; stale edit rejected | Integration | ✅ `backend/tests/integration/api/v1/printers/test_editing.py::test_invalidates_a_base_after_an_external_settings_write[legacy-api]` |
| PE5 | preserves the base during telemetry updates | Edge | Status/error/timestamp changes | Settings save still accepted | Integration | ✅ `backend/tests/integration/api/v1/printers/test_editing.py::test_preserves_the_base_during_telemetry_updates` |
| PE6 | invalidates a base after an external settings write | Edge | Direct persisted settings change | Stale PATCH rejected | Integration | ✅ `backend/tests/integration/api/v1/printers/test_editing.py::test_invalidates_a_base_after_an_external_settings_write[direct-writer]` |
| PE7 | rejects a prior database history | Error | Epoch rotated with unchanged settings version | Stale PATCH rejected | Integration | ✅ `backend/tests/integration/api/v1/printers/test_editing.py::test_rejects_a_prior_database_history` |
| PE8 | rolls back an invalid settings edit | Error | Provider configuration invalid | Neither settings nor version consumed | Integration | ✅ `backend/tests/integration/api/v1/printers/test_editing.py::test_rolls_back_an_invalid_settings_edit` |
| PE9 | rechecks revoked editor authority | Error | Account or printer permission changed after initial read | No write committed | Integration | ✅ `backend/tests/integration/modules/printing/test_printer_edits.py::test_revoked_administrator_authority_rejects_the_edit` |
| PE10 | preserves existing rows across schema upgrade | Edge | Database at prior migration | Settings retained; fresh and upgraded triggers agree | Integration | ✅ `backend/tests/integration/db/migrations/test_printer_edit_version.py::test_upgrade_preserves_existing_printer_settings` |
| PE11 | serializes simultaneous settings edits on PostgreSQL | Edge | Concurrent writers use one base | Exactly one commit accepted | Integration | ✅ `backend/tests/integration/modules/printing/test_printer_edits.py::test_only_one_editor_can_commit_from_a_shared_base` |
| PE12 | preserves a conflicted printer draft for review | Error | Competing save | Draft retained; explicit review/revised save | Frontend unit / Playwright | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::preserves a conflicted printer draft for an explicit revised save` |
| PE13 | retires a printer draft with its session | Edge | Session changes during save/review | Old result cannot publish | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::ignores a settings acknowledgement from a retired session` |
| PE14 | reviews an uncertain printer save | Error | Accepted write loses its response | Read before next save; no automatic overwrite | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::requires review after an uncertain printer save` |
| PE15 | preserves browse invalidation across downgrade | Edge | Remove settings version then update printer | Existing catalog revision still advances | Integration | ✅ `backend/tests/integration/db/migrations/test_printer_edit_version.py::test_round_trip_preserves_existing_settings` |
| PE16 | retains a claim after a transaction rollback | Edge | Claim and flush then rollback | Original version remains usable | Integration | ✅ `backend/tests/integration/modules/printing/test_printer_edits.py::test_rollback_preserves_an_unused_editing_base` |
| PE17 | accepts a current conditional settings edit | Happy | Current authorized base | New settings committed with advancing version | Integration | ✅ `backend/tests/integration/modules/printing/test_printer_edits.py::test_a_current_administrator_can_commit_an_explicit_edit` |
| PE18 | accepts an explicit legacy settings command | Edge | No contract headers | Write accepted and base advanced | Integration | ✅ `backend/tests/integration/modules/printing/test_printer_edits.py::test_explicit_legacy_commands_can_save_without_a_base` |
| PE19 | renders migration SQL without connecting | Edge | SQLite/PostgreSQL offline render | Column and trigger DDL emitted | Integration | ✅ `backend/tests/integration/db/migrations/test_printer_edit_version.py::test_renders_the_upgrade_without_a_database_connection` |
| PE20 | sends the captured printer editing headers | Happy | Draft predates a background refetch | Original base on PATCH; advancing receipt required | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::sends the original settings base after a background refetch` |
| PE21 | rejects an invalid printer acknowledgement | Error | Wrong identity, epoch or non-advancing version | No confirmed success; explicit review required | Frontend unit | ✅ `frontend/src/lib/api/__tests__/printers.test.ts::rejects an acknowledgement with $label` |
| PE22 | adopts authorized current settings | Edge | Conflicted draft including local credential | Explicit adoption resets fields and clears secret without PATCH | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::adopts reviewed values without publishing the credential draft` |
| PE23 | refuses revised credentials after a provider change | Error | Reviewed provider differs from draft | Revised save disabled until explicit adoption | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::requires adoption when the reviewed connection uses another provider` |
| PE24 | retires an inaccessible editor after denied review | Error | GET review returns 403 | Editor removed; explicit read retry; no write | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::retires the inaccessible printer editor after review is denied` |
| PE25 | reviews a competing quick model edit | Error | Card editor receives 412 | Accessible review inside model dialog; explicit retry | Frontend unit | ✅ `frontend/src/components/__tests__/printers-list.test.tsx::reviews a competing quick model edit inside its dialog` |
| PE26 | rejects a second competing edit after review | Error | Another editor wins after review GET | Revised PATCH still conditional; draft retained | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::rejects a second conflict after reviewing current printer settings` |
| PE27 | preserves a newer cached printer after a delayed acknowledgement | Edge | Cache observes newer version while PATCH pending | Older receipt cannot replace newer server state | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::preserves a newer printer already observed while a save was pending` |
| PE28 | completes printer conflict recovery against the backend | Happy | Two browser forms edit one printer | Conflict shown; explicit revised save preserves other fields after reload | Playwright real | ✅ `frontend/tests/e2e-real/printers.spec.ts::two printer editors resolve a conflict without replacing untouched settings` |
| PE29 | validates the revised settings draft | Error | Empty required name after review | Browser validity blocks another PATCH | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::validates the revised settings draft before sending it` |
| PE30 | rechecks a revoked printer grant | Error | Admin grant removed after actor/row lookup | No settings or version committed | Integration | ✅ `backend/tests/integration/modules/printing/test_printer_edits.py::test_rechecks_a_revoked_printer_grant` |
| PE31 | refuses settings edits on a trashed printer | Error | Legacy command targets deleted row | No settings change | Integration | ✅ `backend/tests/integration/modules/printing/test_printer_edits.py::test_refuses_a_legacy_edit_on_a_trashed_printer` |
| PE32 | retires an in-flight settings review | Edge | Session changes while authorized GET is pending | Late values cannot populate the replacement session | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::retires an in-flight printer settings review with its session` |
| PE33 | retires a printer view after access is revoked | Error | Loaded detail later returns 403/404 | Controls removed; live connection disposed | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::retires a loaded printer view after a %s denial` |
| PE34 | recovers a denied printer after explicit retry | Happy | Permission restored after denied detail | Fresh authorized detail and a new live connection become available | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::recovers a denied printer after explicit retry` |


### Printer-settings qualification results

- Backend CRUD, conditional API and printer RBAC selection: **80 passed** (16.05s).
- Atomic owner on SQLite/PostgreSQL: original **20 passed** within the initial
  38-pass combined run; the added grant-revocation/deleted-row selection added
  **4 passed** (75.65s). The original combined run also had two migration fixture
  errors, corrected before the schema gate below.
- Migration upgrade/downgrade/offline render, model-versus-chain, migration
  conventions and database parity: **165 passed** (326.71s). A downgrade
  regression first proved that recreating the table dropped existing browse
  triggers; native column removal now preserves them. The initial PostgreSQL
  fixture used enum values instead of persisted enum names; corrected.
- OpenAPI snapshot: **1 passed** (8.14s). Configured Pyright and the new contract
  module/schema check: zero errors. An additional forced check of legacy router
  and model files outside configured scope reported 41 errors; that exploratory
  check is not green and has no independently measured baseline here.
- Printer detail, card editor and API tests: **135 passed** (28.72s). The later
  lost-response adoption/session-retirement extension: **2 passed** (6.65s).
  Detail plus feature query owner after permission-denial handling: **80 passed**
  (16.46s). Explicit authorized recovery after denial: **1 passed** (4.50s). Denial cases first failed with stale controls visible; assertions wait
  for Query's scheduled observer notification, not merely request settlement.
- Mock-browser printer flows: **3 passed** (34.8s). Real-backend two-editor flow:
  **1 passed** (15.2s body, 1.2m with setup). Its initial failure was an overly
  exact label lookup after persistence; the accessible textbox-role lookup passed.
- Frontend formatting, lint, app/workspace type checks and production build
  passed before the final denial increment; final static results are recorded
  below. The build retained its existing large-chunk warning. Scoped backend
  Ruff and formatting passed.

These are separate focused invocations, not one uninterrupted all-green suite.
Earlier intermediate failures included missing localization keys and an e2e
suite nesting violation; the corrected affected 103-case run passed. No broad
coverage gate, final PR CI, latency or memory improvement is claimed. Credentials
remain component-local, outside Query/MutationCache; permission denial hides
retained private reads and retires the live connection, while session retirement
clears the private cache. A transient read failure does not revoke access.

Removed mechanisms: unconditional first-party printer settings updates, the
unused `publishPrinter` callback, post-commit receipt refresh, and quick-editor
saves outside the shared settings command. Legacy third-party writes remain
explicitly unprotected; they advance the settings base and invalidate stale
conditional clients. Versions are monotonic, not necessarily consecutive.
Rollback requires reverting the UI and conditional-client requirement together;
retain the additive database column/contract during a mixed-client rollout.

Final post-denial static gate: formatting **772 files**, lint with warnings denied,
application plus both workspace type checks, and production build **passed**
(4.20s build; large-chunk warning retained). `git diff --check` passed.
