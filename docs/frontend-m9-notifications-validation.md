# M9 notifications workflow

Status: notification workflow locally accepted, including conditional first-party
edits and real-browser conflict recovery. M9 remains open for the remaining
administration workflows; final integration and remote CI are not complete.

## Initial inspection and diagnosis (before `7489d206`)

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
| N14 | resolves competing notification edits | Error | Concurrent channel/switch changes | No silent overwrite; explicit authorized review | Integration / Playwright | ✅ NC3 and NF16 below |
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
  verify competing edits; N14 was pending at this checkpoint (covered by NF16 below).
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

At the read-owner checkpoint, the remaining notification work was the conditional backend/channel/master-switch
contract, explicit conflict/uncertain-save recovery and its real-browser coverage.
This increment alone does not close the notifications workflow or M9.

## Conditional backend contract — checkpoint `ba3272f6`

The master switch has its own version, independent from vault/Search settings and
channel rows. Each channel binds its version to database history and a persisted
incarnation identity, so a reused integer ID cannot inherit an old draft. Legacy
writes remain supported but advance the same versions. Delivery status/counters
are telemetry; automatic disabling changes editable state and invalidates drafts.
An atomic claim precedes applying a draft, rechecks live administrator authority,
and shares the transaction with validation and the confirmed response snapshot.
Rollback retains additive schema and restores HTTP/UI precondition use together.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| NC1 | publishes an editing base | Happy | Channel/master GET | Opaque history and positive version | Integration | ✅ passed |
| NC2 | accepts the captured notification base | Happy | Matching If-Match | Change accepted; version advances | Integration | ✅ passed |
| NC3 | rejects a replayed notification base | Error | Two writes from one snapshot | 412; first write remains | Integration | ✅ passed |
| NC4 | requires the opted-in base | Error | Contract header without If-Match | 428; unchanged data | Integration | ✅ passed |
| NC5 | advances legacy notification writes | Edge | Legacy update | Old conditional draft rejected | Integration | ✅ passed |
| NC6 | ignores delivery telemetry for edits | Edge | Delivery counters/status changed | Captured channel base remains usable | Integration | ✅ passed |
| NC7 | detects automatic channel disabling | Error | Delivery worker disables channel | Old editor receives 412 | Integration | ✅ passed |
| NC8 | rejects a recreated channel identity | Error | Delete/recreate same integer ID | Old editor receives 412 | Integration | ✅ passed |
| NC9 | rejects restored database history | Error | History epoch changes | Old editor receives 412 | Integration | ✅ passed |
| NC10 | rolls back an invalid notification save | Error | Invalid channel config | Error without claim/version persistence | Integration | ✅ passed |
| NC11 | refuses retired administrator authority | Error | Role/session/deletion changes before claim | No write or committed version | Integration SQLite/Postgres | ✅ passed |
| NC12 | arbitrates independent notification writers | Edge | Independent sessions share base | Exactly one accepted update | Integration SQLite/Postgres | ✅ passed |
| NC13 | preserves notification data through migration | Edge | Existing data, upgrade/downgrade/upgrade | Values retained; correct triggers installed | Migration SQLite/Postgres | ✅ passed |
| NC14 | rejects malformed notification preconditions | Error | Wrong aggregate/overflow/malformed ETag | 412 without write | Integration | ✅ passed |
| NC15 | isolates notification aggregate versions | Edge | Vault/Search/other channel changes | Unrelated draft remains usable | Integration | ✅ passed |
| NC16 | renders the notification upgrade offline | Edge | SQLite/PostgreSQL SQL generation | Additive columns and triggers rendered without connection | Migration | ✅ passed |
| NC17 | publishes the confirmed master editing base | Happy | Master switch response | Canonical settings contain server version/history | Frontend unit | ✅ passed |

### Backend contract checkpoint

Implemented the additive conditional contract in `modules/notifications/editing.py`,
`api/v1/notifications.py` and immutable `db/notification_edit_contracts_v1.py`.
`c9852ff533ee` was autogenerated against predecessor `7af8bb13c174`: three
columns only. The generated migration was amended with historical identity
backfill, removal of that temporary default, frozen SQLite rebuild metadata and
trigger install/uninstall. The SystemConfig addition/removal remains native so
unrelated vault/Search triggers survive. Future channels receive fresh UUIDs from
the entity factory path; existing channel factories inherit those model defaults.

The master switch and each channel expose required EditingBase fields; HTTP writes
accept the established `conditional-v1` header and strong If-Match. Responses are
captured before commit. Opted-in missing bases return 428 and stale/foreign history
returns 412. Unversioned legacy calls still work and advance the same versions.
At this backend checkpoint, the first-party frontend carried read/receipt fields
but did not submit conditional edits yet. The client cutover is recorded below.

Evidence:

- Initial conditional API selection: 12 failed, 55 deselected, 5.56 s, before
  implementation. Initial complete API file: 67 passed; expanded contract cases:
  77 passed, 13.83 s.
- API + existing notification-service consumers: 140 passed / 2 failed, 19.24 s.
  Both failures constructed unpersisted channels for serialization; the API read
  contract now requires persisted identity. Converted those arrangements to the
  existing factory without weakening their corruption/secret assertions; the
  affected selection passed 2 tests, 61 deselected, 2.99 s.
- Independent-session claims and migration lifecycle/offline SQL on SQLite and
  PostgreSQL: 28 passed, 94.44 s. Retirement setup was subsequently moved from the
  test body to a fixture to follow test-design rules; the focused SQLite demotion
  selection passed 2 tests, 22 deselected, 28.65 s.
- Schema convergence against the full chain: 1 passed, 4 deselected, 48.62 s.
- Frontend component/API selection with required editing fields: 68 passed,
  8.74 s. Frontend types passed after exporting the new switch DTO from the public
  barrel. Frontend lint passed; formatting required reformatting the changed test.
- Final public-contract run: 79 notification API cases + 1 OpenAPI snapshot
  passed (80 total), 17.66 s. Reviewed snapshot differences are limited to the
  three notification paths and ChannelRead, NotificationSettingsRead and
  NotificationsSettings schemas.
- Scoped backend Ruff, scoped Pyright and configured project Pyright passed.
  Frontend formatting subsequently passed across 776 files. OpenAPI now declares required
  channel/settings editing fields and the two optional request headers; legacy
  request bodies remain unchanged.

Remaining work at that checkpoint was the first-party conditional write cutover, captured draft bases,
conflict/uncertain recovery, and real-browser proof. The existing notification
scope implementation also treats an empty printer ID list as all printers
(`notifications._channel_subscribes`); review the editor's empty-selection behavior
at that cutover rather than claiming that explicit-empty already means no printers.
No application performance improvement or remote CI completion is claimed here.

## First-party editor cutover — matrix recorded before tests, assessed below

Existing channels capture a masked snapshot only when editing starts. Updates send
only deliberate changes against that captured editing base. Background reads do
not rebase drafts. A conflict or uncertain update requires a fresh authorized
review, followed by explicit adoption or a revised save. Recreated channel/history
identities require adoption and clear old credential input. The master switch uses
its own captured intent and the same review policy. No generic form framework is
introduced. Rollback pairs the UI/transport precondition changes; retain the
additive backend schema and compatibility contract.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| NF1 | sends the captured channel precondition | Happy | Update known channel | Exact If-Match/contract headers; advancing receipt | Frontend API | ✅ `frontend/src/lib/api/__tests__/notifications.test.ts::sends the captured channel precondition` |
| NF2 | sends the captured switch precondition | Happy | Toggle known switch | Exact master If-Match; advancing receipt | Frontend API | ✅ `frontend/src/lib/api/__tests__/notifications.test.ts::sends the captured switch precondition` |
| NF3 | rejects invalid notification acknowledgements | Error | Wrong history/version/ID | No successful acknowledgement | Frontend API | ✅ `frontend/src/lib/api/__tests__/notifications.test.ts::rejects invalid notification acknowledgements` |
| NF4 | retains the original channel editing base after refresh | Edge | Draft then newer GET | PATCH uses original base and deliberate fields only | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::retains the original channel editing base after refresh` |
| NF5 | requires review after a conflicting channel save | Error | 412 | Draft retained; normal save blocked; explicit review available | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::requires review after a conflicting channel save` |
| NF6 | saves deliberate changes against the reviewed channel | Happy | Same-history authorized review | Revised PATCH uses reviewed base; untouched fields omitted | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::saves deliberate changes against the reviewed channel` |
| NF7 | requires review after an uncertain channel save | Error | Lost/malformed acknowledgement | No automatic retry or success; draft retained for review | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::requires review after an uncertain channel save` |
| NF8 | adopts a replacement channel before editing | Error | Review returns different history/incarnation | Revised save blocked; adoption clears old secret input | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::adopts a replacement channel before editing` |
| NF9 | retires a missing or denied channel editor | Error | Review missing/403 | Private draft removed; no revised write | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::retires a missing or denied channel editor` |
| NF10 | preserves a channel draft when review fails temporarily | Error | Review 503 | Draft retained; retry review available | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::preserves a channel draft when review fails temporarily` |
| NF11 | reviews a failed master switch intent | Error | Master 412/lost response | Intent retained; explicit reviewed save uses master base | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::reviews a failed master switch intent` |
| NF12 | retires a notification review with its session | Edge | Pending review then logout | No old review or draft published | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::retires a notification review with its session` |
| NF13 | refuses an empty selected-printer scope | Error | All printers unchecked, no selection | Save blocked with explanation; no accidental all-printer request | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::refuses an empty selected-printer scope` |
| NF14 | presents legacy empty scopes as all printers | Edge | Existing API scope [] | Display/editor match backend all-printer semantics | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::presents legacy empty scopes as all printers` |
| NF15 | preserves newer observed notification receipts | Edge | Older write acknowledgement follows newer GET | Canonical cache retains newer version/history | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::preserves newer observed notification receipts` |
| NF16 | resolves competing notification editors | Error | Two real editors change channel/switch | Conflict, deliberate review and revised save preserve server state | Playwright real | ✅ `frontend/tests/e2e-real/notifications.spec.ts::resolves competing notification editors` |
| NF17 | requires adoption after the master history changes | Error | Review from restored history | Revised save blocked; explicit adoption displays new state | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::requires adoption after the master history changes` |
| NF18 | preserves a newer observed master receipt | Edge | Old switch acknowledgement after newer GET | Newer observed switch remains canonical | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::preserves a newer observed master receipt` |
| NF19 | adopts current state after history changes during review | Edge | New history observed after review snapshot | Adoption fetches current authoritative state; old preview cannot replace it | Frontend unit | ✅ `frontend/src/components/__tests__/notifications-panel.test.tsx::adopts current state after history changes during review` |

### First-party acceptance results

The transport now requires captured editing bases for channel and master-switch
writes. Drafts keep the original masked snapshot across background reads; revised
saves submit only deliberate differences against the explicitly reviewed version.
Conflicts and uncertain acknowledgements require review. Recreated identities
require adoption, clearing previous secret input. Adoption fetches through the
canonical query owner so an expired preview cannot replace a newer observation.
Confirmed receipts cannot overwrite a newer observed version/history or resurrect
a channel removed by a later read.

The editor now blocks an empty new selected-printer scope with an explanation.
Existing empty scopes render as All printers, matching the backend's historical
meaning; no backend scope semantics changed.

- Before implementation: four new regressions failed (58 deselected, 6.08 s).
- Initial combined run: 4 failed / 73 passed, 12.56 s. Updated obsolete transport
  argument/full-body expectations and made the recoverable validation test return
  its actual known 400 contract instead of an ambiguous transport error.
- Expanded review run: 1 failed / 72 passed, 10.96 s. It exposed an old confirmed
  receipt resurrecting a subsequently removed channel; the publisher now preserves
  that newer observation. The next component/API run passed 91 cases, 11.64 s.
- The expired-preview regression failed before correction (1 failed, 76 deselected,
  3.24 s). An incomplete call-site edit produced an intermediate failed run;
  after completing adoption through the canonical query owner, the targeted
  selection passed 4 cases (73 deselected, 4.22 s).
- Final component/API selection: **92 passed** (77 component, 15 API), **12.47 s**.
- Real Chromium/FastAPI `notifications.spec.ts`: **1 passed**, 6.1 s body,
  40.3 s including startup. Two editors exercise channel and master-switch
  conflicts, explicit review and revised saves; untouched channel fields survive.
  The master race holds then continues a real request without faking its response.
  This browser run preceded the final adoption correction; it exercises revised
  saves, while adoption is covered by the final component selection.
- Final app/UI/domain typecheck, frontend lint and formatting passed (777 files).
  Vite build passed in 2.31 s, with existing locale-shell/large-chunk warnings.
  No backend files changed in this cutover; backend evidence above is unchanged.

Correctness is supported by these specific assertions. Maintainability comes from
one canonical settings owner, explicit captured edit bases and a shared command
lifetime that keeps credentials outside mutation history. No performance gain is
claimed: explicit adoption deliberately performs a fresh read. Full suites,
coverage and latest-commit remote CI remain final integration work. Notification
workflow acceptance does not close M9 or authorize advancing to M10.
