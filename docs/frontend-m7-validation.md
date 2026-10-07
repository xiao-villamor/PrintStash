# M7 — asynchronous workflow qualification

Status: active after M6 acceptance e1746a46. This pass reconciles preserved M7
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
Archive review/transfer recovery remains an M7 acceptance review gap; this
upload checkpoint does not close the entire milestone.
