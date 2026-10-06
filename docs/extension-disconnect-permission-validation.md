# Extension disconnect permission validation

Disconnect must remove optional vault access, including local-network vaults. The
extension's built-in grants are limited to the exact HTTP localhost, 127.0.0.1 and
[::1] hosts declared by wxt.config.ts. Local URL classification also includes LAN
addresses, local names and HTTPS loopback, so it is not the permission contract.

This checkpoint owns popup.ts, tests/popup.test.ts and this document only. It keeps
URL normalization, requested origins, connection cancellation, credential removal
ordering and the manifest unchanged. Credentials are removed before attempting to
release optional host access. A failed credential removal preserves the connection.

## Requirement matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | removes optional local vault access on disconnect | Happy | Paired LAN IPv4, .local, single-label, subdomain.localhost, ULA IPv6, alternate loopback IPv4 and HTTPS loopback URLs | Exact optional origin removed/rechecked; credential cleared; successful disconnect text | Frontend unit | ✅ browser-extension/tests/popup.test.ts::removes optional local vault access on disconnect |
| 2 | explains retained built-in loopback access | Edge | HTTP localhost, 127.0.0.1 and [::1] with custom ports | Credentials cleared; no removal of required grants; built-in text | Frontend unit | ✅ browser-extension/tests/popup.test.ts::explains retained built-in loopback access |
| 3 | explains an optional local permission that the browser retained | Error | Optional LAN grant remains or removal throws | Credentials cleared; site-access recovery guidance instead of built-in claim | Frontend unit | ✅ browser-extension/tests/popup.test.ts::explains an optional local permission that the browser retained |
| 4 | keeps the previous connection when disconnect storage fails | Error | Credential storage removal rejects | Previous connection remains; permissions untouched | Frontend unit | ✅ browser-extension/tests/popup.test.ts::keeps the previous connection when disconnect storage fails |
| 5 | restores a paired device credential and clears it on disconnect | Happy | Remote-domain paired vault | Credential cleared; exact remote origin removed | Frontend unit | ✅ browser-extension/tests/popup.test.ts::restores a paired device credential and clears it on disconnect |
| 6 | ships the popup, icons, and only the intended permission surface | Edge | Generated Chrome manifest | Exact built-in HTTP hosts and optional host patterns remain unchanged | Unit | ✅ browser-extension/tests/manifest.test.ts::ships the popup, icons, and only the intended permission surface |

## Validation

Matrix authored before tests. The corrected RED run against unchanged production
reported 11 expected failures and three passing built-in controls (3.53 seconds).
Nine optional-vault cases skipped permission removal, and two retained-permission
cases skipped recovery guidance. The first run also exposed a test selector mistake:
asserting the whole status container included its heading. The corrected run asserts
the message element and reproduces only the intended permission behavior.

The implementation now classifies only the manifest's exact HTTP loopback hosts as
built-in. Formatting, lint, type checks and Chrome/Firefox/Edge builds passed. The
full extension suite passed 272 tests across 13 files (10.18 seconds), including all
14 new permission cases and the existing storage-failure, capture-cancellation and
manifest controls. No new native-browser execution claim is made for these
browser-boundary tests.
