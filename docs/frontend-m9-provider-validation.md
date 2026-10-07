# M9 provider accounts and paired browsers

Status: active. Prerequisite M8 is accepted at `ea144c7c`. This is one M9 workflow,
not acceptance of all administration/entry routes.

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

The device-name form also lacks a conditional-write contract. That backend/client
contract remains required before this workflow can close; read/lifetime fixes do
not waive it. Other remaining M9 seams are notifications, remote connection/source
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
| PC5 | refuses commands from an earlier session | Error | Session changes while command awaits | No old callback, code, credential or new-session write | Frontend unit | ❌ missing |
| PC6 | preserves a confirmed connection against an older read | Edge | Read overlaps Cults acceptance | Canonical connection stays accepted | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::preserves a confirmed connection against an older read` |
| PC7 | keeps credentials outside shared caches | Happy | Cults password submitted | Transport receives credentials; Query data/mutations contain no secret | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::connects Cults from newly entered credentials without retaining them` |
| PC8 | keeps pairing codes local to the active panel | Happy | Pairing accepted | Code shown locally; not in Query data/mutations | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::shows a temporary pairing code and never a device credential` |
| PC9 | preserves a failed disconnect confirmation | Error | Disconnect rejected | Dialog stays open with failure; no invented disconnected state | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::preserves a failed disconnect confirmation` |
| PC10 | reconciles device revocation from the server | Happy | Confirmed revoke | Authoritative revoked device rendered; no fabricated timestamp | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::reconciles device revocation from the server` |
| PC11 | blocks duplicate overlapping gestures | Edge | One command pending | No second write | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::blocks duplicate overlapping gestures` |
| PC12 | retires unauthorized private rows | Error | Refetch returns 401/403 | No connection/device details or actions remain visible | Frontend unit | ✅ `src/components/__tests__/provider-connections-panel.test.tsx::retires unauthorized private rows` |
| PC13 | retains the device-name draft across reads | Edge | Device list refreshes during editing | Draft and original editing base preserved | Frontend unit | ❌ missing |
| PC14 | rejects a competing browser name edit | Error | Two editors use the same base | Conflict; retained draft and explicit authorized review | Integration / Playwright | ❌ missing |
| PC15 | preserves extension pairing compatibility | Happy | Existing pairing/claim/revoke contracts | Existing extension/server clients remain usable | Contract / Playwright | ❌ missing |
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

PC5 is still incomplete: the pairing-session regression passes, but this is not
proof for every credential/OAuth command. PC13–PC15 remain required; no claim
of a complete provider workflow or M9 acceptance. Backend inspection confirms
`rename_device` reads a user-scoped row then overwrites `name` unconditionally.
`BrowserDevice` has no edit version; `last_used_at` is telemetry and must not
serve as the editing base. Pairing can reuse revoked rows, so the eventual
contract must also distinguish a new pairing incarnation.
