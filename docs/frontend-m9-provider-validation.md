# M9 provider accounts and paired browsers

Status: locally accepted provider/browser workflow, pending final branch CI.
Prerequisite M8 is accepted at `ea144c7c`. This does not accept the remaining
M9 administration/entry routes.

## Current evidence and intended increment

Complete source reads: `components/provider-connections-panel.tsx`, its component
mirror, `lib/api/provider-connections.ts`, and the relevant provider router names.
The panel is the only first-party consumer of these account/device reads.
`refresh` combines two requests, copies both DTO lists, has no loading contract,
and cannot cancel after disposal. Errors leave the UI presenting disconnected
providers and no devices as though those were successful results. A held OAuth
response still calls navigation after leaving the panel. Revoke synthesizes a
local timestamp rather than reconciling the authoritative device list.

Move the two independent reads to canonical Query owners. A domain command owner
will serialize active gestures, capture session identity, cancel obsolete reads,
and publish or revalidate non-secret receipts. Cults credentials and pairing codes
must stay outside Query data and mutation history. Keep confirmation for disconnect
and revoke; loading/error/denial must never become empty/disconnected success.
Use caller-owned abort signals for transports. Keep the navigation handoff local
to the active panel. No generic CRUD hook or new runtime is required.

The original device-name form also lacked a conditional-write contract. The
backend/client increments below now cover it. Remaining M9 seams are notifications, remote connection/source
edit contracts, and Settings health/release/trash/GC reads. These are subsequent
bounded workflows, not parallel implementation tracks.

Rollback the provider query/command owner and panel together. The backend OAuth,
credential storage, pairing and authorization contracts remain authoritative.

## Coverage matrix (initial plan, reassessed after focused verification)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| PC1 | waits for provider authority before exposing connect actions | Edge | Initial reads pending | Loading shown; no disconnected/empty success | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::waits for provider authority before exposing connect actions` |
| PC2 | retries failed provider reads explicitly | Error | Provider GET fails | Error plus retry; accepted later read renders connections | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::retries failed provider reads explicitly` |
| PC3 | cancels provider reads after disposal | Edge | Held read; panel unmounts | Request signal aborted; no late state publication | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::cancels provider reads after disposal` |
| PC4 | refuses a retired OAuth navigation | Edge | OAuth POST settles after unmount | No navigation | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::refuses a retired OAuth navigation` |
| PC5 | refuses commands from an earlier session | Error | Session changes while command awaits | No old callback, code, credential or new-session write | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::refuses an OAuth handoff from an earlier session`, `src/components/__tests__/provider-connections-panel.test.tsx::refuses a credential receipt from an earlier session`, `src/components/__tests__/provider-connections-panel.test.tsx::refuses pairing receipts from an earlier session` |
| PC6 | preserves a confirmed connection against an older read | Edge | Read overlaps Cults acceptance | Canonical connection stays accepted | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::preserves a confirmed connection against an older read` |
| PC7 | keeps credentials outside shared caches | Happy | Cults password submitted | Transport receives credentials; Query data/mutations contain no secret | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::connects Cults from newly entered credentials without retaining them` |
| PC8 | keeps pairing codes local to the active panel | Happy | Pairing accepted | Code shown locally; not in Query data/mutations | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::shows a temporary pairing code and never a device credential` |
| PC9 | preserves a failed disconnect confirmation | Error | Disconnect rejected | Dialog stays open with failure; no invented disconnected state | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::preserves a failed disconnect confirmation` |
| PC10 | reconciles device revocation from the server | Happy | Confirmed revoke | Authoritative revoked device rendered; no fabricated timestamp | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::reconciles device revocation from the server` |
| PC11 | blocks duplicate overlapping gestures | Edge | One command pending | No second write | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::blocks duplicate overlapping gestures` |
| PC12 | retires unauthorized private rows | Error | Refetch returns 401/403 | No connection/device details or actions remain visible | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::retires unauthorized private rows` |
| PC13 | retains the device-name draft across reads | Edge | Device list refreshes during editing | Draft and original editing base preserved | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::retains the original browser base during refresh` |
| PC14 | rejects a competing browser name edit | Error | Two editors use the same base | Conflict; retained draft and explicit authorized review | Integration / Playwright | ✅ `tests/e2e-real/provider-connections.spec.ts::detects competing edits in real browsers` |
| PC15 | preserves extension pairing compatibility | Happy | Existing pairing/claim/revoke contracts | Existing extension/server clients remain usable | Frontend unit / Playwright | ✅ `browser-extension/tests/core.test.ts::claims a pairing code and retains only the returned browser credential`, `tests/e2e-real/provider-connections.spec.ts::detects competing edits in real browsers` |
| PC16 | retains browser-name input after a transient read failure | Error | Edited name, then 503 refresh | Draft remains visible and read-only; retry restores actions | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::retains browser-name input after a transient read failure` |

| PC17 | preserves a failed revocation confirmation | Error | Revoke rejected | Failure visible inside retained dialog; device stays active | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::preserves a failed revocation confirmation` |

## Validation

Read/lifetime increment implemented: canonical Query reads, native cancellation,
secret-free command history, session/disposal checks, authoritative revocation
reconciliation, retained drafts during transient read failures, and failure text
inside the retained disconnect/revoke confirmation.

Latest focused run: component mirror plus `lib/api/__tests__/provider-connections.test.ts`,
**26 passed, 5.18 s**. Frontend format check (774 files), lint with denied warnings,
and app/UI/domain typechecks passed. No backend code changed in this increment.
No browser, extension, build, full suite, coverage or performance claim.

Preserved failure evidence:
- Initial two regressions: retired OAuth navigation and actions before authority
  failed before the Query/lifetime refactor (2 failed, 3.96 s).
- Retained draft after transient 503 failed before preserving mounted rows
  (1 failed, 14 skipped, 3.12 s).
- Dialog failure visibility failed before adding the error to its description
  (1 failed, 14 skipped, 2.91 s). An initial accessible-description assertion
  was corrected to inspect dialog content because the existing primitive does
  not expose `aria-describedby`.
- The added revocation test initially expected raw exception text; the existing
  error translator intentionally sanitizes that text. The fixture now uses the
  same stable error code as the disconnect test (1 failed, 25 passed, 5.66 s).

At the read-owner checkpoint PC5 and PC13–PC15 remained incomplete. The frontend
cutover below closes those rows; M9 as a whole remains open. The pre-change backend inspection confirmed
`rename_device` overwrote `name` unconditionally. The following additive backend
increment addresses that contract; the frontend cutover is recorded below.
`last_used_at` is telemetry and must not serve as the editing base. Pairing can reuse revoked rows, so the eventual
contract must also distinguish a new pairing incarnation.


## Browser-name conditional contract

Use the existing `conditional-v1` / `If-Match` protocol. A device editing base
contains a persisted monotonic version and an opaque history identifier bound to
the database epoch, user, device ID and current credential incarnation. Do not
expose the credential hash. A trigger advances versions for name, owner,
credential or revocation changes; last-use telemetry is excluded. The per-user
write lock already used for pairing also serializes renames and duplicate-name
checks. The command checks active session authority under that lock. Legacy
callers remain accepted but invalidate older conditional drafts.

First-party forms will capture the original device/base, preserve drafts after
refetch, and require explicit review after conflict or uncertain outcome. A
replacement pairing must be adopted rather than inheriting a stale draft.
Rollback the new frontend editor before removing the additive server contract;
never leave an opted-in client writing without a precondition.

### Browser edit coverage matrix before implementation

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| BD1 | publishes an opaque editing base | Happy | Paired device read | Positive version, opaque history, no credential hash | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_publishes_an_opaque_editing_base` |
| BD2 | accepts the captured browser base | Happy | Current If-Match | Name saved; receipt advances | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_accepts_the_captured_browser_base` |
| BD3 | refuses an obsolete browser base | Error | Two editors share a base | Second edit 412; winner retained | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_refuses_an_obsolete_browser_base` |
| BD4 | requires the opted-in browser base | Error | conditional-v1 without If-Match | 428; no write | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_requires_the_opted_in_browser_base` |
| BD5 | rejects malformed browser preconditions | Error | Wildcard, wrong kind/ID, oversized version, unknown contract | 400/412; no write | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_rejects_malformed_browser_preconditions` |
| BD6 | invalidates drafts after legacy edits | Edge | Unconditional write changes name | Old conditional draft refused | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_invalidates_drafts_after_legacy_edits` |
| BD7 | ignores browser-use telemetry | Edge | Credential used while editor open | Captured base remains usable | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_ignores_browser_use_telemetry` |
| BD8 | refuses a draft after re-pairing | Error | Revoked row reused with fresh credential | Old draft refused; new pairing unchanged | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_refuses_a_draft_after_re_pairing` |
| BD9 | refuses editing a revoked browser | Error | Current base for revoked device | Conflict; name unchanged | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_refuses_editing_a_revoked_browser` |
| BD10 | reports a duplicate browser name | Error | Another row owns name | 409; original name/base retained | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_reports_a_duplicate_browser_name` |
| BD11 | hides another account's editing authority | Error | Foreign device ID and matching base | 404; no write or private receipt | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_hides_another_accounts_editing_authority` |
| BD12 | invalidates drafts across restored history | Error | Epoch changes | Old draft refused | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_invalidates_drafts_across_restored_history` |
| BD13 | versions direct writes | Edge | SQL name changes away then back | Old base rejected despite matching name | Integration | ✅ `integration/api/v1/provider_connections/test_browser_edits.py::TestBrowserEditing::test_versions_direct_writes` |
| BD14 | preserves device data through migration | Edge | Previous revision, device rows | Upgrade/downgrade/upgrade retain credentials and names; triggers installed | Integration | ✅ `integration/db/migrations/test_browser_edit_versions.py::TestBrowserEditMigration::test_preserves_device_data_through_migration` |
| BD15 | arbitrates simultaneous device edits | Edge | Independent sessions share base on SQLite/PostgreSQL | Exactly one winner, one conflict | Integration | ✅ `integration/modules/ingestion/test_browser_edits.py::TestRename::test_arbitrates_simultaneous_device_edits` |
| BD16 | retains a draft for explicit review | Error | Save conflict or uncertain response | No automatic retry; review current state before revised save | Frontend unit / Playwright | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::requires review after a browser conflict`, `src/components/__tests__/provider-connections-panel.test.tsx::keeps review failure recoverable` |
| BD17 | refuses retired account authority | Error | Account disabled/deleted/trashed/session version changed after authentication | No name change; forbidden | Integration | ✅ `integration/modules/ingestion/test_browser_edits.py::TestRename::test_refuses_retired_account_authority` |
| BD18 | rolls back an uncommitted browser rename | Error | Transaction rolled back | Name and editing base unchanged | Integration | ✅ `integration/modules/ingestion/test_browser_edits.py::TestRename::test_rolls_back_an_uncommitted_browser_rename` |
| BD19 | renders the browser migration offline | Edge | SQLite/PostgreSQL offline SQL | Additive column and version trigger rendered without connection | Integration | ✅ `integration/db/migrations/test_browser_edit_versions.py::TestBrowserEditMigration::test_renders_browser_upgrade_without_a_connection` |

### Backend implementation checkpoint

`modules/ingestion/browser_edits.py` owns the account lock, current authority,
conditional base and name uniqueness. The router captures the receipt before
commit. The schema adds required `edit_epoch`/`edit_version` to public device
receipts. Migration `7af8bb13c174` was autogenerated against an isolated chain-built
SQLite database; its only schema operation adds `browser_devices.edit_version`.
The versioned trigger is shared by fresh installs and upgrades. Existing extension
pairing/claim clients remain compatible with these additive response fields.

- Initial API reproduction: **17 failed, 8.42 s**; preconditions were ignored and
  device receipts had no editing base.
- API conditional cases plus existing pairing endpoint suite: **53 passed, 11.35 s**.
- Concurrent-session owner and migration/offline tests: **14 passed, 118.84 s**,
  covering SQLite and PostgreSQL.
- OpenAPI snapshot: **1 passed, 5.70 s**. Reviewed diff: two additive response fields
  and two optional PATCH headers only.
- Schema/model convergence: **1 passed, 66.86 s**; autogenerate has no remaining
  operations after the new migration.
- Scoped Ruff passed. Configured backend Pyright passed with zero errors. A direct
  invocation naming the normally excluded SQLModel file reports ten existing
  `__tablename__` assignment diagnostics; it is not the configured gate.
- An additional account-trash regression failed before the final authority check
  (**1 failed, 11 deselected, 24.65 s**); SQLite/PostgreSQL regression rerun:
  **2 passed, 10 deselected, 59.03 s**.

This additive server checkpoint alone did not accept the browser editing
workflow; the following frontend cutover provides the missing evidence. Full backend/coverage/PR CI and performance were not run here.


### Frontend cutover coverage matrix before implementation

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| BF1 | sends the captured browser base | Happy | Device draft saved | Native signal and exact conditional headers; unchanged name payload | Frontend unit | ✅ `src/lib/api/__tests__/provider-connections.test.ts::sends the captured browser base` |
| BF2 | refuses an invalid browser acknowledgement | Error | Wrong ID/history or nonadvancing version | No accepted receipt | Frontend unit | ✅ `src/lib/api/__tests__/provider-connections.test.ts::refuses an invalid browser acknowledgement: $label` |
| BF3 | retains the original browser base during refresh | Edge | Draft typed, then newer GET | Original base submitted with retained draft | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::retains the original browser base during refresh` |
| BF4 | requires review after a browser conflict | Error | 412 or uncertain save | Draft retained; ordinary save disabled; no retry until explicit review | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::requires review after a browser conflict` |
| BF5 | saves explicitly revised browser changes | Happy | Fresh review accepted | Revised draft sent once with reviewed base | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::saves explicitly revised browser changes` |
| BF6 | adopts a replacement browser pairing | Edge | Review returns another history | Revised save disabled; adopt current values before another edit | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::adopts a replacement browser pairing` |
| BF7 | retires an inaccessible browser editor | Error | Review denies/misses device | Private draft removed; no save | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::retires an inaccessible browser editor: %s` |
| BF8 | refuses an obsolete browser receipt | Edge | Session changed during rename | No old receipt published | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::refuses an obsolete browser receipt` |
| BF9 | keeps review failure recoverable | Error | Review GET temporarily fails | Retained draft; explicit retry; no write | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::keeps review failure recoverable` |
| BF10 | detects competing edits in real browsers | Error | Two UI editors share original base | First wins; second reviews/revises deliberately | Playwright | ✅ `tests/e2e-real/provider-connections.spec.ts::detects competing edits in real browsers` |


### Frontend cutover and workflow acceptance

The first-party client always sends the captured conditional base and rejects
wrong-identity, wrong-history and non-advancing acknowledgements. BrowserDeviceRow
owns only an edit intent and review state. A fresh list does not replace the
intent's original snapshot. Errors preserve it for explicit review; revised
saves use the reviewed base, and a different pairing history requires adoption.
Denied/missing devices retire the private draft. OAuth, pairing, credential and
name receipts from a retired session cannot publish into the current panel.

Validation:
- Initial frontend reproduction: **2 failed, 16 skipped, 5.08 s**, proving missing
  captured bases and missing review after conflict.
- A native input pattern was initially over-escaped (**3 failed, 15 passed,
  7.77 s**); correcting the JSX string expression restored form submission.
- Expanded tests exposed a fixture that spread a complete device over the submitted
  name (**1 failed, 37 passed, 6.07 s**). The command now receives the immutable
  editing pair explicitly; the fixture preserves the actual payload name.
- Affected component/API files: **40 passed, 6.62 s**, followed by the additional
  missing-device review case: **4 passed, 24 skipped, 3.70 s**, covering
  401/403/404 and a successful read without the device. No suite-wide coverage claim.
- Real Chromium, real FastAPI: **1 passed**, **8.1 s** test / **1.0 min** total.
  The test issues the code through the UI, claims it without an account session,
  drives two editors through conflict/review/revised save, then confirms revocation.
- Existing extension pairing adapter with the new additive response fields:
  **3 passed, 28 skipped, 1.60 s**. This is focused adapter compatibility,
  not a claim that the full loaded-extension suite was run.
- Frontend format (775 files), lint, app/UI/domain typechecks passed. Build passed
  in **2.43 s**, retaining the existing locale-shell/large-chunk notices.
  Extension's changed test passes scoped lint and formatting.

Correctness is supported by the observable contract checks above. Maintainability
improves through one read/command owner and an explicit draft/base/review lifecycle;
there is no component copy of the remote lists or invented revocation timestamp.
No performance improvement is claimed. Full branch CI, coverage and final delivery
remain pending, as do the other M9 workflows named at the start of this document.
