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

Supported-browser followup requirements before implementation:

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 28 | works without AbortSignal.any | Happy | browser has AbortController but lacks any | request succeeds | Frontend unit | ✅ `frontend/src/lib/__tests__/session-transport.test.ts::works without AbortSignal.any` |
| 29 | releases caller cancellation after completion | Edge | request completed; caller later aborts | completed scope signal remains live | Frontend unit | ✅ `frontend/src/lib/__tests__/session-transport.test.ts::releases $label cancellation after completion` |
| 30 | releases session cancellation after completion | Edge | request completed; session later retires | completed scope signal remains live | Frontend unit | ✅ `frontend/src/lib/__tests__/session-transport.test.ts::releases $label cancellation after completion` |
| 31 | preserves an already cancelled caller's reason | Error | cancelled before starting | exact reason; operation never starts | Frontend unit | ✅ `frontend/src/lib/__tests__/session-transport.test.ts::preserves an already cancelled caller's reason` |
| 32 | preserves the first cancellation reason | Error | caller cancels, then session retires | caller's original reason wins | Frontend unit | ✅ `frontend/src/lib/__tests__/session-transport.test.ts::preserves the first cancellation reason` |

Browser compatibility source: [WebKit Safari 17.4 release](https://webkit.org/blog/15063/webkit-features-in-safari-17-4/) explicitly introduces `AbortSignal.any`; the manifest supports Safari/iOS 16.4. The request scope now uses `AbortController` and event listeners with cleanup, without `any` or `throwIfAborted`.

## Manual review ledger

Each listed path was opened and its concrete implementation/assertions read; test execution and search inventories are recorded separately above. Config files were reviewed without changes.

| Exact path | Symbols / notes inspected |
|---|---|
| `frontend/src/lib/session-transport.ts` | epoch rotation, caller/session composition, completed-scope cleanup |
| `frontend/src/lib/api/request.ts` | JSON/actions/forms/XHR; headers/body/error fences; artifact proxy retry; binary/text/download publication; compatibility invalidation |
| `frontend/src/lib/auth-store.ts` | auth events; silent identity comparison; storage exceptions; once-only unauthorized latch |
| `frontend/src/lib/auth-provider.tsx` | bootstrap checking lifetime; verified login; immediate logout; stale refresh failure |
| `frontend/src/lib/query-client.ts` | Query defaults/key roots; auth clear; compatibility mutation path fanout |
| `frontend/src/lib/api/auth.ts` | getMe/API-key direct reads; cookie-only headers; auth endpoint DTOs |
| `frontend/src/lib/api/models.ts` | star removal, purge normalization, file deletion, exports and archive downloads |
| `frontend/src/lib/api/multipart-models.ts` | local cover PUT/DELETE and star DELETE; post-ack invalidation |
| `frontend/src/lib/api/printers.ts` | printer-file deletion; ticket-to-WebSocket construction seam |
| `frontend/src/lib/api/backup.ts` | protected backup download, server filename, URL release |
| `frontend/src/lib/api/provenance.ts` | multipart source-cover PUT |
| `frontend/src/lib/api/artifact-uploads.ts` | durable upload DTOs; creation idempotency header; native/chunk acknowledgement |
| `frontend/src/lib/api/search.ts` | deadline ownership; parent abort relay; parsed image/text requests; timer cleanup |
| `frontend/src/lib/artifact-upload.ts` | hashing, upload plan, signed part receipt, progress, pause and finalize fences |
| `frontend/src/lib/asset-cache.ts` | inflight promise identity, URL revocation, auth reset; leases/admission deferred |
| `frontend/src/lib/api/__tests__/request.test.ts` | real deferred headers/bodies; mutations; XHR; error and filename contracts |
| `frontend/src/lib/api/__tests__/auth.test.ts` | identity/API-key fresh read assertions and old 401 regression |
| `frontend/src/lib/api/__tests__/models/transfer.test.ts` | filename/save/revoke contracts; retired deferred download bytes |
| `frontend/src/lib/api/__tests__/multipart-models.test.ts` | payload/cover/delete/star wire assertions; retired acknowledgement |
| `frontend/src/lib/api/__tests__/artifact-uploads.test.ts` | creation hash identity, chunk envelope, native receipt; retired chunk |
| `frontend/src/lib/api/__tests__/search.test.ts` | all interactive deadline variants; route cancellation; errors; management DTOs |
| `frontend/src/lib/__tests__/auth-store.test.ts` | unauthorized latch, token non-persistence, cross-tab filter, storage failure |
| `frontend/src/lib/__tests__/auth-provider.test.tsx` | rendered bootstrap/login/logout/refresh transitions and stale-session races |
| `frontend/src/lib/__tests__/artifact-upload.test.ts` | native/chunk paths, pause, hashing fallback, creation DTO, retired hash/receipt |
| `frontend/src/lib/__tests__/asset-cache.test.ts` | inflight ordering regression and AbortError assertion |
| `frontend/src/lib/__tests__/session-transport.test.ts` | unsupported-any stand-in, first abort reason, cleanup and pre-cancelled request |
| `frontend/src/test-support/fetch-backed-xhr.ts` | POST recording, asynchronous body handoff, abort and progress test boundary |
| `frontend/package.json` | browser floors; exact scripts; pinned manager and resolved-compatible dependency ranges |
| `frontend/vite.config.ts` | same-origin API/WS proxy; jsdom test inclusion; coverage scope/exclusions |
| `frontend/tsconfig.json` | strict checking, DOM API typing, bundler resolution and application/test inclusion |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 33 | preserves a genuine unauthorized upload error | Error | established session; upload creation 401 | original coded ApiError; session retired | Frontend unit | ✅ `frontend/src/lib/__tests__/artifact-upload.test.ts::preserves a genuine unauthorized upload error` |

A verified current request failure that retires authentication is carried unchanged through enclosing upload/workflow scopes. `expireSessionForFailure` first checks the captured incarnation, then marks the exact Error in a WeakSet before expiring auth. A stale response never acquires this marker because header/body/version fences reject it first. This preserves the server's current 401 contract without allowing an old 401 to expire a newer session.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 34 | ignores a retired upload's unauthorized response | Error | upload creation headers deferred; new login; old401 | AbortError; new user preserved | Frontend unit | ✅ `frontend/src/lib/__tests__/artifact-upload.test.ts::ignores a retired upload's unauthorized response` |

Browser followup evidence: scope red **4 failed / 1 passed**; genuine nested upload401 red **1 failed / 10 passed**. Final followup: **107 passed (6 files)**; app/UI/domain typecheck green; lint zero diagnostics; format:check green.
