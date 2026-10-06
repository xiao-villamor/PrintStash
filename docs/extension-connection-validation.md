# Extension connection lifecycle validation

This checkpoint closes the popup connection-establishment gap recorded in
[extension capture validation](extension-capture-validation.md). Capture operations
retain their separate owner and frozen vault/credential contract.

The popup owns one connection transition at a time. Verification is cancellable:
Cancel restores the previous UI and aborts its HTTP requests. Guards before each
request and after verification prevent stale headers or bodies from publishing.
The transition keeps its lock until permission cleanup settles, so a retry cannot
lose its same-origin permission to an older attempt. Editing is disabled during
that cleanup; importing into the restored connection remains available.

Once verification succeeds, Cancel is disabled for storage publication and
permission cleanup. One `storage.local.set` publishes the vault and matching
credential fields. Inactive credential kinds are replaced with null, and the
existing initialization parser accepts only string credentials. No second secret
store or cached credential was introduced. The fake-browser storage getter omits
null entries; reload tests assert the resulting usable credential set.

Initialization disables manual setup until stored settings and prepared setup
have been read. Disconnect disables connection controls while removing stored
credentials and permissions; a storage failure retains the previous connection.

## Behaviour matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | preserves the previous connection after cancelled verification | Edge | Claim HTTP resolves with 200 or 400 after Cancel | Old storage and UI survive; aborted HTTP; only new permission cleaned | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::preserves the previous connection after cancelled verification` |
| 2 | stops a cancelled health check before sending credentials | Edge | Cancel while health awaits | No claim request; old connection remains | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::stops a cancelled health check before sending credentials` |
| 3 | ignores a cancelled response body after headers arrive | Edge | Claim headers arrive; body held until after Cancel | No stale credential publication or status | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::ignores a cancelled response body after headers arrive` |
| 4 | finishes cancelled permission cleanup before a same-origin retry | Edge | Permission grant and removal delayed across Cancel | Retry waits; eventual new connection retains its permission | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::finishes cancelled permission cleanup before a same-origin retry` |
| 5 | keeps publication exclusive until storage settles | Edge | Deferred storage set | Cancel disabled until coherent new vault and credential are published | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::keeps publication exclusive until storage settles` |
| 6 | preserves the previous connection when publication fails | Error | Storage rejects | Old storage remains; editable recovery and error shown | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::preserves the previous connection when publication fails` |
| 7 | replaces the previous credential kind in one publication | Happy | Device-to-key and key-to-device updates | Only new secret kind remains; popup reload connects to new vault | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::replaces the previous credential kind in one publication` |
| 8 | locks setup while initialization reads are pending | Edge | Delayed stored settings | Manual connection stays disabled until read completes | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::locks setup while initialization reads are pending` |
| 9 | locks setup while prepared setup is pending | Edge | Delayed active-tab setup read | Manual connection stays disabled until setup read completes | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::locks setup while prepared setup is pending` |
| 10 | preserves the previous connection after a failed update | Error | Denied host permission or rejected pairing code | Previous credential retained; error and retry controls shown | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::preserves the previous connection after a failed update` |
| 11 | locks connection controls during disconnect | Edge | Delayed stored credential removal | No competing Cancel/update; final disconnected state | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::locks connection controls during disconnect` |
| 12 | keeps the previous connection when disconnect storage fails | Error | Stored credential removal rejects | Credential retained; error shown; controls recover | Frontend unit | ✅ `browser-extension/tests/popup.test.ts::keeps the previous connection when disconnect storage fails` |
| 13 | cancels native connection verification before publication | Edge | Loaded Chrome; claim response held across Cancel | Native request abort; previous stored vault/credential and UI survive | E2E | ✅ `browser-extension/tests/e2e/loaded-extension.e2e.ts::cancels native connection verification before publication` |

## Evidence and limits

The original implementation reproduced stale success publication, stale failure
feedback, a claim after cancelled health verification, enabled Cancel during
storage publication, and enabled manual setup during initialization. The first
red run also exposed test-local storage replacements leaking across cases; the
new tests use restored spies at that browser boundary.

The first full popup run passed 61 tests. The final gate passed formatting
(47 files), lint (zero warnings/errors), TypeScript, Chrome/Firefox/Edge MV3 builds,
and all 194 tests in 13 files (64 popup cases). The loaded Chrome spec passed
all four cases using the existing Chrome 148 driver and launcher, native extension
APIs, and controlled loopback HTTP. No browser API was replaced in that spec.
Run logs: `/tmp/extension-connection-final-gates.log` and
`/tmp/extension-connection-browser.log`.

This is a per-popup transition contract, not a cross-window transaction. A pairing
claim may already have reached the server before Cancel; cancellation does not
revoke a device created there. Settings remains the place to revoke that device.
A browser permission prompt or response body that never settles can hold editing
until it settles or the popup is reopened. Native fetch cancellation normally
settles promptly. No Firefox/Edge loaded-browser qualification or live provider
capture is claimed by this checkpoint.
