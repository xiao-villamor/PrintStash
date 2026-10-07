# M9 notifications workflow

Status: active, after provider/browser acceptance at `35ec1ea4`. M9 remains open.

## Inspection and diagnosis

Read `components/notifications-panel.tsx`, its complete test mirror,
`lib/api/notifications.ts`, its transport tests, `types/notifications.ts`, backend
`api/v1/notifications.py`, `db/models/notifications.py`, and the master-switch
functions in `modules/administration/runtime_config.py`. The panel owns four remote
copies through one Promise.all. A settings failure toasts then renders the initial
false/empty state; printer and delivery errors are silently converted to empty
success. Save disables only its button, so the draft can change during a command
and the receipt then clears the newer input. There is no command/session/disposal
guard. The transport also retains the compatibility GET cache below those copies.
The backend restricts every notification read to superusers, so a non-admin cannot
truthfully present the switch as Off after a denied read.

Preserve masked secret omission, immutable target on existing channels, null versus
explicit printer scopes, existing validation, auto-disable indicators and actual
test-delivery outcomes. Channel edits and the master switch currently have no
conditional write protection; that contract remains required before this workflow
can be accepted. Delivery telemetry must not invalidate ordinary channel drafts,
but automatic disable is a relevant change to editable state.

## Intended increment

One settings query and one bounded delivery-log query own notification reads.
Printer choices use the existing printer query key/options and load only when a
draft needs them. A notification command owner keeps secret-bearing arguments out
of mutation history, cancels old reads, publishes masked receipts and handles
invalidation explicitly. The component owns only draft and gesture state. Read
errors have explicit recovery; secondary errors cannot fabricate empty authority.
No background timer or generic form framework is introduced. Roll back the panel,
query owner and transport together; keep backend authorization and secret masking.

## Behaviour coverage matrix (recorded before tests; updated from results)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| N1 | retries unavailable notification settings | Error | Initial GET fails | Error/retry; no Off or empty success; later read renders channels | Frontend unit | ✅ passed |
| N2 | reports unavailable delivery history separately | Error | Deliveries GET fails | Channels remain; history error/retry shown | Frontend unit | ✅ passed |
| N3 | refuses fabricated empty printer choices | Error | Printer GET fails while scoped draft open | Draft retained, error/retry; no false no-printers message or save | Frontend unit | ✅ passed |
| N4 | blocks draft replacement during a notification save | Edge | Held update | Fields and competing actions disabled until receipt | Frontend unit | ✅ passed |
| N5 | retires notification work with the session | Error | Session changes while command awaits | No old receipt/toast; secret draft cleared | Frontend unit | ✅ passed |
| N6 | cancels notification reads after disposal | Edge | Pending GET then unmount | Signal aborted; no late publication | Frontend unit | ✅ passed |
| N7 | publishes a confirmed channel receipt | Happy | Save accepted | Server row visible without an extra list GET | Frontend unit | ✅ passed |
| N8 | removes a confirmed deleted channel | Happy | DELETE accepted | Row removed; no local guess before confirmation | Frontend unit | ✅ passed |
| N9 | keeps the stored secret when the field is left alone | Happy | Existing channel secret left blank | Mask never sent | Frontend unit | ✅ passed |
| N10 | uses the confirmed master switch | Edge | Server response differs from gesture | Canonical checkbox follows receipt | Frontend unit | ✅ passed |
| N11 | avoids private reads for a nonadministrator | Error | canEdit false | Permission explanation; no private request or invented switch value | Frontend unit | ✅ passed |
| N12 | confirms a test that went through | Happy | Test accepted | Successful delivery reported | Frontend unit | ✅ existing test passed |
| N12b | reports a test the server could not deliver | Error | Delivery rejected | Failure reported with server reason | Frontend unit | ✅ existing test passed |
| N13 | retains an editable draft after a transient settings error | Error | Existing draft then 503 | Input retained read-only; retry restores actions | Frontend unit | ✅ passed |
| N15 | keeps raw credentials outside shared caches | Edge | Secret supplied while command pending | No MutationCache entry; only masked receipt in Query | Frontend unit | ✅ passed |
| N14 | resolves competing notification edits | Error | Concurrent channel/switch changes | No silent overwrite; explicit authorized review | Integration / Playwright | ❌ missing |
| N16 | completes delivery history during a channel save | Edge | Initial deliveries GET held across save | History finishes instead of remaining loading | Frontend unit | ✅ passed |
| N17 | rejects a pre-save read after confirmation | Edge | Old settings GET ignores abort then resolves | Confirmed channel remains visible | Frontend unit | ✅ passed |
| N18 | reads current settings on every transport call | Edge | Repeated transport reads | Second response is observed, not compatibility GET cache | Frontend unit | ✅ passed |
| N19 | reads current deliveries on every transport call | Edge | Repeated transport reads | Second response is observed, not compatibility GET cache | Frontend unit | ✅ passed |
| N20 | retires an unmounted notification command | Edge | Held update then unmount | Signal aborted; no receipt or toast published | Frontend unit | ✅ passed |
| N21 | retains a rejected notification draft | Error | Update rejected | Input retained; save available for recovery | Frontend unit | ✅ passed |

## Validation and limits

- Original component/API baseline: 51 passed, 5.72 s. Initial two regression
  cases failed before the implementation (2 failed, 43 deselected, 3.89 s).
- Initial refactor run: 12 failed / 41 passed. Ten failures concerned transport
  cancellation arguments or obsolete reread assertions; non-admin and failed-read
  assertions expected the former invented switch/toast behavior. Updated those
  expectations to the documented observable contracts.
- Additional race coverage caught a defect in the refactor: cancelling all
  notification queries on save abandoned pending delivery history (1 failed /
  54 passed, 8.53 s). Cancellation now targets settings only. Component + API
  selection passed 63 tests in 9.24 s after that correction.
- Two transport cache regressions added subsequently: full API mirror passed
  10 tests in 5.94 s (8 overlap with the preceding selection).
- Command disposal and rejected-draft additions passed their focused selection:
  2 passed, 55 deselected, 3.48 s. Combined unique notification cases exercised:
  57 component and 10 API cases.
- Shared printer query regression selection: 3 passed, 29 deselected by name,
  6.50 s. Existing enabled gating and server data remain intact.
- Real Chromium + FastAPI: existing `settings.spec.ts` notification create/delete
  lifecycle passed (1 test, 10.2 s; 47.8 s including startup). This does not yet
  verify competing edits; N14 remains pending.
- Frontend app/UI/domain typecheck passed. Frontend formatting passed across
  776 files. Initial lint found an unused PrinterRead import after extracting
  printer options; removing it restored the lint gate. Vite build passed in
  3.23 s with the existing locale-shell/large-chunk warnings.

Correctness evidence is limited to these contracts and checks. Maintainability
changes are the removal of four component-owned remote copies, one combined
loader and compatibility GET caching for notification reads; canonical printer
ownership is reused. No latency, startup, memory or bundle-performance improvement
is claimed from test timings or code size. Full suites, coverage floors and remote
required CI remain part of final integration, not this focused checkpoint.

The remaining notification work is the conditional backend/channel/master-switch
contract, explicit conflict/uncertain-save recovery and its real-browser coverage.
This increment alone does not close the notifications workflow or M9.
