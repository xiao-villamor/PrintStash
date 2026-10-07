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

## Conditional backend contract — next increment

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
The first-party frontend carries the read/receipt fields but does not submit
conditional edits yet; no end-user conflict-protection claim is made at this point.

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

Remaining work is the first-party conditional write cutover, captured draft bases,
conflict/uncertain recovery, and real-browser proof. The existing notification
scope implementation also treats an empty printer ID list as all printers
(`notifications._channel_subscribes`); review the editor's empty-selection behavior
at that cutover rather than claiming that explicit-empty already means no printers.
No application performance improvement or remote CI completion is claimed here.
