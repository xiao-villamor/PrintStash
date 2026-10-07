# M7 — asynchronous workflow qualification

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
