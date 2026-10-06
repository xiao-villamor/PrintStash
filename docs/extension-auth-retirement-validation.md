# Extension authentication retirement validation

The capture endpoints use `require_capture_actor`: invalid/revoked device credentials
and invalid/stale JWTs return HTTP 401. Resource visibility is checked separately
(with 404 for invisible items/slots). Arbitrary HTTP 403 remains a scoped request
failure and must not invalidate the browser connection.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | rejects direct capture authentication failures | Error | 401 invalid browser, invalid JWT, malformed body | Typed safe failure | Unit | ✅ `browser-extension/tests/core.test.ts::rejects direct capture authentication failures` |
| 2 | keeps direct capture permission failures scoped | Error | 403 | Ordinary error, no credential retirement | Unit | ✅ `browser-extension/tests/core.test.ts::keeps direct capture permission failures scoped` |
| 3 | rejects rich capture authentication failures | Error | 401 at create, upload, finalize | Typed safe failure; no subsequent upload/finalize | Unit | ✅ `browser-extension/tests/capture-transport.test.ts::rejects rich capture authentication failures` |
| 4 | keeps rich capture permission failures scoped | Error | 403 at create, upload, finalize | Ordinary capture error | Unit | ✅ `browser-extension/tests/capture-transport.test.ts::keeps rich capture permission failures scoped` |
| 5 | preserves authentication failure through owned cleanup | Error | Upload401 then cleanup403 | Original vault/credential; original auth error | Unit | ✅ `browser-extension/tests/capture-transport.test.ts::preserves authentication failure through owned cleanup` |
| 6 | retires the connection after rich capture authentication failure | Error | 401 at create, upload, finalize | Reconnect UI; disabled capture; selectors/inbox cleared; storage retained | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::retires the connection after rich capture authentication failure` |
| 7 | keeps the connection after rich capture permission failure | Error | 403 at create, upload, finalize | Connected UI; scoped recovery | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::keeps the connection after rich capture permission failure` |
| 8 | ignores retired rich authentication failures | Edge | Deferred old401 after vault switch | New connection unchanged; no old credential request to new vault | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::ignores retired rich authentication failures` |
| 9 | retires the connection after direct capture authentication failure | Error | Opaque-device401 | Same reconnect recovery | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::retires the connection after direct capture authentication failure` |
| 10 | enforces the paired-device capture lifecycle | Happy | Real pairing, upload, revoke, recapture | Initial item remains; revoked recapture rejected | E2E | ✅ `browser-extension/tests/ci/real-backend-capture.test.ts::enforces the paired-device capture lifecycle` |

## Ownership and recovery

`CaptureAuthenticationError` is the shared capture transport boundary for HTTP
401. The slot-create, file-upload, finalize and direct-URL endpoints classify the
status without echoing response bodies. Provider reads and HTTP 403 retain their
existing scoped error behavior. Popup stage diagnostics preserve the typed error,
and the current-capture ownership check runs before reconnect recovery.

Recovery retires the active capture, clears candidate/manual-file controls and
stale Pending Imports links, clears the active access token, and displays the
existing reconnect form. Persisted credentials remain available for explicit
recovery; this checkpoint does not silently remove them. A failed rich upload
still attempts bounded dismissal with its original vault and credential before
surfacing the original authentication failure.

## Validation

The first focused red run produced 11 intended authentication failures with eight
permission/obsolete-response cases passing; there were no timeout or harness
failures. After implementation, all 115 affected tests across three files passed
with one worker, including all 19 new cases. Logs: `/tmp/extension-auth-red.log`
and `/tmp/extension-auth-focused.log`.

The coordinator-supervised final gate passed formatting, lint, TypeScript,
Chrome/Firefox/Edge MV3 builds, and all 213 tests in 13 files. The fresh real-backend
paired-device lifecycle passed 1/1: capture succeeds before revocation, recapture
fails with the typed authentication error after revocation, and the earlier Pending
Import remains unchanged. The existing native Chrome smoke passed all four cases
against the final build, including connection cancellation and capture retirement.
Logs: `/tmp/extension-auth-gates.log` and
`/tmp/extension-auth-backend-contract.log`, and `/tmp/extension-auth-browser.log`.

## Remaining separate work

This change does not validate malformed slot acknowledgements, change the
health-only `verifyBrowserDevice` initialization check, introduce cross-popup
credential coordination, or add a device verification endpoint. Those require
separate checkpoints. An already-revoked credential may therefore still display
Connected when the popup opens; an authenticated capture response now consistently
moves it to reconnect recovery.
