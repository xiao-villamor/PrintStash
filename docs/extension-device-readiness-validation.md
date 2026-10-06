# Extension device readiness presentation

This checkpoint removes the invented Member role from opaque browser pairings and
names the health-only API for vault reachability. It preserves Connected, current
capture capabilities, connection cancellation and authentication-failure recovery.

## Contract and scope

`/api/v1/health` is public liveness. It establishes server reachability and identity,
not device validity or user role. Stored paired credentials remain checked by the
capture endpoint when used. A successful pairing claim issues a credential but does
not return a user profile. Legacy login checks `/auth/me` and has a verified profile.
Only that profile supports displaying Admin or Member.

The exact manifest is core.ts, tests/core.test.ts, popup.ts, tests/popup.test.ts under
browser-extension, plus this document. No backend route, protocol, dependency or
additional authentication probe is introduced.

## Requirement matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | displays paired browser readiness without inventing a user role | Happy | Stored credential or newly claimed pairing; healthy server; no user profile | Connected with Paired browser and host only; capture available | Frontend unit | ✅ browser-extension/tests/popup.test.ts::displays paired browser readiness without inventing a user role |
| 2 | preserves the verified legacy account role | Happy | Legacy login returns admin or member profile | Exact username, host and verified role displayed | Frontend unit | ✅ browser-extension/tests/popup.test.ts::preserves the verified legacy account role |
| 3 | checks vault reachability without authenticating a device | Happy | Healthy PrintStash endpoint | Normalized base returned; only credential-free health GET; no user profile invented | Unit | ✅ browser-extension/tests/core.test.ts::checks vault reachability without authenticating a device |
| 4 | rejects an unusable vault reachability response | Error | HTTP failure, incorrect identity, unhealthy status or unreadable JSON | Existing safe server error; no connection result | Unit | ✅ browser-extension/tests/core.test.ts::rejects an unusable vault reachability response |
| 5 | retires the connection after rich capture authentication failure | Error | Current paired capture receives401 | Reconnect recovery; capture disabled; persisted credentials retained | Frontend unit | ✅ browser-extension/tests/popup.test.ts::retires the connection after rich capture authentication failure |
| 6 | keeps the connection after rich capture permission failure | Error | Current paired capture receives403 | Capture error; connection available | Frontend unit | ✅ browser-extension/tests/popup.test.ts::keeps the connection after rich capture permission failure |
| 7 | preserves the previous connection after cancelled verification | Edge | Cancel while replacement verification headers/body pending | Previous paired details retained; no late publication | Frontend unit | ✅ browser-extension/tests/popup.test.ts::preserves the previous connection after cancelled verification |
| 8 | uses a paired browser credential for capture without legacy login fields | Happy | Paired device captures a direct source | Original device credential on capture request; no legacy login | Unit | ✅ browser-extension/tests/core.test.ts::uses a paired browser credential for capture without legacy login fields |

## Validation

The nine new cases ran against unchanged production first: two expected failures
showed the invented Member label for stored and newly claimed pairings; seven
legacy-role and public-health controls passed (3.01 seconds). After the change,
formatting, lint and type checks passed, as did Chrome/Firefox/Edge builds and all
258 extension tests across 13 files (10.31 seconds). The existing paired-device backend contract remains the authority
for credential acceptance and revocation, rather than this public health check.
