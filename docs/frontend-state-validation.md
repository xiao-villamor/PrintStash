# Session and transport validation

Base: `710e4eb7`. This records M1/I1 HTTP transport and authentication only. Socket lifetime and asset admission remain separate increments.

The requirements matrix preceded production edits. New session tests demonstrated red before the owning transport, upload and auth transition changes. Existing filename, URL, auth-header, derivative-state, endpoint DTO and asset race tests remain regression evidence; their passing results are not new browser or performance qualification.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | returns fresh JSON on every transport read | Happy | repeat same path | distinct server payloads; two HTTP reads | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::returns fresh JSON on every transport read` |
| 2 | keeps concurrent transport reads independent | Edge | concurrent same-path reads | two independent HTTP outcomes | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::keeps concurrent transport reads independent` |
| 3 | rejects old-session response headers | Error | auth changes before headers | AbortError; current user survives | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::ignores an old session's 401 on a %s read` |
| 4 | rejects old-session response bodies | Error | auth changes during body parse | AbortError; private body never publishes | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::rejects old-session response bodies for $label` |
| 5 | rejects same-account session replacement | Edge | same identity logs in again | old request aborts | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::rejects same-account session replacement` |
| 6 | aborts pending transports on logout | Edge | logout while transport pending | network signal aborted | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::aborts pending transports on logout` |
| 7 | preserves caller cancellation | Error | caller aborts | caller abort reason survives | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::preserves caller cancellation` |
| 8 | preserves current-session unauthorized errors | Error | current session receives 401 | coded ApiError; session expiry latch | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::preserves current-session unauthorized errors` |
| 9 | ignores unauthorized bodies from retired sessions | Error | identity changes during 401 body | new session remains signed in | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::ignores unauthorized bodies from retired sessions` |
| 10 | rejects retired mutation acknowledgements | Error | identity changes while mutation pending | no data published; new queries not invalidated | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::rejects retired mutation acknowledgements for $label` |
| 11 | rejects retired protected bytes | Error | session changes during blob/text read | private bytes rejected | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::rejects old-session response bodies for $label` |
| 12 | aborts retired upload progress | Edge | session changes during XHR transfer | transfer aborts; progress stops | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::aborts retired upload progress` |
| 13 | retains verified identity after stale refresh | Edge | new login while old getMe pending | new identity remains stored | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-provider.test.tsx::retains verified identity after stale refresh` |
| 14 | retains verified identity after stale logout | Edge | new login while logout pending | new identity remains stored | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-provider.test.tsx::retains verified identity after stale logout` |
| 15 | retires sessions when browser storage is unavailable | Error | storage writes fail | old session work cancelled | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-store.test.ts::retires sessions when browser storage is unavailable` |
| 16 | retires bootstrap when validated identity changes | Edge | stored id differs from server id | new session generation; old queries cleared | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-store.test.ts::retires bootstrap when validated identity changes` |
| 17 | ignores a retired auth response | Error | identity/API-key read; deferred 401 | AbortError; new identity preserved | Frontend unit | ✅ `frontend/src/lib/api/__tests__/auth.test.ts::ignores a retired $label response` |
| 18 | never saves retired export bytes | Error | export/archive body pending during logout | no anchor click; no object URL | Frontend unit | ✅ `frontend/src/lib/api/__tests__/models/transfer.test.ts::never saves retired $label bytes` |
| 19 | discards a retired multipart acknowledgement | Error | upload cover/delete cover/unstar; late response | no invalidation of new Query state | Frontend unit | ✅ `frontend/src/lib/api/__tests__/multipart-models.test.ts::discards a retired $label acknowledgement` |
| 20 | discards a retired chunk acknowledgement | Error | API chunk pending during logout | network cancellation; no upload status publication | Frontend unit | ✅ `frontend/src/lib/api/__tests__/artifact-uploads.test.ts::discards a retired chunk acknowledgement` |
| 21 | stops an upload retired during hashing | Error | hash pending during logout | upload creation never sent; no remembered id | Frontend unit | ✅ `frontend/src/lib/__tests__/artifact-upload.test.ts::stops an upload retired during hashing` |
| 22 | never records a signed-part receipt after session retirement | Error | native PUT pending during logout | no receipt/finalize sent | Frontend unit | ✅ `frontend/src/lib/__tests__/artifact-upload.test.ts::never records a signed-part receipt after session retirement` |
| 23 | keeps same-session metadata refresh silent | Edge | verified same id; changed role metadata | no auth-change notification | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-store.test.ts::keeps same-session metadata refresh silent` |
| 24 | rejects stale login identity publication | Error | later login replaces pending getMe | later identity stays displayed | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-provider.test.tsx::rejects stale login identity publication` |
| 25 | rejects old thumbnail bytes without erasing the new session's pending request | Error | old/new same-path assets resolve out of order | AbortError; new asset request survives | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::rejects old thumbnail bytes without erasing the new session's pending request` (existing assertion updated for cancellation type) |
| 26 | retires the displayed session while logout awaits the server | Edge | logout response pending | signed-out UI immediately | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-provider.test.tsx::retires the displayed session while logout awaits the server` |
| 27 | publishes no provisional identity during login verification | Edge | login success; getMe pending | no stored id=0 session | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-provider.test.tsx::publishes no provisional identity during login verification` |

The compatibility options `fresh` and `invalidateApiCache(path)` remain for current endpoint wrappers and test setup. They no longer own JSON freshness. `requestMutation` validates acknowledgement before invalidating Query state; feature mutation owners replace this bridge by I10.

Direct-fetch inventory migrated here: auth identity/API keys; Model deletion/purge/star removal; Multipart cover/star changes; printer-file deletion; Model/library/backup exports; provenance cover upload; Artifact upload creation/status/plan/chunks/sign/receipt/finalize/abort; interactive search deadlines; signed native upload PUT. Binary bodies remain inside the session scope through download publication. Existing `asset-cache` promise identity guards remain in place.

Pending later ownership: event/printer socket ticket and live connection lifetime (M7), authenticated loader/camera admission and asset leases (M6). These are not claimed qualified by this HTTP checkpoint. The original dirty checkout was not used as implementation input.

Evidence:

- Initial request red run: 17 failed, 26 passed (43 tests).
- Native upload red run: 2 failed, 8 passed (10 tests).
- Auth transition red run: 2 failed, 22 passed (24 tests).
- HTTP/API/auth/upload/asset regression lane: 553 passed (44 files), before the final two auth transition tests.
- Final transport/auth/upload/asset/query/hygiene checks: **137 passed (7 files)**, including both auth transition regressions.
- `pnpm typecheck`: green for app, UI and domain packages.
- `pnpm lint --deny-warnings`: green, zero diagnostics.
- `pnpm format:check` and `git diff --check`: green.
- No full frontend coverage, browser, backend, performance or CI result is claimed by this checkpoint.
