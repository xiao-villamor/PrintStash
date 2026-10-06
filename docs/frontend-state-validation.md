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
| 59 | disposes active and queued assets on scope retirement | Error | four active; queued leases; auth event | old signals aborted, URLs discarded, queued HTTP never starts | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::disposes active and queued assets on scope retirement` |
| 60 | acquires a lease for an already cached image | Happy | remount cached path then cache pressure | immediate URL remains leased until unmount | Frontend unit | ✅ `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx::acquires a lease for an already cached image` |
| 61 | clears a resolved image after private scope retirement | Error | mounted hook with resolved private URL | old image removed immediately | Frontend unit | ✅ `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx::clears a resolved image after private scope retirement` |
| 62 | admits an image only near the viewport | Happy | offscreen element then intersecting observer frame | no fetch before admission; fetch afterward | Frontend unit | ✅ `frontend/src/lib/__tests__/use-viewport-admission.test.tsx::admits an image only near the viewport` |
| 63 | rejects late image state after a path switch | Error | A pending, switch to cached B | B URL remains visible after old A settles | Frontend unit | ✅ `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx::rejects late image state after a path switch` |
| 64 | reports an image ready only after decode | Happy | protected image loads then decode resolves | startup data marker ready after actual decode | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::reports an image ready only after decode` |
| 65 | admits visible thumbnails before distant cards | Happy | browser grid beyond viewport; delayed images | maximum four active; offscreen requests deferred; visible images decoded | Playwright | ✅ `frontend/tests/e2e/protected-assets.spec.ts::admits visible thumbnails before distant cards` |

Provisional asset settings: four simultaneous protected blob downloads; inactive cache at most 400 entries and 32MiB of encoded Blob bytes. Mounted lease bytes are excluded from inactive eviction; this bounds retained encoded data, not browser decoded-image memory. Decoded readiness is tracked separately at the image element. These initial budgets await isolated measurement.

| 66 | refetches a mounted image after explicit invalidation | Edge | mounted cached path replaced | old URL hidden; fresh bytes displayed | Frontend unit | ✅ `frontend/src/lib/__tests__/use-authenticated-asset-url.test.tsx::refetches a mounted image after explicit invalidation` |
| 67 | starts current scope work before an old aborted response settles | Error | four retired requests ignore abort temporarily | current scope request starts immediately | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::starts current scope work before an old aborted response settles` |
| 68 | reports leased and inactive encoded bytes separately | Happy | resolved lease then release | live/inactive counters move exact bytes | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::reports leased and inactive encoded bytes separately` |

| 69 | admits images when intersection observation is unavailable | Edge | supported API absent in fallback environment | image fetched and displayed | Frontend unit | ✅ `frontend/src/lib/__tests__/use-viewport-admission.test.tsx::admits images when intersection observation is unavailable` |
| 70 | unobserves a removed thumbnail | Edge | queued viewport target unmounts | observer releases target; zero fetch | Frontend unit | ✅ `frontend/src/lib/__tests__/use-viewport-admission.test.tsx::unobserves a removed thumbnail` |
| 71 | keeps missing image and alternative text semantics | Edge | no thumbnail path | existing placeholder; no empty src image | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::keeps missing image and alternative text semantics` |
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
| `frontend/src/components/model-grid.tsx` | baseline MultipartModelListRow and ModelListRow thumbnail hook/frame; no edits; integrated Document owner assigned to root |
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

All base protected-asset hook consumers now acquire leases and share max4 admission. List thumbnail viewport adapters in root-owned integrated ModelGrid (Model, Multipart, Document rows/cards) remain a coordinator cutover dependency; this checkpoint does not claim that final grid migration or final measurements are complete.

| 76 | keeps a failed decode out of the readiness milestone | Error | loaded image decoder fails | pending marker; no false decoded-ready claim | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::keeps a failed decode out of the readiness milestone` |
| 77 | ignores a previous URL decode after image reassignment | Error | decoder A pending; URL B displayed | A cannot mark B ready; B decode establishes readiness | Frontend unit | ✅ `frontend/src/components/__tests__/protected-thumbnail.test.tsx::ignores a previous URL decode after image reassignment` |

Final M6 combined qualification: **281 passed (14 files)** across assets, thumbnails, existing card/Multipart/similarity/startup, M7 sockets, and integrated AuthProvider/store retirement contracts; final focused decoder lane **7 passed**, including two additional error/reassignment cases. Scope/presentation prerequisite is coordinator commit2facc0c3 (content cherry-picked locally as16613e66).
