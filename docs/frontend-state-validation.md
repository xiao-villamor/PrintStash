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

## Socket lifetime requirements

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 35 | closes an abandoned connection after listeners return | Edge | old factory pending; unsubscribe; new subscribe | old socket closed; new socket delivers | Frontend unit | ✅ `frontend/src/lib/__tests__/events.test.ts::closes an abandoned connection after listeners return` |
| 36 | ignores callbacks from a disposed event connection | Error | old handlers saved; new connection active | no notice/reconnect from old handlers | Frontend unit | ✅ `frontend/src/lib/__tests__/events.test.ts::ignores callbacks from a disposed event connection` |
| 37 | does not retry a failed abandoned factory | Error | old factory rejects after disposal | zero stale retry timers | Frontend unit | ✅ `frontend/src/lib/__tests__/events.test.ts::does not retry a failed abandoned factory` |
| 38 | retires the active event socket on logout | Edge | socket active; logout | socket closed; no new ticket while signed out | Frontend unit | ✅ `frontend/src/lib/__tests__/events.test.ts::retires the active event socket on logout` |
| 39 | reauthorizes event channels after login | Happy | logout/login; persistent listeners | new ticket/socket; subscribed channel; resync delivered | Frontend unit | ✅ `frontend/src/lib/__tests__/events.test.ts::reauthorizes event channels after login` |
| 40 | keeps Model follow cleanup idempotent | Edge | two followers; one unsubscribe twice | remaining follower keeps channel | Frontend unit | ✅ `frontend/src/lib/__tests__/events.test.ts::keeps Model follow cleanup idempotent` |
| 41 | does not open a retired printer ticket | Error | ticket pending; account changes | no WebSocket construction | Frontend unit | ✅ `frontend/src/lib/api/__tests__/printers.test.ts::does not open a retired printer ticket` |
| 42 | cancels an abandoned printer ticket | Error | ticket pending; caller aborts | network signal aborted; no socket | Frontend unit | ✅ `frontend/src/lib/api/__tests__/printers.test.ts::cancels an abandoned printer ticket` |
| 43 | does not reconnect a disposed printer page | Error | unmount emits socket close | no further ws-ticket request | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::does not reconnect a disposed printer page` |
| 44 | rejects an abandoned printer page ticket | Edge | unmount with ticket pending | no live socket created | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::rejects an abandoned printer page ticket` |
| 45 | ignores old printer snapshot callbacks after a switch | Error | printer A callback saved; printer B active | B state remains unchanged | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::ignores old printer snapshot callbacks after a switch` |
| 46 | stops printer callbacks on logout | Edge | active printer socket; auth retirement | socket closes; snapshot clears; no reconnect | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::stops printer callbacks on logout` |

| 47 | aborts the events ticket request | Error | pending ticket; caller abort | fetch signal aborted; late result rejected | Frontend unit | ✅ `frontend/src/lib/api/__tests__/work.test.ts::aborts the events ticket request` |
| 48 | rejects a late events ticket after session retirement | Error | pending ticket; auth retirement | late ticket is never returned | Frontend unit | ✅ `frontend/src/lib/api/__tests__/work.test.ts::rejects a late events ticket after session retirement` |

M7 baseline red: events/printer API **7 failed / 48 passed (2 files)**; printer page lifetime **4 failed / 43 skipped (1 file)**. The retired printer ticket already passes with M1; the other new cases expose connection-owner gaps.

| 49 | cancels the ticket when its last subscriber leaves | Error | default events factory ticket pending; unsubscribe | HTTP aborted; no socket or retry from late response | Frontend unit | ✅ `frontend/src/lib/__tests__/events.test.ts::cancels the ticket when its last subscriber leaves` |
| 50 | reauthorizes the printer socket on a new login | Happy | same printer page; logout then different verified identity | old socket closed; new ticketed socket delivers current snapshot | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::reauthorizes the printer socket on a new login` |

| 51 | stops a notice delivery when a listener retires the session | Edge | earlier subscriber logs out during a frame | later subscriber never receives retired frame | Frontend unit | ✅ `frontend/src/lib/__tests__/events.test.ts::stops a notice delivery when a listener retires the session` |

M7 validation: **166 passed (6 files)**, including the prior request/session transport regression suites. App/UI/domain typecheck green; lint zero diagnostics; repository frontend formatting green. Ticket-specific baseline red **1 failed / 11 passed** (caller signal propagation missing, late-session rejection already protected by M1). New additional cancellation/reauthorization/during-delivery tests were added after implementation, not represented as baseline-red evidence. No browser, performance, coverage or CI claim in this socket checkpoint.

M7 manual review additions:

| Exact path | Symbols / notes inspected |
|---|---|
| `frontend/src/lib/events.ts` | default ticket factory/adapter; connection generation, retry timer, auth subscriber cleanup, per-listener delivery fence, Model reference counts |
| `frontend/src/lib/api/work.ts` | createEventsTicket POST body/headers; caller signal; ordinary Work action contracts preserved |
| `frontend/src/lib/api/printers.ts` | openPrinterWS ticket creation, constructed-socket cleanup and session fence |
| `frontend/src/components/printer-detail.tsx` | initial snapshot/read ownership; effect disposal, auth retirement, socket handlers/reconnect, protected diagnostics/config reads |
| `frontend/src/lib/__tests__/events.test.ts` | fake/default factory paths; drops/backoff/refollow/resync; pending disposal; auth transitions; idempotent cleanup |
| `frontend/src/lib/api/__tests__/work.test.ts` | existing Work DTO assertions; ticket caller cancellation and late response |
| `frontend/src/lib/api/__tests__/printers.test.ts` | live endpoint wire contracts; retired/caller-abandoned tickets |
| `frontend/src/components/__tests__/printer-detail.test.tsx` | controls/config/job/material behavior; realistic close callbacks; unmount/switch/account connection races |

The sole printer socket connection owner is PrinterDetailPage. Other event subscribers (task-center, thumbnail/preview/derivative hooks, background-work panel, gcode-viewer) were inventoried by search, not claimed as manually audited in this checkpoint. Their subscription cleanup API remains unchanged.

## Protected asset requirements

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 52 | limits simultaneous protected image downloads to four | Edge | six admitted distinct leases; pending responses | four fetches; fifth starts only after completion | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::limits simultaneous protected image downloads to four` |
| 53 | removes abandoned queued image work | Edge | four active downloads; fifth lease released | fifth never fetched | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::removes abandoned queued image work` |
| 54 | aborts an unneeded active download | Error | only consumer releases pending asset | fetch signal aborted; lease rejected | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::aborts an unneeded active download` |
| 55 | keeps shared work for the remaining image consumer | Happy | two consumers same path; one leaves | one fetch, retained consumer receives URL | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::keeps shared work for the remaining image consumer` |
| 56 | keeps a mounted image URL under count pressure | Edge | leased image plus 405 inactive assets | leased URL usable and not revoked | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::keeps a mounted image URL under count pressure` |
| 57 | bounds inactive image bytes | Edge | inactive blobs exceed 32MiB | oldest inactive URL revoked; newest retained | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::bounds inactive image bytes` |
| 58 | observes caller cancellation independently | Error | two shared consumers; caller aborts one | cancelled caller rejected; other completes | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::observes caller cancellation independently` |
| 59 | disposes private assets on scope retirement | Error | four active; queued leases; auth event | old signals aborted, URLs discarded, queued HTTP never starts | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::disposes private assets on scope retirement` |
| 60 | acquires a lease for an already cached image | Happy | remount cached path then cache pressure | immediate URL remains leased until unmount | Frontend unit | ✅ `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx::acquires a lease for an already cached image` |
| 61 | clears a resolved image after private scope retirement | Error | mounted hook with resolved private URL | old image removed immediately | Frontend unit | ✅ `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx::clears a resolved image after private scope retirement` |
| 62 | admits an image only near the viewport | Happy | offscreen element then intersecting observer frame | no fetch before admission; fetch afterward | Frontend unit | ✅ `frontend/src/lib/__tests__/use-viewport-admission.test.tsx::admits an image only near the viewport` |
| 63 | rejects late image state after a path switch | Error | A pending, switch to cached B | B URL remains visible after old A settles | Frontend unit | ✅ `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx::rejects late image state after a path switch` |
| 64 | reports an image ready only after decode | Happy | protected image loads then decode resolves | startup data marker ready after actual decode | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::reports an image ready only after decode` |
| 65 | admits visible thumbnails before distant cards | Happy | browser grid beyond viewport; delayed images | maximum four active; offscreen requests deferred; visible images decoded | Playwright | ✅ `frontend/tests/e2e/protected-assets.spec.ts::admits visible thumbnails before distant cards` |

Provisional asset settings: four simultaneous protected blob downloads; inactive cache at most 400 entries and 32MiB of encoded Blob bytes. Mounted lease bytes are excluded from inactive eviction; this bounds retained encoded data, not browser decoded-image memory. Decoded readiness is tracked separately at the image element. These initial budgets await isolated measurement.

| 66 | refetches a mounted image after explicit invalidation | Edge | mounted cached path replaced | old URL hidden; fresh bytes displayed | Frontend unit | ✅ `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx::refetches a mounted image after explicit invalidation` |
| 67 | starts current scope work before an old aborted response settles | Error | four retired requests ignore abort temporarily | current scope request starts immediately | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::starts current scope work before an old aborted response settles` |
| 68 | reports encoded byte ownership separately | Happy | resolved lease then release | live/inactive counters move exact bytes | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::reports encoded byte ownership separately` |

| 69 | admits images when intersection observation is unavailable | Edge | supported API absent in fallback environment | image fetched and displayed | Frontend unit | ✅ `frontend/src/lib/__tests__/use-viewport-admission.test.tsx::admits images when intersection observation is unavailable` |
| 70 | unobserves a removed thumbnail | Edge | queued viewport target unmounts | observer releases target; zero fetch | Frontend unit | ✅ `frontend/src/lib/__tests__/use-viewport-admission.test.tsx::unobserves a removed thumbnail` |
| 71 | keeps missing image semantics | Edge | no thumbnail path | existing placeholder; no empty src image | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::keeps missing image semantics` |
| 72 | keeps external covers outside authenticated transport | Happy | external HTTPS cover | native image URL; no protected blob fetch | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::keeps external covers outside authenticated transport` |
| 73 | leases Multipart covers through viewport admission | Happy | shared Cover in Multipart lists/cards | leased protected image with original alt | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::leases Multipart covers through viewport admission` |
| 74 | leases Search previews through viewport admission | Happy | Document preview in search list | protected preview image displayed | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::leases Search previews through viewport admission` |

| 75 | excludes referenced image bytes from inactive eviction | Edge | mounted 8MiB image; inactive bytes exceed cap | mounted URL survives; live8MiB reported outside inactive32MiB | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::excludes referenced image bytes from inactive eviction` |

M6 baseline evidence: lease/admission API tests **8 failed / 18 passed** (new lease API absent, count-only cache); hook owner tests **3 failed / 1 passed** (cached mount unleased, retired resolved URL retained, eager unadmitted read). Additional explicit-invalidation/current-scope admission regressions were red **2 failed / 1 passed** before fixes. The first byte-test arrangement used jsdom Blob with native Response, which serialized the wrapper rather than eight MiB; it was corrected to Uint8Array before final validation. No byte-bound conclusion is drawn from that initial malformed arrangement.

M6 local evidence: focused asset/thumbnail/card/Multipart/similarity/startup tests green; Chromium headline admission test **1 passed** with actual decoded visible images, four held requests, and distant thumbnail deferred until scroll. App/UI/domain typechecks green; lint zero diagnostics; format:check green. The browser run is a deterministic functional check against Vite, not a timing measurement.

The baseline source was independently materialized from committed `710e4eb7` under the ignored worktree reports directory, frozen dependencies independently installed (no cross-worktree node_modules symlink), and production Vite build passed. Comparable timing remains pending until the final integrated grid/backend is ready and qualification processes are idle. Machine observed: AMD Ryzen 5 1600, 12 logical CPUs; Node24.19.0, manifest-selected pnpm10.18.1. The existing startup corpus is 91 Models/27 Collections, distributed or 90+1 dense, real local SQLite/FS thumbnails (160x160 WebP), production nginx, Chromium1440x900 with active service worker, repeated cold-context and warm-context navigation. No optimization percentage, p95 or final budget tuning is claimed here.

M6 manual review additions:

| Exact path | Symbols / notes inspected |
|---|---|
| `frontend/src/lib/asset-cache.ts` | shared lease acquisition/release, queued/active cancellation, immediate retired-slot release, inactive count+encoded-byte eviction, explicit invalidation subscriptions, live/inactive stats and global disposal |
| `frontend/src/lib/use-authenticated-asset-url.ts` | cached mount lease, admitted acquisition, scope snapshot and path-specific invalidation, stale completion suppression |
| `frontend/src/lib/use-viewport-admission.ts` | one observer with200px margin, persistent admission, unobserve/disconnect cleanup, absent-API fallback and shared adapter |
| `frontend/src/components/protected-thumbnail.tsx` | alt/native external URL semantics, actual decode readiness, cached onload recovery and existing fade tokens |
| `frontend/src/components/model-card.tsx` | thumbnail extraction only; drag, Shift-selection, optimistic star, tag button and hover route prefetch retained |
| `frontend/src/components/multipart-model-presentation.tsx` | Count localization; Cover placeholder/layout/alt and admitted URL owner |
| `frontend/src/components/search-evidence.tsx` | SearchSubjectPreview icon/frame and image admission |
| `frontend/src/components/similarity-queue.tsx` | ModelLabel thumbnail admission; linked model semantics; remaining queue untouched |
| `frontend/src/components/multipart-model-browser.tsx` | four Cover call sites for cards, candidates, live member and unavailable-member presentations; no edits |
| `frontend/src/components/model-grid.tsx` | baseline MultipartModelListRow and ModelListRow thumbnail hook/frame; no edits; Document cards inspected separately: icon-only, no thumbnail owner |
| `frontend/src/components/model-detail/index.tsx` | thumbUrl detail hero; base lease hook retained for detail visibility |
| `frontend/src/components/model-detail/source-tab.tsx` | SourceCover content path, upload/delete explicit invalidation; base lease hook retained |
| `frontend/src/components/markdown-view.tsx` | AuthImage local versus external content URL; base lease hook retained for document/content images |
| `frontend/src/lib/__tests__/asset-cache.test.ts` | existing reuse/error/session/LRU assertions plus leases, scheduler, caller cancellation, encoded bytes and stats |
| `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx` | cached mount pressure, scope retirement, path race, admission, replacement bytes |
| `frontend/src/lib/__tests__/use-viewport-admission.test.tsx` | fake observer admission/unmount/fallback contracts |
| `frontend/src/components/__tests__/protected-thumbnail.test.tsx` | deferred decode, alt/missing/external, Cover and Search preview semantics |
| `frontend/src/components/__tests__/model-card.test.tsx` | revision/star/selection existing assertions; no edits |
| `frontend/src/components/__tests__/similarity-queue.test.tsx` | review/status/list existing assertions; no edits |
| `frontend/src/components/__tests__/multipart-model-browser.test.tsx` | shared Cover compatibility assertions; no edits |
| `frontend/src/lib/__tests__/use-startup-thumbnails.test.tsx` | visible completed-image reporting and cleanup; no edits |
| `frontend/tests/e2e/protected-assets.spec.ts` | 24-card admission, valid PNG decoding, held HTTP concurrency and scroll admission |
| `frontend/tests/e2e/_setup.ts` | authenticated mock server lifecycle, per-test resets and browser metadata |
| `frontend/tests/e2e/mock-api.ts` | ModelPage shape, native PNG fixture and route server; no edits |
| `frontend/src/test-support/factories.ts` | aModelListItem defaults for headline browser corpus; no edits |
| `frontend/playwright.config.ts` | Chromium, single worker, strict port, Vite/API proxy and test directory; no edits |
| `frontend/playwright.startup.config.ts` | production build, real backend/nginx startup and sample runner; no edits |
| `frontend/tests/performance/library-startup.spec.ts` | cold/warm context lifecycle, SW, actual completed-image milestone and resource/server timing observations; no edits |
| `frontend/tests/performance/scripts/start-backend.sh` | owned throwaway data root and exact corpus seed; no edits |
| `frontend/tests/performance/scripts/start-frontend.sh` | production nginx caching/delivery and cleanup; no edits |
| `backend/tests/factories/library_startup.py` | distribution/cardinality, derivative rows and completed160pxWebP fixture; no edits |

All base protected-asset hook consumers now acquire leases and share max4 admission. List thumbnail viewport adapters in root-owned integrated ModelGrid (Model and Multipart rows/cards) remain a coordinator cutover dependency; this checkpoint does not claim that final grid migration or final measurements are complete.

| 76 | keeps a failed decode out of the readiness milestone | Error | loaded image decoder fails | pending marker; no false decoded-ready claim | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::keeps a failed decode out of the readiness milestone` |
| 77 | ignores a previous URL decode after image reassignment | Error | decoder A pending; URL B displayed | A cannot mark B ready; B decode establishes readiness | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::ignores a previous URL decode after image reassignment` |

Final M6 combined qualification: **281 passed (14 files)** across assets, thumbnails, existing card/Multipart/similarity/startup, M7 sockets, and integrated AuthProvider/store retirement contracts; final focused decoder lane **7 passed**, including two additional error/reassignment cases. Scope/presentation prerequisite is coordinator commit2facc0c3 (content cherry-picked locally as16613e66).

## M3/M5 authority revalidation plan and assessment

One Query scheduler owns mount/focus/reconnect/resync/foreground30s authority reads. Opaque tokens are compared only for equality. Client-local request-start sequencing fences mount, displayed page replacement, and every browse-prefix success receipt, including structural sharing, hover prefetch and mutation publication. Authority successes never advance browse receipts. Conservative redundant probes from non-displayed browse successes are coalesced.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 78 | checks authority after mounting a cached page | Happy | cached page r1/a1 | one revision HTTP read | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::checks authority after mounting a cached page` |
| 79 | preserves a list when its browse revision changes | Happy | r2/a1 authority for r1/a1 page | retained output; explicit Refresh | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::preserves a list when its browse revision changes` |
| 80 | retires private scope when authorization changes | Happy | r1/a2 authority | hidden private output; cache clear; verified identity retained | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::retires private scope when authorization changes` |
| 81 | ignores cached authority from a previous mount | Edge | old cached mismatch; fresh probe pending | no notice or retirement | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::ignores cached authority from a previous mount` |
| 82 | rejects a probe started before a newer page | Edge | old probe resolves after page replacement | old response cannot retire newer page | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::rejects a probe started before a newer page` |
| 83 | rechecks an identical page receipt | Edge | structurally shared browse success during probe | old signal aborted; current probe accepted | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::rechecks an identical page receipt` |
| 84 | keeps authority successes outside browse receipts | Edge | successful probe | exactly one read; no refetch loop | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::keeps authority successes outside browse receipts` |
| 85 | coalesces resync while a probe is pending | Edge | repeated socket resync | one active revision read | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::coalesces resync while a probe is pending` |
| 86 | checks authority after focus returns | Happy | focused tab after settled initial probe | new revision read | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::checks authority after focus returns` |
| 87 | checks authority after reconnect | Happy | offline then online | new revision read | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::checks authority after reconnect` |
| 88 | polls only while foregrounded | Happy | 30s foreground; hidden tab | foreground revision read; no background read | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::polls only while foregrounded` |
| 89 | cancels a probe when its last view unmounts | Edge | pending probe; unmount | request signal aborted | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::cancels a probe when its last view unmounts` |
| 90 | rejects a late retired session response | Edge | logout during pending probe | no retirement callback or stale notice | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::rejects a late retired session response` |
| 91 | retains private output when revision lookup fails | Error | 503 response | error exposed; list retained | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::retains private output when revision lookup fails` |
| 92 | delegates explicit refresh to the browse owner | Happy | ordinary revision mismatch; Refresh click | owner refresh executes; no hidden automatic list read | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::delegates explicit refresh to the browse owner` |
| 93 | skips checks without an active presentation | Edge | null page or disabled hook | no revision HTTP request | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::skips checks without an active presentation ($label)` |
| 94 | rechecks the same cached page on remount | Edge | same page object remounted; old authority mismatch cached | fresh probe; no cached notice | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::rechecks the same cached page on remount` |
| 95 | checks authority after a settled resync | Happy | socket resync after initial probe | new revision request | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::checks authority after a settled resync` |
| 96 | preserves pending list reads on ordinary revision change | Happy | list request pending; newer browse token | list request signal stays active | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::preserves pending list reads on ordinary revision change` |
| 97 | retires pending private reads on authorization change | Edge | private request pending; changed authorization token | private signal abort; late value rejected | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::retires pending private reads on authorization change` |
| 98 | revokes mounted asset leases on authorization change | Edge | mounted ready protected image; changed authorization token | URL revoked; cache entry removed | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::revokes mounted asset leases on authorization change` |
| 99 | retains private output for malformed authority responses | Error | absent, null, non-string or empty required token | lookup error; no false retirement | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::retains private output for malformed authority responses ($label)` |
| 100 | shares authority requests between mounted views | Edge | two authority consumers mounted together | one network request; both settle | Frontend unit | ✅ `frontend/src/features/library/__tests__/authority.test.tsx::shares authority requests between mounted views` |


M3/M5 evidence: initial boundary stub produced13 observable behavior failures,2 arrangement errors (the session-version import), and2 passing inactive variants. After correcting the arrange import, the two affected auth/cached-authority cases were rerun against the stub and were red2failed/15skipped. The initial implemented17-case lane passed; the additional malformed-response cases were red4failed/1passed with the expected null-payload render error, then the response guard moved those failures into ordinary lookup errors. The final authority file covers27 cases. No production timing, coverage percentage, browser headline or CI result is inferred from these unit results.

The integrated authorization retirement test asserts private DOM is absent before the post-retirement callback, verified identity remains stored, private Query entries disappear, an outstanding private read is aborted and rejects late bytes, and a mounted Blob URL is revoked. Ordinary browse changes preserve their pending read. Lookup failures (503 and malformed tokens) retain authorized presentation and expose an error.

M6 suite-hygiene checkpoint f390c397 changes only test headers, describe grouping and three names. Existing assertions are retained; the conjunction cap remains123. The combined asset/hygiene/authority qualification was70passed6files before the final malformed-response cases were added. No browser behavior is attributed to this syntactic followup.

### M3/M5 manually inspected source, test and configuration ledger

| Path | Symbols / notes |
|---|---|
| `frontend/src/features/library/authority.ts` | BrowseReceipts exact models/browse success prefix; weak client provenance owner; mount/page/request sequencing; Query scheduler; synchronous render suppression; once-only post-retirement callback |
| `frontend/src/features/library/__tests__/authority.test.tsx` | real endpoint transport, real Query client/cache, deferred HTTP signals, shared Socket fake, structural sharing, focus/online manager and foreground interval, private read and Blob lease retirement |
| `frontend/src/features/library/browse.ts` | libraryBrowseKeys; libraryBrowseOptions infinite pages; explicit loadMore; no page focus/reconnect reordering; no edits |
| `frontend/src/lib/api/library-browse.ts` | getLibraryRevision GetJsonOptions signal; ordered list API; typed authority DTO; no edits |
| `frontend/src/types/library-browse.ts` | required opaque revisions; mixed page/card discriminants; no edits |
| `frontend/src/lib/session-transport.ts` | getSessionVersion, withSessionRequest caller/scope fences; no edits |
| `frontend/src/lib/auth-store.ts` | retirePrivateSessionScope preserves verified identity and emits same scope event; onAuthChange; no edits |
| `frontend/src/lib/auth-provider.tsx` | keyed private subtree; refresh captures current session incarnation when called; coordinator wires handled getMe refresh callback; no edits |
| `frontend/src/lib/query-client.ts` | onAuthChange clears private Query cache; no edits |
| `frontend/src/lib/events.ts` | subscribeEvents lifecycle/resync; signal-fenced ticket/socket; no edits |
| `frontend/src/lib/asset-cache.ts` | scope event disposal, acquireAssetUrl reference ownership and revocation; no edits |
| `frontend/src/test-support/render.tsx` | real singleton QueryClient; cached seed; deferred route responses; stored verified identity; no edits |
| `frontend/src/test-support/factories.ts` | aModelListItem defaults include integrated required edit_version; no edits |
| `frontend/src/features/library/__tests__/browse.test.tsx` | ordered append, pending continuation and refresh-required 409 regressions; no edits |
| `frontend/src/features/library/__tests__/mutations.test.tsx` | confirmed mutation publication cancels obsolete browse reads; these success receipts conservatively request authority checks; no edits |
| `frontend/tests/repo/suite-hygiene.test.ts` | contract header, describe ownership, mirror paths, conjunction cap123; no edits |
| `frontend/node_modules/.pnpm/@tanstack+query-core@5.101.0/node_modules/@tanstack/query-core/src/queryCache.ts` | updated success events include structurally shared data; subscribing only while view listeners remain |
| `frontend/node_modules/.pnpm/@tanstack+query-core@5.101.0/node_modules/@tanstack/query-core/src/query.ts` | initial pending refetch shares request even cancelRefetch:true with no data; explicit cancelQueries required for pre-page request |
| `frontend/node_modules/.pnpm/@tanstack+query-core@5.101.0/node_modules/@tanstack/query-core/src/queryObserver.ts` | observer scheduling owns polling/focus/reconnect; disabled/background guard; no separate hook interval |
| `frontend/src/components/document-browser.tsx` | full card/KindIcon rendering manually reviewed: icon-only cards, no image or protected-asset hook; no viewport cutover needed |
| `frontend/src/pages/document-detail.tsx` | image is detail/content output; not a list thumbnail admission owner |
| `frontend/package.json` | app/UI/domain typecheck scripts; oxlint/oxfmt; Safari16.4 supported floor; no new runtime APIs/frameworks |

Integration contract: supply the first displayed server page (null for placeholder/no-page), suppress private content whenever authorizationChanged is true, and provide onAuthorityRetired that invokes verified AuthProvider.refresh with a handled rejection. The callback executes after scope retirement, so refresh captures the new incarnation. The browse owner supplies an awaited explicit refresh callback. The authority Query stores a LibraryAuthorityObservation wrapper with client provenance, using the unchanged library-authority key. Route headline Playwright and final before/after startup measurements remain coordinator integration dependencies and are not claimed by this standalone hook checkpoint.

The HTTP decoder has one documented no-runtime-typeof exception: foreign revision JSON must establish both required nonempty opaque string tokens without coercion before any observation enters Query. Runtime guards never participate in already-decoded authority decisions. Manual review also included frontend/.oxlintrc.json and frontend/tools/oxlint/anti-slop/rules/no-runtime-typeof.ts to verify this is a boundary-specific exception, not a change to repository lint policy.

Final M3/M5 local qualification:137passed9files (authority, browse, confirmed mutations, auth store/provider, session transport, assets, events and suite hygiene); after extracting the dedicated boundary decoder,27authority cases passed again. Full app/UI/domain typecheck, full frontend lint (zero diagnostics), full format check and git diff --check passed. No integrated browser or CI qualification is claimed.

## M7 remote printer and fleet ownership plan (before tests)

Initial maintenance reads remain2*N because the server exposes per-printer windows and log endpoints. Keyed Query ownership removes repeated2*N reads on object/array identity changes; no aggregate endpoint or benchmark improvement is claimed. Printer detail socket snapshots remain generation-owned; Query owns HTTP state. Drafts remain local. Maintenance mutations reconcile only the confirmed resource after cancelling obsolete reads.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 101 | reuses maintenance reads for unchanged printer IDs | Happy | new printer array with unchanged IDs | exact initial two reads; no rerender reads | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::reuses maintenance reads for unchanged printer IDs` |
| 102 | fetches maintenance only for an added printer | Edge | fleet expands one→two | only new printer resources fetched | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::fetches maintenance only for an added printer` |
| 103 | aborts removed printer maintenance reads | Edge | pending resource; printer removed | signal abort; late response excluded | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::aborts removed printer maintenance reads` |
| 104 | shares maintenance reads between mounted panels | Edge | same printer in two views | two resource reads total | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::shares maintenance reads between mounted panels` |
| 105 | retains successful maintenance beside a failed resource | Error | log503; windows200 | window visible; lookup error/retry exposed | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::retains successful maintenance beside a failed resource` |
| 106 | preserves maintenance drafts during revalidation | Happy | open Log draft; refresh arrives | entered note retained | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::preserves maintenance drafts during revalidation` |
| 107 | refreshes only confirmed maintenance windows | Happy | deletewindow ack | affected windows re-read; log not re-read | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::refreshes only confirmed maintenance windows` |
| 108 | refreshes only confirmed maintenance logs | Happy | deletelog ack | affected log re-read; windows not re-read | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::refreshes only confirmed maintenance logs` |
| 109 | retains maintenance data after a denied mutation | Error | maintenance delete403 | visible row retained; no invalidation | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::retains maintenance data after a denied mutation` |
| 110 | rejects maintenance mutation effects after scope retirement | Edge | createpending; private scope retired | late result cannot dismiss current draft or publish | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::rejects maintenance mutation effects after scope retirement` |
| 111 | refreshes active maintenance after event resync | Happy | settled data; resync | coalesced maintenance resources re-read | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::refreshes active maintenance after event resync` |
| 112 | skips maintenance reads for an empty fleet | Edge | zero printer IDs | noHTTP reads | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::skips maintenance reads for an empty fleet` |
| 113 | passes cancellation to printer HTTP reads | Edge | caller signal abort | read request signals aborted | Frontend unit | ✅ `frontend/src/lib/api/__tests__/printers.test.ts::passes cancellation to printer HTTP reads ($label)` |
| 114 | passes cancellation to maintenance HTTP reads | Edge | caller signal abort | two resource signals aborted | Frontend unit | ✅ `frontend/src/lib/api/__tests__/fleet.test.ts::passes cancellation to maintenance HTTP reads ($label)` |
| 115 | leaves private caches untouched after maintenance transport writes | Happy | successful maintenance/routing writes | no HTTP-triggered Query invalidation | Frontend unit | ✅ `frontend/src/lib/api/__tests__/fleet.test.ts::leaves private caches untouched after maintenance transport writes ($label)` |
| 116 | retains genuine maintenance auth failures | Error | 401 from current scope | ApiError401 retained; identity expires | Frontend unit | ✅ `frontend/src/lib/api/__tests__/fleet.test.ts::retains genuine maintenance auth failures` |
| 117 | cancels obsolete maintenance before confirmed revalidation | Edge | pending log read; createack; freshread completes first | oldsignal aborted; late old row cannot replace fresh row | Frontend unit | ✅ `frontend/src/features/printers/__tests__/queries.test.tsx::cancels obsolete maintenance before confirmed revalidation` |
| 118 | limits routing reconciliation to routing read models | Happy | routingack with unrelated maintenance cached | printer/list updated; unrelated maintenance stays fresh | Frontend unit | ✅ `frontend/src/features/printers/__tests__/queries.test.tsx::limits routing reconciliation to routing read models` |
| 119 | retries only failed maintenance resources | Error | window200; log503then200 | successful window untouched; log Retry recovers | Frontend unit | ✅ `frontend/src/features/printers/__tests__/queries.test.tsx::retries only failed maintenance resources` |
| 120 | retains genuine auth failures through the maintenance owner | Error | current create request401 | owner preserves ApiError401; identity expires | Frontend unit | ✅ `frontend/src/features/printers/__tests__/queries.test.tsx::retains genuine auth failures through the maintenance owner` |
| 121 | scheduling a maintenance window calls createMaintenanceWindow with the entered fields | Happy | entered date/reason; createwindow ack | payload retained; modal closes; only windows re-read | Frontend unit | ✅ `frontend/src/components/__tests__/fleet-panels.test.tsx::scheduling a maintenance window calls createMaintenanceWindow with the entered fields` |


M7 maintenance evidence: after correcting a text arrangement mismatch in the new tests, the unchanged implementation was red7failed/3passed/16skipped for keyed ownership and cancellation. The seven typed read cancellation variants were separately red7failed/53skipped. Existing draft and session fences passed against the prior implementation and are recorded as retained behavior, not newly introduced protection. Final focused qualification was102passed5files (fleet panel, printer owner, fleet/printer clients and suite hygiene); two files were rerun after adding exact read-count assertions and passed32cases. Full app/UI/domain typecheck passed. Full lint and format passed before the last fixture/extra assertions, with final checks recorded below. No timing, coverage, browser or CI result is claimed.

### M7 maintenance manually inspected source, test and configuration ledger

| Path | Symbols / notes |
|---|---|
| `frontend/src/components/fleet-panels.tsx` | FleetMaintenancePanel full section: duplicated2*N identity-triggered effect removed; keyed resource projection; local form/mode/note retained; exact-resource mutation and scope-fenced UI acknowledgements. FleetQueuePanel read only for import compatibility. |
| `frontend/src/components/__tests__/fleet-panels.test.tsx` | Existing maintenance payload/draft tests and twelve new HTTP-count, partial failure, cancellation, resync, permission and scope cases; Queue tests unchanged except shared event factory setup. |
| `frontend/src/features/printers/queries.ts` | printerKeys, maintenance query options, stable ID useQueries, resync subscription, retry only errors, typed mutation variants; cancel exact obsolete reads before confirmed invalidation. |
| `frontend/src/features/printers/__tests__/queries.test.tsx` | deferred obsolete HTTP response, exact routing cache scope, partial retry, genuine401 through outer owner workflow. |
| `frontend/src/lib/api/printers.ts` | getPrinter, getDiagnostics, getMoonrakerConfig, listPrinterFiles, listPrinterJobs caller signal options; other writes inventoried for next detail slice. |
| `frontend/src/lib/api/__tests__/printers.test.ts` | five typed read cancellation variants plus existing provider/printer client contracts. |
| `frontend/src/lib/api/fleet.ts` | windows/log signal options; maintenance/routing POST/PATCH/DELETE requestApi transport without Query invalidation; fleet queue mutations unchanged. |
| `frontend/src/lib/api/__tests__/fleet.test.ts` | two read cancellation variants; five raw write cache isolation cases; current401 identity/error contract. |
| `frontend/src/test-support/factories.ts` | appended aMaintenanceWindow, aMaintenanceLog and aPrinterFile canonical fixtures; existing factories preserved. |
| `frontend/src/components/printer-detail.tsx` | loadPrinter/jobs/files/diagnostics/config effects and mutation paths inspected for upcoming migration; socket generation/auth cleanup retained, no edits in this checkpoint. |
| `frontend/src/lib/events.ts` | resync delivery and subscriber disposal reviewed; no edits. |
| `frontend/src/lib/api/request.ts` | raw requestApi writes avoid temporary requestMutation→Query bridge; scoped derived response fencing preserved; no edits. |
| `frontend/src/lib/session-transport.ts` | outer workflow assertion and genuine expired-scope401 marker behavior reviewed; no edits. |
| `frontend/src/test-support/render.tsx` | real Query provider/client and auth-store reset, deferred response helpers; no edits. |
| `frontend/tests/repo/suite-hygiene.test.ts` | mirror headers/describe scope and test conjunction cap verified; no edits. |
| `frontend/package.json` | focused Vitest/type/lint/format command owners; no edits. |

The initial per-printer API cost is still two HTTP requests per printer. This checkpoint reduces redundant work and prevents obsolete reads from replacing confirmed maintenance, without claiming a lower initial fleet cost or aggregate backend behavior.

Final M7 maintenance gates: full frontend lint reported zero diagnostics; full format check passed681files; git diff --check passed. The final typecheck included app, UI and domain packages.


## M7 printer detail ownership plan (before tests)

HTTP detail/jobs/files/diagnostics/config use stable printer/resource keys. Initial route data renders immediately but is revalidated. Socket state remains local and generation-owned. Reconnect/resync revalidate owned HTTP resources with pending reads coalesced. Confirmed file results cancel obsolete file reads before publication. Query updates preserve settings and temperature drafts.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 122 | revalidates an initially supplied printer | Happy | initial route printer; changed HTTP printer | fresh server name replaces initial name | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::revalidates an initially supplied printer` |
| 123 | aborts reads for a previous printer | Edge | pending files; printer ID switch | signal abort; late old file absent | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::aborts reads for a previous printer` |
| 124 | shares printer resources between mounted views | Edge | two same-printer pages | one read per HTTP resource | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::shares printer resources between mounted views` |
| 125 | preserves settings drafts during revalidation | Happy | edited name; Query detail refresh | typed draft retained | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::preserves settings drafts during revalidation` |
| 126 | refreshes printer resources after reconnect | Happy | completed initial resources; socket reconnect | detail/jobs/files re-read | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::refreshes printer resources after reconnect` |
| 127 | coalesces print state job revalidation | Edge | pending jobs; multiple print state frames | one pending jobs read | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::coalesces print state job revalidation` |
| 128 | publishes confirmed files over obsolete reads | Edge | old files read pending; syncack | oldsignal aborted; confirmed row retained after late oldresponse | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::publishes confirmed files over obsolete reads` |
| 129 | retains files after a denied sync | Error | files loaded; sync403 | row retained; no additional read invalidation | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::retains files after a denied sync` |
| 130 | rejects a file acknowledgement after a printer switch | Edge | pending sync; changed printer ID | old ack cannot replace new files or dismiss current UI | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::rejects a file acknowledgement after a printer switch` |
| 131 | surfaces printer lookup failure for retry | Error | noinitialprinter; detail503then200 | error/retry visible then current printer recovered | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::surfaces printer lookup failure for retry` |
| 132 | leaves caches untouched after printer file transport writes | Happy | start/sync/delete success | no HTTP-triggered Query invalidation | Frontend unit | ✅ `frontend/src/lib/api/__tests__/printers.test.ts::leaves caches untouched after printer file transport writes ($label)` |
| 133 | retires printer HTTP data on scope changes | Edge | authenticated data; private scope retired | old requests abort and new scope verifies HTTP detail | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::retires printer HTTP data on scope changes` |
| 134 | retains genuine auth failures through printer file mutations | Error | current start/sync/delete401 | owner retains ApiError401; identity expires | Frontend unit | ✅ `frontend/src/features/printers/__tests__/queries.test.tsx::retains genuine auth failures through printer file mutations ($kind)` |
| 135 | refreshes printer resources after shared event resync | Happy | settled resources; resync | current detail/jobs/files/config/diagnostics re-read | Frontend unit | ✅ `frontend/src/components/__tests__/printer-detail.test.tsx::refreshes printer resources after shared event resync` |


M7 printer detail evidence: the corrected initial ownership test lane was red6failed/2passed/48skipped against the prior HTTP effects. Draft preservation and denied mutation data retention already passed; those are retained behavior. Two initial button-name arrangements and one overwritten signal capture were corrected before assessing that baseline. Pure file transport isolation was separately red3failed/37skipped after correcting a missing fixture import. The first implemented detail lane passed56cases. Final combined qualification passed160cases6files; after adding shared resync and three genuine401 mutation variants,71cases3files passed. Existing socket disposal/login/logout/late ticket regressions remain in that run. No benchmark, coverage or browser/CI result is inferred.

### M7 printer detail manually inspected source, test and configuration ledger

| Path | Symbols / notes |
|---|---|
| `frontend/src/components/printer-detail.tsx` | HTTP loader effects, socket effect generation/controller/reconnect/auth retirement, file actions, header errors, config/diagnostics refresh handlers, Settings state/save lifecycle. Presentation snapshots and drafts remain local; component identity is printer ID plus private scope. Untouched metrics/table layout was not counted as a new UI audit. |
| `frontend/src/components/__tests__/printer-detail.test.tsx` | renderPrinter/fake Socket, existing settings/file/socket cases and new HTTP owner describe; canonical aPrinterFile replaces duplicate local fixture. |
| `frontend/src/features/printers/queries.ts` | five HTTP options, usePrinterResources, private route seed fence, shared event resync, exact-resource refresh/publish, usePrinterFileMutation caller/scope fences and obsolete file cancellation. |
| `frontend/src/features/printers/__tests__/queries.test.tsx` | FileProbe genuine401 for start/sync/delete, real Query/transport owner workflow; earlier maintenance cases preserved. |
| `frontend/src/lib/api/printers.ts` | startPrinterFile/syncPrinterFiles/deletePrinterFile raw requestApi and caller options; private ticket factory unchanged. updatePrinter/control compatibility writes remain inventoried for final bridge removal. |
| `frontend/src/lib/api/__tests__/printers.test.ts` | three raw file write cache isolation variants; earlier five read cancellation variants and private ticket retirement contracts preserved. |
| `frontend/src/lib/auth-store.ts` | scope notifications/keyed view retirement; no edits. |
| `frontend/src/lib/query-client.ts` | private cache clear lifecycle and defaults reviewed for enabled/refetch behavior; no edits. |
| `frontend/src/lib/events.ts` | shared resync frames/subscriber retirement; no edits. |
| `frontend/src/types/printers.ts` | PrinterRead, provider/admin capability boundaries, StartPrinterFile and response DTO contracts; no edits. |
| `frontend/src/test-support/render.tsx` | route request capture, rerender provider scope, deferred response propagation; no edits. |
| `frontend/tests/repo/suite-hygiene.test.ts` | final headers/names/mirror checks retained; no edits. |

Printer file actions have a single production owner, PrinterDetail. Their HTTP wrappers now report acknowledgements without cache invalidation. The feature owner cancels obsolete files before publishing acknowledged lists and refreshes the printer read; start refreshes only the printer and jobs. A denied write preserves cached presentation. Page switch or private scope retirement aborts the caller workflow and suppresses old UI delivery. Settings/control wrappers still use the temporary compatibility bridge because they have other owners, but their page acknowledgements are lifetime-fenced.

Final M7 printer detail gates: full app/UI/domain typecheck, full frontend lint (zero diagnostics), full format check681files and git diff --check passed after the final resync/auth variants.


## M9 public transport and viewer seam plan (before tests)

Public token reads have caller cancellation only, omit cookies and Authorization, preserve response/error DTO semantics, and never emit private auth events. The body consumer remains within the caller scope. Public STL uses an explicit previewFetcher; public G-code uses its existing toolpathFetcher plus privateEventsEnabled=false. Private defaults retain authenticated delivery and event subscriptions. SharePage's independent route QueryClient and token owner are assigned to the feature worker.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 136 | omits private credentials from public reads | Happy | signed-in identity; publicJSON/blob/text | credentials omit; Authorization absent | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::omits private credentials from public reads ($label)` |
| 137 | preserves private identity after public unauthorized responses | Error | publicJSON/blob/text401 | ApiError401 retained; private identity/scope unchanged | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::preserves private identity after public unauthorized responses ($label)` |
| 138 | completes public reads across private scope retirement | Edge | publicJSON/blob/text pending; scope retired | public signal active; current public bytes delivered | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::completes public reads across private scope retirement ($label)` |
| 139 | preserves caller cancellation through public body parsing | Edge | JSON/blob/text body pending; callerabort | first abort reason retained; late body rejected | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::preserves caller cancellation through public body parsing ($label)` |
| 140 | skips a public request with an already aborted caller | Edge | aborted caller | no fetch; same reason | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::skips a public request with an already aborted caller` |
| 141 | preserves public derivative preparation responses | Happy | binary/text202 | pending discriminant; state decoded | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::preserves public derivative preparation responses` |
| 142 | rejects malformed public derivative state | Error | text202 stateinvalid | error instead of invented state | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::rejects malformed public derivative state` |
| 143 | retries public artifact delivery through the proxy without credentials | Edge | TypeError or redirected failure | one proxy retry; caller signal/omit retained | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::retries public artifact delivery through the proxy without credentials (%s)` |
| 144 | preserves public bodyless acknowledgements | Edge | public204 | undefined result | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::preserves public bodyless acknowledgements` |
| 145 | preserves public retryable failures | Error | public503 | exactApiError status/code; private scope unchanged | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::preserves public retryable failures` |
| 146 | delegates STL preparation to an explicit fetcher | Happy | hook custombinaryfetcher | customfetcher called; globalfetch unused | Frontend unit | ✅ `frontend/src/lib/__tests__/use-stl-preview.test.ts::delegates STL preparation to an explicit fetcher` |
| 147 | passes an explicit preview fetcher into STL preparation | Happy | STLViewer customfetcher rejectsresource_limit | refusalrendered; privateHTTPunused | Frontend unit | ✅ `frontend/src/components/__tests__/stl-viewer.test.tsx::passes an explicit preview fetcher into STL preparation` |
| 148 | keeps a public Gcode viewer outside private events | Edge | signedin; explicitpublicfetcher; eventdisabled | renderedtoolpath; no privateSocketfactory | Frontend unit | ✅ `frontend/src/components/__tests__/gcode-viewer.test.tsx::keeps a public Gcode viewer outside private events` |
| 149 | removes an accidental Authorization header from public transport | Edge | explicitAuthorization/credentialsinclude | bearerremoved;credentialsomit;otherheaderretained | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::removes an accidental Authorization header from public transport` |
| 150 | hides STL bytes when the preparation fetcher changes | Edge | sameURL; replacementfetchpending | oldpreviewhidden;oldURLrevoked | Frontend unit | ✅ `frontend/src/lib/__tests__/use-stl-preview.test.ts::hides STL bytes when the preparation fetcher changes` |
| 151 | hides a toolpath when its fetcher changes | Edge | sameURL;replacementfetchpending | oldsliderhidden;loadingdisplayed | Frontend unit | ✅ `frontend/src/components/__tests__/gcode-viewer.test.tsx::hides a toolpath when its fetcher changes` |


M9 seam evidence: a typed public alias to the private transport demonstrated12failed/8passed/44skipped. The eight existing passes retain body cancellation, pending state decoding and error semantics; isolation and credential omission are new. The three explicit viewer seams were red3failed/28skipped. Two later same-URL fetcher identity tests were red2failed/29skipped before adding fetcher identity to completed viewer results. Final combined qualification passed106cases6files, including private session/response contracts. One old private policy event test lacked a signed-in arrange; it now explicitly calls storeLogin before testing a private event (the M7 events contract correctly rejects anonymous socket creation). STL internal Mesh props exclude the transport-only previewFetcher. No browser/CI or coverage result is inferred.

### M9 public seam manually inspected source, test and configuration ledger

| Path | Symbols / notes |
|---|---|
| `frontend/src/lib/api/request.ts` | complete transport source inspected: active URL derivation, artifact proxy retry, private scope wrapper, derived consumers, response/error parsing, compatibility invalidation adapter, multipart/XHR body lifetime. New ResponseContext/RequestContext distinguish response lifetime from private identity; caller-only scope installs no listeners; public headers delete Authorization and always omit credentials. |
| `frontend/src/lib/api/__tests__/request.test.ts` | public transport describe: JSON/binary/text credentials, scope changes and body parsing; preabort, public401/503/204, pending/invalid derivative state, redirected/network proxy retry and accidental header removal. Private cases retained. |
| `frontend/src/lib/use-stl-preview.ts` | full hook state/source/fetcher identity, polling, caller controller, public/no-model event behavior and Blob URL disposal. |
| `frontend/src/lib/__tests__/use-stl-preview.test.ts` | explicit preparation fetcher; same-URL adapter replacement hides old output/revokes lease; existing cancellation/failure/polling cases retained. |
| `frontend/src/components/stl-viewer.tsx` | STLViewerProps, internal Mesh Required/Omit boundary, STLViewer primary/overlay preparation and refusal/readiness paths; rendering internals unchanged. |
| `frontend/src/components/__tests__/stl-viewer.test.tsx` | custom public-style fetcher renders persisted failure without private HTTP or native Canvas; existing failure case retained. |
| `frontend/src/components/gcode-viewer.tsx` | GcodeViewerProps, LoadedToolpath identity, private event effect, fetch/parser lifecycle, preparation/retry paths and readiness projection. Public callers explicitly disable private events; private defaults preserved. |
| `frontend/src/components/__tests__/gcode-viewer.test.tsx` | public no-private-Socket factory, delivery identity replacement, existing toolpath rendering/error/pending/private-policy cases. |
| `frontend/src/lib/api/share.ts` | getSharedModel private-getJson bug inspected; private share management has separate owner; no edits, feature worker migrates only public lookup. |
| `frontend/src/pages/share.tsx` | token effect/read/error/viewer delivery inspected; feature worker owns independent token Query/cache/page migration; no edits. |
| `frontend/src/router.tsx` | public share route is outside AuthProvider/private composition; no edits. |
| `frontend/src/lib/session-transport.ts` | first caller abort reason and listener cleanup compatibility; no edits. |
| `frontend/src/lib/__tests__/session-transport.test.ts` | existing private races/caller abort/cleanup included in final lane; no edits. |
| `frontend/tests/repo/suite-hygiene.test.ts` | unchanged headers/mirror/naming checks included; no edits. |
| `frontend/package.json` | app/UI/domain typecheck, oxlint/oxfmt commands and supported browser floor checked; no new framework/runtime APIs. |

Integration API: request.ts exports getPublicJson<T>(path,{signal?}), getPublicDerivedBlob/Text(path,signal?), and requestPublicApi<T>(path,RequestInit,optional scoped consumer). STLViewer accepts previewFetcher; GcodeViewer keeps toolpathFetcher and adds privateEventsEnabled (defaulttrue). SharePage passes public helpers and privateEventsEnabled=false, with no modelId subscription for public STL. The feature worker owns an independent route QueryClient and complete token key so private global cache disposal cannot retire public reads. Setup/auth bootstrap uses cookies/CSRF and must retain private/session-auth transport.

Final M9 seam gates: full app/UI/domain typecheck, full frontend lint (zero diagnostics), full format check681files and git diff --check passed. The final tests reported one asynchronous act warning in the unchanged derivative-ready timer case; its baseline presence was not separately qualified. No runtime exception or unhandled rejection was reported.


## M7 task-center scope plan (before tests)

The browser projection retains upload progress and completion waiters, with one completion-chained polling scheduler. Identity/access retirement discards its private state without cancelling durable server Jobs. Bootstrap owns disposal outside React effect cleanup. Persisted recovery requires a matching verified owner stamp; legacy unowned local recovery metadata is discarded on upgrade, but authorized server Jobs remain rediscoverable.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 152 | retains recovery for the same verified owner | Happy | same owner reload | resumable task retained | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::retains recovery for the same verified owner` |
| 153 | discards %s persisted tasks | Edge | unowned or other-owner snapshot | no private rows restored | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::discards %s persisted tasks` |
| 154 | retires private history on %s change | Edge | A→B or same user access scope | tasks, dismissed IDs, emitted IDs and terminal cache cleared | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::retires private history on %s change` |
| 155 | rejects a retired completion waiter | Edge | pending waiter; scope retired | AbortError; server work not cancelled | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::rejects a retired completion waiter` |
| 156 | rejects late source publication after retirement | Edge | deferred jobs/runs; scope retired | no tasks or terminal callback delivered | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::rejects late source publication after retirement` |
| 157 | coalesces concurrent snapshots within a scope | Edge | concurrent sync/waiter requests | one source invocation/shared promise | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::coalesces concurrent snapshots within a scope` |
| 158 | keeps a newer flight after an old finalizer | Edge | retire old flight; start newer deferred flight | third sync joins newer flight | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::keeps a newer flight after an old finalizer` |
| 159 | fences reentrant terminal subscribers | Edge | first completion subscriber retires scope | next subscriber receives no old completion | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::fences reentrant terminal subscribers` |
| 160 | preserves recovery across StrictMode shell mount | Edge | persisted task; StrictMode mount/unmount | recovery task retained | Frontend unit | ✅ `frontend/src/components/__tests__/app-shell.test.tsx::preserves recovery across StrictMode shell mount` |
| 161 | freezes both source readers for each snapshot | Edge | replace sources during snapshot | original reader used for whole flight | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::freezes both source readers for each snapshot` |
| 162 | retirement survives unavailable browser storage | Error | storage removal throws | memory/waiters still retired | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::retirement survives unavailable browser storage` |
| 163 | fences cached terminal delivery across retirement | Edge | known completion promise; retire before await | AbortError, no old result | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::fences cached terminal delivery across retirement` |
| 164 | shares a snapshot between completion waiters | Edge | two waiters on pending snapshot | one source read; both terminals delivered | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::shares a snapshot between completion waiters` |
| 165 | rejects late Similarity Run publication | Edge | deferred run; retire | AbortError; no old progress rows | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::rejects late Similarity Run publication` |
| 166 | suppresses anonymous snapshot admission after logout | Edge | mounted subscriber; logout then online/visibility | no new read, no private task restored | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::suppresses anonymous snapshot admission after logout` |
| 167 | preserves a newer poll across old retirement finalization | Edge | old poll resolves while new poll pending | old callback cannot schedule/clear newer poll | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::preserves a newer poll across old retirement finalization` |


M7 task-center evidence: the untouched baseline had10failed/80passed because anonymous test arrangements could no longer open the verified private events channel. Adding verified identity to those arrangements retained all90 existing cases. New scope/shell regressions were red11failed/108passed; the source-reader freeze case was separately red1failed/101skipped. The cached-terminal handoff case was separately red1failed/2passed/103skipped; the two passes (shared waiters and late Run suppression) were first assessed after the initial implementation, so no separate red result is claimed for them. Anonymous snapshot admission was red1failed/1passed/106skipped. Final combined qualification passed160tests5files, including events/session contracts and suite hygiene. Two intermediate failures were arrangement corrections: Array.map's extra callback arguments required an explicit unary run adapter, and the logout rediscovery case needed a newly verified login after anonymous admission became closed. Typed Error outcome promises follow the repository's existing rejection-observation pattern and keep asynchronous assertions awaited.

Same-owner stamped recovery is retained. Legacy unowned local upload/review recovery metadata is deliberately discarded once on upgrade; this can require selecting a browser-local upload again. Durable server Jobs are not cancelled and are rediscovered through the authorized source. Completed IDs/dismissals/terminal cache and pending waiters retire on identity, logout or access-scope change. Storage retirement invalidates the owner stamp first and attempts each removal; if all browser writes are unavailable, in-memory state still clears. There is one completion-chained scheduler, one source flight per epoch, no new timer/cache framework, and no production cancel-Job endpoint call.

### M7 task-center manually inspected source, test and configuration ledger

| Path | Symbols / notes |
|---|---|
| `frontend/src/lib/task-center.ts` | Full source: local upload/review DTO, load/reload recovery and retention, task publication, grouped/single Job reconciliation, Similarity Run reader, terminal delivery/waiters, reset, polling/backoff/event subscribers. New owner stamp, explicit bootstrap session listener, captured reader/session/epoch and one flight; post-retirement callbacks cannot schedule a new anonymous read. |
| `frontend/src/lib/__tests__/task-center.test.ts` | Recovery/reset, linked duplicate fixture, completion waiters and adaptive scheduler sections manually reviewed, plus every added private-scope case. Dynamic module harness disposes bootstrap listener before reload; signed-in arrange matches private event contract. |
| `frontend/src/components/app-shell.tsx` | Full source: auth/RBAC chrome, title, completion/archive listeners and lazy dialog. Removed task retirement from React effect cleanup; private presentation remount remains AuthProvider's responsibility. |
| `frontend/src/components/__tests__/app-shell.test.tsx` | Auth/RBAC render helper, completion event tests and added StrictMode mount/unmount recovery test. No change to completion event DTO fixtures. |
| `frontend/src/main.tsx` | Full entry point: StrictMode, Query/I18n/Router composition, lazy development tools and PWA registration. Task-scope listener starts once outside React and is disposed on HMR replacement. |
| `frontend/src/lib/auth-store.ts` | Full source: verified owner metadata, silent same-ID refresh, explicit identity/scope/logout/storage notifications and failure expiry. No edits. |
| `frontend/src/lib/session-transport.ts` | Full source: captured version, cancellation controller/listener cleanup and genuine401 failure translation. Task synchronization/waiter handoffs use this contract; no edits. |
| `frontend/src/lib/__tests__/events.test.ts` | Existing private channel scope/ticket/retirement tests included in combined gate; no edits. |
| `frontend/src/lib/__tests__/session-transport.test.ts` | Existing source abort/listener cleanup and Error outcome arrangement manually reviewed and included in combined gate; no edits. |
| `frontend/src/lib/api/jobs.ts` | Full source: private status/list/work-job reads and compatibility writes. listJobs signal seam belongs to feature worker; no edits. |
| `frontend/src/lib/api/similarity.ts` | Run reader and authenticated transport contract checked; frozen unary callback preserves its optional second argument; no edits. |
| `frontend/src/test-support/render.tsx` | adminSession/memberSession metadata and renderApp silent same-ID behavior checked; no edits. |
| `frontend/src/components/top-bar.tsx` | activityEnabled sync subscriber ownership checked; no edits. |
| `frontend/tests/repo/suite-hygiene.test.ts` | Existing headers/names/mirror checks included in combined gate; no edits. |

Final M7 task-center gates: full app/UI/domain typecheck and full frontend lint passed. Full format check681files and git diff --check passed. No browser, coverage, CI or timing result is inferred.


## M7 Background work ownership plan (before tests)

Two independent authoritative reads retain partial output on failures. Query owns10s foreground polling and focus/reconnect; notices coalesce pending reads. Lane drafts and confirmations remain local, keyed by lane identity and private scope. Owned writes fence caller/session lifetime before exact cancellation/publication/invalidation. Regeneration retains the inventoried compatibility adapter because Settings still calls it.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 168 | shares work snapshots between mounted panels | Happy | two panels | one overview + Jobs read; same rendered data | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::shares work snapshots between mounted panels` |
| 169 | coalesces work notices during pending reads | Edge | job/resync/policy burst | one pending read pair retained | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::coalesces work notices during pending reads` |
| 170 | shows overview when Jobs reading fails | Error | Jobs503; overviewvalid | overview visible plus read error | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::shows overview when Jobs reading fails` |
| 171 | shows active Jobs when overview reading fails | Error | overview503; Jobsvalid | Jobs visible plus read error | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::shows active Jobs when overview reading fails` |
| 172 | aborts work reads when the last panel leaves | Edge | pending reads; unmount | signals aborted; late publication discarded | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::aborts work reads when the last panel leaves` |
| 173 | retires work presentation on account scope change | Edge | loaded and pending private reads; retire | old output removed; pending read rejected | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::retires work presentation on account scope change` |
| 174 | preserves lane drafts on background refresh | Edge | editedlane; updatedserverconcurrency | typed value retained | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::preserves lane drafts on background refresh` |
| 175 | clears acknowledged lane drafts after saving | Happy | own save ack | confirmed concurrency visible | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::clears acknowledged lane drafts after saving` |
| 176 | preserves a changed draft while saving | Edge | type again duringwrite | new text retained | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::preserves a changed draft while saving` |
| 177 | publishes acknowledged work changes after cancelling old reads | Edge | old overview read pending during lane acknowledgement | oldreadcancelled; confirmed update retained | Frontend unit | ✅ `frontend/src/features/work/__tests__/queries.test.tsx::publishes acknowledged work changes after cancelling old reads` |
| 178 | keeps work data after denied writes | Error | mutation403 | cached presentation retained; error visible | Frontend unit | ✅ `frontend/src/features/work/__tests__/queries.test.tsx::keeps work data after denied $label writes` |
| 179 | suppresses a late work acknowledgement after retirement | Edge | writepending; scope retired | no old success/Querypublication | Frontend unit | ✅ `frontend/src/features/work/__tests__/queries.test.tsx::suppresses a late work acknowledgement after retirement` |
| 180 | aborts work mutation when its view leaves | Edge | writepending; unmount | caller signal abort; no late toast | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::aborts work mutation when its view leaves` |
| 181 | preserves current unauthorized work failures | Error | current401 | ApiError401 retained; sessionexpires | Frontend unit | ✅ `frontend/src/features/work/__tests__/queries.test.tsx::preserves current unauthorized $label failures` |
| 182 | invalidates only affected work resources | Edge | ownedlane/queue/policywrites | exact work keys refreshed; unrelatedkeysretained | Frontend unit | ✅ `frontend/src/features/work/__tests__/queries.test.tsx::invalidates affected work resources for $label` |
| 183 | does not read administrator work anonymously | Edge | noverifieduser/member | no privateworkread; no cacheoutput | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::does not read administrator work for %s` |
| 184 | reads work through caller cancellation | Edge | overview/workJobs options | wire signal/body remains cancellable | Frontend unit | ✅ `frontend/src/lib/api/__tests__/work.test.ts::reads work through caller cancellation` |
| 185 | keeps raw owned work acknowledgements outside compatibility effects | Edge | lane/queue writes | DTO preserved; unrelatedQuerynotinvalidated | Frontend unit | ✅ `frontend/src/lib/api/__tests__/work.test.ts::keeps raw $label acknowledgements outside compatibility effects` |
| 186 | writes only requested derivative policy fields | Happy | partialmesh/gcode/toolpathpayload | existingconfigroute; exactpayload; no defaultreset | Frontend unit | ✅ `frontend/src/lib/api/__tests__/work.test.ts::writes only requested derivative policy fields` |
| 187 | retains regeneration compatibility for settings callers | Edge | regeneratewithsignal | caller abort fenced through the response body; existing mode payload retained | Frontend unit | ✅ `frontend/src/lib/api/__tests__/work.test.ts::retains regeneration compatibility for settings callers` |
| 188 | polls work only in a visible active view | Edge | 10s foregroundfallback; hidden/unmount | sharedreads refresh; no backgroundpoll | Frontend unit | ✅ `frontend/src/components/__tests__/background-work-panel.test.tsx::polls work only in a visible active view` |
| 189 | rejects a lane acknowledgement missing its lane | Error | malformed acknowledgement DTO | error; prior Query remains untouched | Frontend unit | ✅ `frontend/src/features/work/__tests__/queries.test.tsx::rejects a lane acknowledgement missing its lane` |
| 190 | rejects a Job acknowledgement for a different Job | Error | mismatched job_id | error; prior Query remains untouched | Frontend unit | ✅ `frontend/src/features/work/__tests__/queries.test.tsx::rejects a Job acknowledgement for a different Job` |
| 191 | aborts owned Job writes | Edge | cancel/retry pending; callerabort | wire aborted; late DTO rejected | Frontend unit | ✅ `frontend/src/lib/api/__tests__/jobs.test.ts::aborts the %s acknowledgement` |


M7 Background work evidence: six initial owner regressions were red (duplicate reads, pending-notice coalescing, both partial-failure presentations, draft overwrite and private-scope output retention). Lifetime tests added three red cases; denied writes already retained the prior presentation. Transport cases added three red caller-cancellation cases; raw unknown-route cache isolation and policy payload checks already passed. The first combined run had79cases with9failures from existing mock expectations for the newly forwarded signal and the deliberately removed failed-save refetch. Those expectations were corrected; no production failure was hidden. The focus fallback test initially failed because the shared render helper disables focus refresh; explicit feature-owned focus/reconnect options fixed this. A render-time ref assignment was rejected by lint and replaced with event-handler updates.

Qualification passed90tests across the component, feature, two endpoint mirrors and suite hygiene. The subsequent six cancellation/exact-invalidation variants passed in the two changed mirrors:30tests2files. These results cover96distinct cases across the same five files; no second aggregate96-case run is inferred. Full app/UI/domain typecheck and full frontend lint passed before those six test-only additions. The final test files received focused lint; full formatting checked683files and git diff --check passed. No browser, coverage, CI or performance result is claimed for this slice.

Administrative reads use two endpoints initially; no backend aggregation was added. Query owns foreground10s polling, focus/reconnect and notice reconciliation; pending reads coalesce. Overview and Job failures are independent. Lane drafts/confirmation state remain presentation-owned and retire with private scope. Acknowledged lane writes patch only their lane, Job writes validate their identity and patch the known Job list, and each mutation cancels older affected reads before publication and exact invalidation. Current401/403 contracts, caller abort and session retirement remain explicit. No server Job is cancelled merely because its view/session retires.

`regenerateDerivatives` keeps the existing compatibility adapter because Settings also calls it. Its existing mode-wire cases and new caller/body cancellation case are qualified; retention of the adapter is manual source evidence, not a separately proved broad invalidation effect. The narrow policy writer sends only the existing requested config fields. All production `cancelJob`/`retryJob` consumers were inventoried: the BackgroundWork owner is the only caller; Fleet uses a different `retryFleetJob`. The feature worker's separate `listJobs` signal change is not present in this worker checkpoint and must be preserved when integrating the disjoint endpoint hunk.

### M7 Background work manually inspected source, test and configuration ledger

| Path | Symbols / notes |
|---|---|
| `frontend/src/features/work/queries.ts` | Full new owner: workKeys, typed BackgroundWorkApi, two Query option factories, read admission/notice subscription, closed WorkChange/WorkOutcome, acknowledged cancellation/publication/invalidation and identity guards. |
| `frontend/src/features/work/__tests__/queries.test.tsx` | Full mirror: real wire401/403 for five mutation kinds; pending-read cancellation, five exact-key cases, late scope acknowledgement and malformed lane/Job acknowledgement. |
| `frontend/src/components/background-work-panel.tsx` | Full source: preserved activity/advanced controls, local lane draft lifecycle, keyed private view, StrictMode-safe caller controller, guarded success/error/toast, independent read errors and active Job presentation. |
| `frontend/src/components/__tests__/background-work-panel.test.tsx` | Full mirror: original controls/policy/confirmation/localization cases and new shared reads, partial errors, refresh drafts, cancellation/retirement and foreground timer cases. Existing write mocks now accept the caller signal. |
| `frontend/src/lib/api/work.ts` | Full source: cancellable overview/lane/queue/regeneration, retained derivative compatibility exports, existing events ticket and narrow raw partial policy writer. |
| `frontend/src/lib/api/__tests__/work.test.ts` | Full mirror: mode/null/exact payload, permission and events ticket cases retained; work caller cancellation and raw route/policy isolation cases. |
| `frontend/src/lib/api/jobs.ts` | Full source: work list/cancel/retry caller signals and raw acknowledgements. getJobStatus/listJobs/discardStaging untouched; listJobs belongs to feature worker. |
| `frontend/src/lib/api/__tests__/jobs.test.ts` | Full mirror: retained status/list/tracked IDs/action wire cases; work-list cancellation and cancel/retry body abort. No edits to listJobs assertions. |
| `frontend/src/lib/query-client.ts` | Shared Query defaults/key vocabulary and compatibility invalidation mapping checked; no edits. |
| `frontend/src/lib/session-transport.ts` | Captured session/caller lifetime and401 translation checked; no edits. |
| `frontend/src/test-support/render.tsx` | Session fixture and disabled focus default checked; no edits. |
| `frontend/package.json` | Gate commands and source workspace dependencies checked; no changes. |

### M10 read-only PWA and package boundary audit

The following is manual source/test inspection, not a new executed gate. It does not constitute full frontend/package coverage. Existing worker entry and CacheStorage tests assert delivery and fallback behavior; the current dev-server browser spec does not prove production bootstrap registration or private-route CacheStorage exclusion. No production change to these boundaries was made.

| Path | Symbols / notes |
|---|---|
| `frontend/src/lib/pwa.ts` | Full source: production/serviceWorker guards, one latched controller reload, load registration, waiting-worker activation and optional registration failure. |
| `frontend/public/sw.js` | Full source: named static shell cache, API/nonGET/cross-origin early return, network-first navigation/bootstrap and static fallback, immutable cache-write shortcut and activation cleanup. No private API response cache owner. |
| `frontend/src/lib/__tests__/pwa.test.ts` | Full source: existing registration options/disabled/reload/source-string cases inspected only. |
| `frontend/tests/repo/service-worker.test.ts` | Full source: VM worker harness, independent network delivery despite hanging CacheStorage, offline/named-cache/bootstrap/immutable/activation assertions inspected only. |
| `frontend/tests/e2e/pwa.spec.ts` | Full source: manifest and manual worker registration using dev server; production registration/private API-cache browser proof remains outside these cases. |
| `frontend/vite.config.ts` | Full source: API proxy, Three dependency prebundle, optional compiler profile and app/package coverage boundaries. |
| `frontend/packages/domain/package.json` | Full source: source exports and framework-free dependency boundary. |
| `frontend/packages/ui/package.json` | Full source: React peer, primitive dependencies and source exports. |
| `frontend/packages/domain/src/index.ts` | Full barrel: pure/domain exports and explicit browser preference helpers. |
| `frontend/packages/domain/src/last-collection.ts` | Full source: guarded storage and legacy navigation recovery. Collection-navigation preference currently crosses private identities; parent URL owner should assess intended preference policy separately. |
| `frontend/packages/domain/src/metadata-preferences.ts` | Full source: closed field validation and false-only flags; browser storage access is unguarded unlike last-collection. No new blocked-storage regression was run. |
| `frontend/packages/ui/src/index.ts` | Full barrel: UI primitives/browser helpers only. |
| `frontend/packages/ui/src/lib/use-media-query.ts` | Full source: guarded matchMedia external store and listener cleanup. |
| `frontend/packages/ui/src/lib/overlay.ts` | Full source: mount/exit/body-lock/focus lifecycle; no remote data owner. |
| `frontend/packages/domain/vitest.config.ts` | Full source: domain-owned environment/tests and branch coverage policy. |
| `frontend/packages/ui/vitest.config.ts` | Full source: UI-owned environment/tests and branch coverage policy. |
| `frontend/src/lib/metadata-preferences.ts` | Full compatibility wrapper: localized labels plus domain reexports. |
| `frontend/src/lib/last-collection.ts` | Full domain reexport wrapper. |
| `frontend/src/lib/use-media-query.ts` | Full UI reexport wrapper. |
| `frontend/src/lib/overlay.ts` | Full UI reexport wrapper. |
| `frontend/src/lib/archive-review-events.ts` | Full typed DOM notification publisher/subscriber and cleanup; no HTTP state owner. |
| `frontend/src/lib/use-thumbnail-arrivals.ts` | Full source: bounded coalescing, current callback and settled/notice/resync subscriptions; root owns Library integration. |
| `frontend/src/components/archive-review.tsx` | Full source reviewed: Job status effect and local selected/folder drafts need subject-identity/caller-cancellation review; no edits in this checkpoint. |
| `frontend/src/components/external-libraries-panel.tsx` | Only pollScanJob and completion sections (282–510) inspected: scan polling also feeds task tracking; owner coordination needed before replacing it. This is not full-file review. |
| `frontend/src/components/derivative-status.tsx` | Full existing derivative read/retry lifecycle inspected; root's dirty ModelDetail slice owns its replacement. |
| `frontend/src/components/model-detail/use-derivative-refresh.ts` | Full existing notice/read lifecycle inspected; root's dirty ModelDetail slice owns its replacement. |
