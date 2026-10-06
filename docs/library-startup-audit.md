# Library startup audit — 2026-10-06

The first sections preserve the original read-only audit and its historical
experiments against `localhostnew.har` and the local unified container. The
implementation and production benchmark results are recorded separately below.
No account credentials, session tokens, HAR bodies, or user folder names are
recorded here. The historical experiment is not the implementation benchmark.

## What the supplied trace establishes

There are **72 entries: 71 HTTP 200 responses and one WebSocket 101**, with no
rejected or aborted requests. The document reaches DOMContentLoaded at **553 ms**
and load at **556 ms**. Those events do not establish when the library is usable;
the HAR does not contain a paint/render trace.

The trace opens the root folder: its direct Model page is empty (41 bytes), the
multipart list is empty (2 bytes), and the child-folder response is 273 bytes.
The installed library has 91 Models spread across 27 collections; this capture
is not rendering 90 Model cards or their thumbnails. Its delay is startup work.

| Phase                        | Start → finish after navigation | Evidence                                                                                    |
| ---------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------- |
| Blocking theme bootstrap     | 29 → 446 ms                     | outer service-worker response; underlying network fetch finishes at 63 ms                   |
| Locale shell script          | 29 → 448 ms                     | Cache Storage response; underlying revalidation finishes at 59 ms                           |
| Setup and authenticated user | 591 → 712 ms                    | two parallel reads                                                                          |
| Main route module            | 758 ms                          | cached JavaScript; resource download takes approximately 0.1 ms                             |
| Library reads begin          | 1088–1105 ms                    | tags, tree restore, printers, saved views, facets, Models, multipart list and child folders |
| Grid data finishes           | 1330 ms                         | final required child-folder read                                                            |
| Tree restoration finishes    | 1379 ms                         | one restore POST, 288 ms elapsed                                                            |

The last data response is at 1.38 seconds. The user's approximately two-second
perceived wait is compatible with subsequent scheduling/rendering, but this HAR
alone cannot attribute or time that additional portion.

## Prioritized findings

### 1. Avoid the main route's startup suspension

`frontend/src/router.tsx:31` wraps the default Library route in `React.lazy`;
`RouteChunk` adds a Suspense fallback. Even when the route module is already
cached, library API calls begin approximately 300 ms after the chrome is mounted.
The installed React production renderer includes the 300 ms fallback throttle.

A controlled experiment changed only Home's import in a disposable copy of the
frontend. Both variants were production builds served by Vite preview, proxied
to the same live backend, with the service worker bypassed. Each had three
settled reload samples; authentication and setup guards were retained.

| Variant               | First folder card, samples | Median first card | Median Model request starts |
| --------------------- | -------------------------- | ----------------- | --------------------------- |
| Current lazy Home     | 735, 583, 583 ms           | 583 ms            | 413 ms                      |
| Eager Home experiment | 407, 390, 396 ms           | 396 ms            | 119 ms                      |

This eliminates **294 ms before the Model request starts** and improves the
median first-card observation by **187 ms (32%)**. The smaller total improvement
also reflects API work overlapping other startup reads. These local samples are
not a promise for the user's browser.

Recommended implementation: make the default Library route available before
mounting its content, either through an eager import limited to Home or a route
loading mechanism that resolves it before rendering. Keep viewers and secondary
routes deferred. The eager experiment increases the main entry from roughly
429 to 574 kB, so assess that tradeoff rather than making all routes eager.
Retain session verification, setup routing, chunk recovery and permission tests.

### 2. Keep critical bootstrap responses from waiting on Cache Storage

`frontend/public/sw.js:67` waits for `caches.match(request)` before returning
anything, including a network response already available. In the user's trace,
`theme-bootstrap.js` has `workerRespondWithSettled = 416.8 ms`; its underlying
network fetch completes far earlier. This tiny script blocks HTML parsing and
all application startup. `locale-shell.js` has the same delayed outer response.

Recommended implementation: handle these small, mutable bootstrap files with a
network-first path and offline cache fallback; use the named shell cache for
lookups rather than a global search. Preserve offline operation and first-PWA
activation behavior. The current cache-first policy also revalidates in the
background even when serving a cached response.

This is directly observed in the supplied trace, but **the 417 ms delay was not
reproduced** in a fresh local Chromium profile. In three interleaved reload pairs,
the current live first-card median was 618 ms with the worker and 595 ms with it
bypassed; bootstrap responses took about 9–15 ms with the worker. The user's
browser Cache Storage state requires separate confirmation. Do not present a
universal 400 ms saving or clear the user's caches as the permanent fix.

### 3. Reduce the simultaneous startup API work

Before Library reads, the chrome starts search status, inbox, Jobs and the event
ticket/WebSocket. `/jobs` is immediately fetched again on the socket's initial
resync, returning the same approximately 25 kB payload twice. Then eight Library
reads start together. `frontend/src/lib/task-center.ts:955` subscribes to resync
while line 958 also schedules immediate synchronization.

Three read-only rounds compared the same request parameters sequentially and as
a batch of seven concurrent Library requests. Median end-to-end timings:

| Read             | Requested alone | In the concurrent batch |
| ---------------- | --------------- | ----------------------- |
| Model page       | 14 ms           | 97 ms                   |
| Child folders    | 30 ms           | 121 ms                  |
| Tree restoration | 51 ms           | 186 ms                  |

Server-Timing corroborates the difference: the Model route's median app time
rises from 8.9 to 86 ms; restoration rises from 46.3 to 170.2 ms. This demonstrates
contention in request processing. It does not by itself distinguish thread
scheduling, Python CPU work, SQL compilation or other processing costs.

Recommended implementation: prioritize the minimum data needed for the folder
cards and bounded tree, defer noncritical chrome/filter reads until the first
Library render or interaction, and coordinate the first Jobs read with the
socket handshake so the initial resync does not duplicate it. Retain a fallback
when the socket is unavailable and preserve event-based refresh. Do not simply
serialize every request or combine unrelated API responses into one unbounded
bootstrap endpoint.

### 4. Reduce the entry's locale and dialog payload

The HAR exposes **1,330,498 bytes of decoded JavaScript across 41 script entries**.
The locale chunk alone is **549,057 bytes**; `frontend/src/locales/catalogs.ts`
statically imports both English and Spanish catalogs. Much of the JavaScript
is already cached in this capture, so these are decoded/execution payload sizes,
not bytes downloaded over the network.

Recommended implementation: retain English fallback while loading the selected
additional language when required; defer dialog-only modules until they are
opened. This is a secondary opportunity: the local comparison recorded no
main-thread tasks longer than 50 ms, and neither HAR nor these observations
establish how much the locale payload contributes to the user's two seconds.
Do not prioritize this over the measured route wait and bootstrap delay.

## Startup implementation — 2026-10-06

The original measured implementation built on the existing navigation/tree working tree. The task-only PR is applied independently to `main`: it keeps the existing paged tree API and adds coherent first-frame readiness without including the uncommitted backend restore changes or subsequent architecture refactor. Home is an
ordinary import; other routes and the upload, tag editing, multipart creation
and ZIP review forms retain deferred loading. Their first-use fallback is inside
our existing Modal primitive, and successful chunk loads clear the stale-chunk
recovery latch. Session validation, setup gates and route permissions still run.

An internal typed startup context waits for coherent cards and the bounded tree
(on mobile, cards only), then releases secondary reads after two animation
frames. Opening filters, saved views, search or activity enables that control
immediately. A visit that starts on another route keeps secondary reads released
when it subsequently enters the library. A primary error releases recovery controls without marking a
successful library. URL filters apply to the first Model request. Hovering or
using the navigation tree warms primary data without enabling facets. Startup
state is keyed by session owner. Generation/request guards discard old GET and
thumbnail responses, prevent invalidated reads from repopulating caches, revoke
previous object URLs and preserve a new owner's pending requests. An old 401
cannot expire the new session. Auth storage notifications include actual session
changes across tabs while ignoring locale/theme writes. Invalidated bootstrap
and Jobs responses cannot restore the previous owner's state. These races were
reproduced with failing tests before applying the guards.

The service worker now uses named shell cache **v5**. Both blocking bootstrap
scripts are precached and delivered network-first without waiting for Cache
Storage. Navigation and hashed build assets use the same delivery separation;
immutable cached assets are not rewritten on each reload. Offline fallback
reads only the named shell cache. Writes remain attached to `waitUntil`; failed
writes cannot hide a valid response. Activation removes only older PrintStash
shell caches. PWA is optional to the product, but retained for this change's
specified offline behavior; TanStack Query and browser HTTP caches remain
separate mechanisms.

The initial Model page is bounded to **24**, rather than 60, to reduce projection
and card mounting work. Subsequent pages use the existing cursor and Load more
control; the real-browser regression reaches all 90 Models. Jobs starts after
primary content or when activity is opened, shares the first socket resync
snapshot across subscribers, and falls back after one second without a socket
notice. Notices during an in-flight request cause another synchronization;
reconnection fetches the current state. There is no event suppression window.

There are no production backend, HTTP-contract, schema or migration changes in
this startup implementation. Backend/navigation changes already present in this
working tree belong to the preexisting navigation work. These reference measurements preceded the subsequent redeployment to :3000.

### Reproducible production procedure

`frontend/playwright.startup.config.ts` builds production Vite assets, serves them
on :3420, and launches the real FastAPI backend on :8420 with a disposable SQLite
root owned solely by `tests/performance/scripts/start-backend.sh`. It serves the build through the actual production nginx server template
(in an isolated unprivileged container, host networking), including gzip, API/WS
proxying, immutable hashed assets and nonstored HTML/service-worker responses.
Docker is therefore a benchmark dependency. The launcher labels and names its
container and removes it on graceful shutdown; the real :3000 image is untouched.
It never uses the live container's
library. The fixture has exactly 91 Models, 27 collections,
real local thumbnail files and completed derivative records, with no imports in
progress. The distributed scenario opens the root; the dense scenario opens
Collection 01 containing 90 Models, with the remaining Model in Collection 02.

Run as the workspace's Linux `local` user, with the configured Node/pnpm runtime:

```sh
cd /home/local/PrintStash/frontend
STARTUP_DISTRIBUTION=distributed pnpm test:startup
STARTUP_DISTRIBUTION=dense pnpm test:startup
# Enforce acceptance on the reference machine; generic Deep CI records trends.
STARTUP_DISTRIBUTION=dense STARTUP_ENFORCE_BUDGET=1 pnpm test:startup
```

Tracing/snapshots are disabled during timed samples to avoid measurement work
competing with the application; deterministic behavior specs retain failure
traces. Each scenario runs **30 warm reloads and 20 fresh authenticated profiles in each
of English and Spanish**, serially at 1440×900 with an active service worker.
Fresh profiles install the worker from `offline.html` before the first app visit:
app JavaScript is cold, while worker installation and login are outside the
measured navigation. This is the specified authenticated/SW-controlled first
visit, not the time to install the PWA. Warm profiles receive one unmeasured visit
before the 30 samples. Previous navigation requests are drained before the next
sample; disabling the worker is not part of acceptance.

The clock begins at navigationStart. An independent DOM observer waits for real
visible cards/folders and tree buttons, then two animation frames. A skeleton
never satisfies it. Internal `printstash:*` marks record validated session,
first Model query, coherent cards, navigable bounded tree, usable library and loaded
visible thumbnails. The harness checks the ready mark does not precede the
observed cards/tree, checks actual image bytes (`complete` and `naturalWidth`),
and exercises real collection navigation. Thumbnail completion remains separate
from the navigation budget; the harness's polling observation is an upper bound,
while the implementation thumbnail mark records image completion on a frame.
Absent derivatives remain distinguishable from complete thumbnails.

JSON output in `frontend/.startup-results/metrics/` contains individual samples,
resource timings, decoded sizes, marks, revision, dirty state and runtime
versions and the nginx image identity; browser failure evidence lives beside it. These local artifacts and
fixture data are gitignored. Deep CI uploads both scenarios' measurements. Set
`STARTUP_FRONTEND_DIR` to a preserved pre-change frontend with installed workspace
dependencies to compare the baseline using the same backend and observer.

### Acceptance and measured results

The initial Vite-preview runs were diagnostic only: their static cache headers
were different from the production nginx template, so they cannot establish the
warm-cache acceptance budget. Early traced runs also included instrumentation
work that is disabled in the final timing protocol. Their completed JSON remains locally under
`.startup-results/preview-diagnostic/` and `.startup-results/nginx-pilot/`.
Interrupted diagnostic runs are not included in acceptance statistics. The final comparison uses the production
server template for both preserved baseline and implementation, and tests its
asset/document/worker cache headers explicitly.

Measured on **2026-10-06** on the combined preexisting navigation/startup working tree (not the isolated PR below): all four cases meet the warm median ≤500 ms,
warm p95 ≤800 ms and first-visit median ≤1,000 ms budgets. There are **400
measured navigations**: 200 before and 200 after, with 30 warm and 20 fresh
samples per distribution/language/version. Values below are milliseconds;
median is the average of the two middle samples and p95 uses nearest rank.

| Scenario                          | Language | Before warm median / p95 | After warm median / p95 | Before fresh median / p95 | After fresh median / p95 | Budget |
| --------------------------------- | -------- | ------------------------ | ----------------------- | ------------------------- | ------------------------ | ------ |
| 91 Models / 27 collections (root) | en       | 729.2 / 891.1            | 352.8 / 475.1           | 947.3 / 982.7             | 656.8 / 752.4            | ✅     |
| 91 Models / 27 collections (root) | es       | 730.1 / 908.4            | 339.9 / 436.1           | 1008.6 / 1146.9           | 691.5 / 851.5            | ✅     |
| 90 Models in Collection 01        | en       | 1114.8 / 1421.8          | 491.0 / 579.3           | 1348.2 / 1787.8           | 835.8 / 1177.1           | ✅     |
| 90 Models in Collection 01        | es       | 1007.5 / 1439.5          | 479.5 / 555.1           | 1296.9 / 1715.9           | 853.6 / 1255.2           | ✅     |

The dense collection's warm median falls by 56% in English and 52% in Spanish;
the root falls by 52% and 53%, respectively. First-visit p95 for the dense
collection still exceeds one second; the agreed first-visit criterion is the
**median**, not p95. This is measured on the reference machine, not a guarantee
for every self-hosted server or library size.

The reference machine is WSL2 Linux 6.18.40.1-microsoft-standard-WSL2, x86_64,
AMD Ryzen 5 1600 Six-Core with 12 logical processors. Backend and frontend use
loopback on the same host; no imports run against the disposable fixture. The
machine is shared, so other local process activity is not claimed to be absent.
Both builds report package **0.14.0**, base revision
`82b2427c7d159e5b0584a4337a3edf962906d691`, with a dirty working tree. The baseline
was preserved at `/tmp/printstash-startup-baseline/frontend` before this change,
including the user's navigation/tree work; the implementation entry asset is
`/assets/index-Bv8mVqkh.js`. All four after-case artifacts identify that same
entry asset. Runtime: Node **v24.19.0**, Chromium **148.0.7778.96**,
production nginx image
`sha256:26b0bf6fbf07297983cb341998d79c831508787de26627dd2a112321b9c3a4af`.

To reproduce the before/after comparison, preserve the frontend source and its
workspace dependencies before editing, then use the same production harness:

```sh
STARTUP_FRONTEND_DIR=/tmp/printstash-startup-baseline/frontend STARTUP_DISTRIBUTION=dense pnpm test:startup
STARTUP_FRONTEND_DIR=/tmp/printstash-startup-baseline/frontend STARTUP_DISTRIBUTION=distributed pnpm test:startup
STARTUP_DISTRIBUTION=dense STARTUP_ENFORCE_BUDGET=1 pnpm test:startup
STARTUP_DISTRIBUTION=distributed STARTUP_ENFORCE_BUDGET=1 pnpm test:startup
```

The final dense and distributed implementation runs completed **11 and 10
production-browser tests**, respectively, with budgets enforced. Baseline runs
execute only the timing tests: behavior introduced by this change is intentionally
skipped there. Deterministic cases are configured for Deep CI together with
recorded timings from both distributions; this task did not trigger a remote
GitHub workflow or deploy an image.

The dense English phase medians illustrate what the budget includes:

| Milestone from navigationStart          | Warm median (ms) | Fresh median (ms) |
| --------------------------------------- | ---------------- | ----------------- |
| Session validated                       | 88.0             | 305.4             |
| First Model query                       | 132.0            | 403.7             |
| Tree ready                              | 359.1            | 668.0             |
| Coherent cards committed                | 443.1            | 767.5             |
| Visible cards/tree, acceptance observer | 491.0            | 835.8             |
| Secondary-data release mark             | 514.0            | 879.9             |
| Visible thumbnail bytes loaded          | 760.5            | 1146.5            |

Acceptance uses the independent observer of visible content and a real tree
interaction. The internal release mark is later (514 ms versus 491 ms in the
English warm case) because it waits for the provider's frame handoff; it is
recorded separately and is not substituted for the acceptance observer. These
are per-milestone medians, not additive durations. In Spanish, warm visible
thumbnail completion is 764 ms. The distributed root has no Model images, so
its thumbnail result is **not applicable**, even if an empty observation emits
an internal mark. A usable library therefore does not imply every preview has
already arrived.

The decoded JavaScript observed on the dense first load decreases from
1,330,496 to 1,287,388 bytes. The locale module remains **549,057 bytes** (42.6%
of that observed JavaScript). Its resource duration has median 0 ms for warm
loads and 82 ms / 108 ms for English / Spanish first visits. This measures
resource delivery, not JavaScript parse/execution CPU time; no causal CPU
attribution is claimed. Splitting language catalogs remains outside this change.

### Final verification

The affected Vitest selection comprises **22 files and 556 tests**, including
session validation/setup, coherent navigation/Back, bounded tree restoration,
thumbnail updates, PWA delivery, deferred dialogs and shared Jobs. The first
selection run, concurrent with browser regressions and checks, passed 554 tests
and hit the 5-second timeout in two existing tree-pagination cases:
`retains downloaded sibling pages when the selected folder changes` and
`reveals a cached level's selected folder without losing its loaded siblings`.
Both passed together in isolation. The complete tree file then passed **64/64**
without concurrent test load or changes to code/timeouts (the two cases took
2.96 s and 3.80 s). The initial failed JSON and the confirming runs are preserved
in `.startup-results/final-unit.json`, `tree-failures.json` and `final-tree.json`.
This is an observed timeout under load, not a claim that suite timing is fixed.

The existing mock-browser vault/motion/PWA selection passed **29/29** after the
final session-cache changes, in addition to the **21/21** production-browser
cases above. Full `pnpm lint`, `pnpm format:check` (659 files), and `pnpm
typecheck` (app, UI and domain packages) pass. The new SQLite fixture passes
Ruff lint/format, both launch scripts pass `bash -n`, and `git diff --check`
passes. No full backend/coverage gate is claimed for this frontend change; the
real production benchmark does run migrations and seed/read the disposable
SQLite backend. Existing unrelated working-tree changes were preserved.

### Behavior coverage matrix

All test implementations below are present. Temporal rows are assessed against
the complete reference distributions, separately from deterministic regressions.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | `startup with active service worker: warm budget` | Happy | 30 cached reloads per scenario/language | median ≤500 ms; p95 ≤800 ms | Playwright real | ✅ `frontend/tests/performance/library-startup.spec.ts::startup with active service worker: warm budget` |
| 2 | `startup with active service worker: fresh budget` | Happy | 20 new authenticated profiles per scenario/language | median ≤1 s | Playwright real | ✅ `frontend/tests/performance/library-startup.spec.ts::startup with active service worker: fresh budget` |
| 3 | `delivers %s without waiting for Cache Storage` | Edge | bootstrap or hashed asset; cache read never resolves | network body delivered | Frontend unit | ✅ `frontend/tests/repo/service-worker.test.ts::delivers %s without waiting for Cache Storage` |
| 4 | `keeps valid network bytes when a cache write fails` | Error | cache.put rejects | valid response received; write failure handled | Frontend unit | ✅ `frontend/tests/repo/service-worker.test.ts::keeps valid network bytes when a cache write fails` |
| 5 | `uses only the named shell cache for an offline bootstrap` | Error | network fails; shell cached | cached bootstrap returned | Frontend unit | ✅ `frontend/tests/repo/service-worker.test.ts::uses only the named shell cache for an offline bootstrap` |
| 6 | `does not rewrite an already cached hashed build asset` | Happy | existing immutable cached asset | network response delivered; no redundant cache write | Frontend unit | ✅ `frontend/tests/repo/service-worker.test.ts::does not rewrite an already cached hashed build asset` |
| 7 | `precaches both bootstrap scripts on first installation` | Happy | new worker installation | both scripts cached; installation completes | Frontend unit | ✅ `frontend/tests/repo/service-worker.test.ts::precaches both bootstrap scripts on first installation` |
| 8 | `updates the shell without deleting unrelated caches` | Edge | old PrintStash and unrelated cache | only old shell removed; clients claimed | Frontend unit | ✅ `frontend/tests/repo/service-worker.test.ts::updates the shell without deleting unrelated caches` |
| 9 | `releases secondary reads after both usable surfaces paint` | Happy | cards and desktop tree settle | controls enabled after paint | Frontend unit | ✅ `frontend/src/lib/__tests__/library-startup-provider.test.tsx::releases secondary reads after both usable surfaces paint` |
| 10 | `loads an explicitly opened control during startup` | Edge | primary pending; control opened | that control enabled immediately | Frontend unit | ✅ `frontend/src/lib/__tests__/library-startup-provider.test.tsx::loads an explicitly opened control during startup` |
| 11 | `releases recovery controls when primary content fails` | Error | primary error | controls enabled for recovery | Frontend unit | ✅ `frontend/src/lib/__tests__/library-startup-provider.test.tsx::releases recovery controls when primary content fails` |
| 12 | `does not wait for an absent mobile tree` | Edge | mobile; cards ready | secondary reads released | Frontend unit | ✅ `frontend/src/lib/__tests__/library-startup-provider.test.tsx::does not wait for an absent mobile tree` |
| 13 | `keeps secondary reads released when navigation starts on another route` | Edge | start on detail; then library | secondary reads remain enabled | Frontend unit | ✅ `frontend/src/lib/__tests__/library-startup-provider.test.tsx::keeps secondary reads released when navigation starts on another route` |
| 14 | `starts the next session with independent pending surfaces` | Edge | owner changes during startup | new owner controls pending | Frontend unit | ✅ `frontend/src/lib/__tests__/library-startup-provider.test.tsx::starts the next session with independent pending surfaces` |
| 15 | `bounds the initial model page without changing URL filters` | Happy | tag URL | limit=24 and tag in first request | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::bounds the initial model page without changing URL filters` |
| 16 | `defers catalogs until coherent primary content is visible` | Happy | Model request pending | no catalogs until visible primary content | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::defers catalogs until coherent primary content is visible` |
| 17 | `keeps secondary reads deferred when the navigation tree is used early` | Edge | tree interaction during primary load | navigation reads begin; no facets/tags | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::keeps secondary reads deferred when the navigation tree is used early` |
| 18 | `loads filter options immediately when filters are opened early` | Edge | Filters opened; primary pending | facets requested; saved views still deferred | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::loads filter options immediately when filters are opened early` |
| 19 | `waits for child folders before releasing secondary reads` | Edge | Models ready; children pending | no premature cards/catalogs; ready after children | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::waits for child folders before releasing secondary reads` |
| 20 | `releases recovery controls after child folders fail` | Error | children respond 503 | error visible; no empty success; filters usable | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::releases recovery controls after child folders fail` |
| 21 | `releases catalog requests after a primary error` | Error | Model request responds 500 | error visible; catalogs requested | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::releases catalog requests after a primary error` |
| 22 | `keeps one coherent folder result while the destination loads` | Edge | destination Models arrive before multipart data | old complete folder retained; destination replaces it atomically | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::keeps one coherent folder result while the destination loads` |
| 23 | `shares one handshake snapshot between subscribers` | Happy | two subscribers; initial resync | one initial Jobs response | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::shares one handshake snapshot between subscribers` |
| 24 | `falls back after one second without a socket notice` | Error | no socket notice | no request at 999ms; snapshot at 1000ms | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::falls back after one second without a socket notice` |
| 25 | `retains a Job notice received during a snapshot` | Edge | Job completes while read pending | follow-up snapshot reports completion | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::retains a Job notice received during a snapshot` |
| 26 | `refetches after the events socket reconnects` | Edge | Job changes while disconnected | reconnected snapshot reports completion | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::refetches after the events socket reconnects` |
| 27 | `ignores a previous installation job response received after reset` | Edge | reset with old Jobs read pending | old Jobs cannot restore state | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::ignores a previous installation job response received after reset` |
| 28 | `serves production asset caching headers` | Happy | nginx production build | hashed assets immutable; HTML and SW nonstored | Playwright real | ✅ `frontend/tests/performance/startup-behaviour.spec.ts::serves production asset caching headers` |
| 29 | `allows early filter interaction without prematurely loading other secondary reads` | Edge | hold primary; open Filters | requested control loads; remaining secondary reads wait | Playwright real | ✅ `frontend/tests/performance/startup-behaviour.spec.ts::allows early filter interaction without prematurely loading other secondary reads` |
| 30 | `retrieves both precached bootstrap scripts offline` | Error | installed SW; offline reload | navigation and bootstrap bytes succeed | Playwright real | ✅ `frontend/tests/performance/startup-behaviour.spec.ts::retrieves both precached bootstrap scripts offline` |
| 31 | `opens each deferred form on its first use` | Happy | Upload/tags/multipart/ZIP first opening | no initial form chunks; forms usable | Playwright real | ✅ `frontend/tests/performance/startup-behaviour.spec.ts::opens each deferred form on its first use` |
| 32 | `preserves URL filters in the first organized/all/multipart/components result` | Edge | collection+tag URL in each mode | first response contains matching content | Playwright real | ✅ `frontend/tests/performance/startup-behaviour.spec.ts::preserves URL filters in the first organized/all/multipart/components result` |
| 33 | `pages through all ninety models after the bounded first render` | Happy | dense 90-Model collection | 24 first-page cards; cursor reaches all 90 | Playwright real | ✅ `frontend/tests/performance/startup-behaviour.spec.ts::pages through all ninety models after the bounded first render` |
| 34 | `loads on opening, showing its pending state inside the dialog` | Edge | pending first form import | dialog fallback visible; usable form retains state | Frontend unit | ✅ `frontend/src/components/__tests__/deferred-dialog.test.tsx::loads on opening, showing its pending state inside the dialog` |
| 35 | `reloads once when a deferred chunk is unavailable` | Error | obsolete chunk or successful retry | one reload, visible repeated error or cleared latch respectively | Frontend unit | ✅ `frontend/src/lib/__tests__/lazy-component.test.tsx::reloads once when a deferred chunk is unavailable` |
| 36 | `surfaces a repeated failure without reloading forever` | Error | obsolete chunk or successful retry | one reload, visible repeated error or cleared latch respectively | Frontend unit | ✅ `frontend/src/lib/__tests__/lazy-component.test.tsx::surfaces a repeated failure without reloading forever` |
| 37 | `clears the recovery latch after a successful deferred import` | Error | obsolete chunk or successful retry | one reload, visible repeated error or cleared latch respectively | Frontend unit | ✅ `frontend/src/lib/__tests__/lazy-component.test.tsx::clears the recovery latch after a successful deferred import` |
| 38 | `waits for authenticated bytes before declaring visible thumbnails` | Edge | pending bytes, missing derivative or unloaded image | completion requires loaded visible image bytes | Frontend unit | ✅ `frontend/src/lib/__tests__/use-startup-thumbnails.test.tsx::waits for authenticated bytes before declaring visible thumbnails` |
| 39 | `keeps missing derivatives separate from a complete visual library` | Edge | pending bytes, missing derivative or unloaded image | completion requires loaded visible image bytes | Frontend unit | ✅ `frontend/src/lib/__tests__/use-startup-thumbnails.test.tsx::keeps missing derivatives separate from a complete visual library` |
| 40 | `does not let an unloaded image satisfy completion` | Edge | pending bytes, missing derivative or unloaded image | completion requires loaded visible image bytes | Frontend unit | ✅ `frontend/src/lib/__tests__/use-startup-thumbnails.test.tsx::does not let an unloaded image satisfy completion` |
| 41 | `ignores bootstrap identity from a previous session` | Edge | pending bootstrap; owner or storage event changes | identity remains tied to current validated owner | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-provider.test.tsx::ignores bootstrap identity from a previous session` |
| 42 | `keeps session validation pending through unrelated storage events` | Edge | pending bootstrap; owner or storage event changes | identity remains tied to current validated owner | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-provider.test.tsx::keeps session validation pending through unrelated storage events` |
| 43 | `accepts same-user auth changes after session validation` | Edge | pending bootstrap; owner or storage event changes | identity remains tied to current validated owner | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-provider.test.tsx::accepts same-user auth changes after session validation` |
| 44 | `ignores unrelated storage events while recognizing cross-tab sessions` | Edge | locale/theme vs auth storage change | only actual session changes invalidate reads | Frontend unit | ✅ `frontend/src/lib/__tests__/auth-store.test.ts::ignores unrelated storage events while recognizing cross-tab sessions` |
| 45 | `drops cached data when another tab changes the session` | Edge | GET/blob pending; cache/session invalidated | old reads cannot restore cache or expire new owner | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::drops cached data when another tab changes the session` |
| 46 | `does not repopulate caches from a previous session's pending read` | Edge | GET/blob pending; cache/session invalidated | old reads cannot restore cache or expire new owner | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::does not repopulate caches from a previous session's pending read` |
| 47 | `does not repopulate an invalidated cache from a pending read` | Edge | GET/blob pending; cache/session invalidated | old reads cannot restore cache or expire new owner | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::does not repopulate an invalidated cache from a pending read` |
| 48 | `ignores an old session's 401 on a %s read` | Edge | GET/blob pending; cache/session invalidated | old reads cannot restore cache or expire new owner | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::ignores an old session's 401 on a %s read` |
| 49 | `ignores an old session's unauthorized thumbnail response` | Edge | GET/blob pending; cache/session invalidated | old reads cannot restore cache or expire new owner | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::ignores an old session's unauthorized thumbnail response` |
| 50 | `revokes cached thumbnails when another tab changes the session` | Edge | protected bytes cached/pending at invalidation | old object URLs revoked; old bytes rejected; new reads preserved | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::revokes cached thumbnails when another tab changes the session` |
| 51 | `revokes cached thumbnails when the session owner changes` | Edge | protected bytes cached/pending at invalidation | old object URLs revoked; old bytes rejected; new reads preserved | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::revokes cached thumbnails when the session owner changes` |
| 52 | `rejects old thumbnail bytes without erasing the new session's pending request` | Edge | protected bytes cached/pending at invalidation | old object URLs revoked; old bytes rejected; new reads preserved | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::rejects old thumbnail bytes without erasing the new session's pending request` |
| 53 | `rejects thumbnail bytes invalidated during their download` | Edge | protected bytes cached/pending at invalidation | old object URLs revoked; old bytes rejected; new reads preserved | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::rejects thumbnail bytes invalidated during their download` |

### Local deployment for user testing

On 2026-10-06, after the reference measurements and at the user's request, the
current checkout was rebuilt with the unified Docker Bake target and redeployed
to `http://localhost:3000`. A clean frontend source copy excluded unreadable,
generated test data from the Docker build context. The existing
`printstash_printstash` data volume was retained, and the previous image remains
available as `printstash:before-startup-20261006`.

The deployed image is
`sha256:32039fceaf563af3919e68d477616fbbd8dfec6f7b2b59b791e429eee3f1aebc`,
serving `/assets/index-Nc3M2NBP.js`. Its health check passes, the API reports
`ok`, the entry asset returns HTTP 200 with immutable caching, and the served
v5 service worker matches the workspace source. This Docker-built artifact is
identified separately from the reference benchmark artifact above; the timing
series was not rerun against port 3000 or the user's live database.

### Task-only PR validation

The startup patch was reconstructed from the preserved pre-startup frontend and
its completed source snapshot, then applied to `main` at `b0e79c3e`. Unrelated
backend/tree restoration, navigation animation/cache work, skill imports and the
subsequent architecture audit/refactor are excluded. The PR uses the existing
paged tree API, retains a coherent card/folder/breadcrumb result while a new
folder loads, and waits for child-folder data before releasing secondary reads.
A failed folder query releases recovery controls without presenting a successful
empty result.

The original benchmark and deployment above remain historical evidence for
the combined working tree. They are not attributed to this reconstructed PR.
Its measurements and checks are recorded below separately. An initial benchmark
attempt overlapped frontend tests and typechecking: distributed EN warm median
294.1 ms, p95 979.7 ms, fresh median 596.8 ms. That attempt fails the warm p95
budget and is retained as diagnostic evidence, not accepted as a controlled run.

Local task-only checks: **535 tests in 22 Vitest files pass**, including auth, setup, router, tree, thumbnail updates, startup, caches and deferred dialogs. The initial folder-navigation regression was reproduced and corrected; the final ModelBrowser file passes all 142 tests. Frontend format, lint and typecheck pass; the new backend fixture passes Ruff check/format, both launchers pass `bash -n`, and the patch passes `git diff --check`.

The task-only mock-browser vault, motion and PWA regressions pass **25/25**. The isolated worktree reused installed modules through symlinks; Vite reported font allow-list warnings in that dev-server run. Production-nginx functional cases pass **8/8** for the distributed fixture; their assets are bundled rather than served from those module symlinks.

The final task-only production series ran **200 measured navigations** (30 warm
and 20 fresh visits per distribution/language) with timing enforcement enabled.
No local unit suite or typecheck overlapped this series. It measured production
source at `5b3fd1442ee7017aaf9e3c3c8026a6581b327c17`, version 0.14.0,
using the same reference machine/runtime described above, an active v5 service
worker and production nginx. Only test organization/names and documentation
change after that measured production source; its runtime remains identical.

| Distribution | Language | Warm median / p95 (ms) | Fresh median / p95 (ms) | Full budget |
|---|---|---|---|---|
| distributed | en | 265.5 / 645.5 | 528.2 / 700.9 | ✅ |
| distributed | es | 255.6 / 577.3 | 490.3 / 548.3 | ✅ |
| dense | en | 447.2 / 595.6 | 667.1 / 806.8 | ✅ |
| dense | es | 446.4 / 968.6 | 738.1 / 1394.1 | ❌ warm p95 |

Measured entry: `/assets/index-v5Ga0AXT.js`. JSON samples are retained locally under
`frontend/.startup-results/pr-reference/`; the controlled dense ES failure and
the earlier diagnostic failure are retained, not dropped from the record.
The acceptance assertion passes for distributed EN/ES and dense EN, but **fails
for dense ES warm p95**. All four warm medians and all four fresh medians meet
their budgets. This PR must not be described as meeting the complete timing
acceptance. Issue #419 remains open for the dense ES tail-latency gap.

The two slowest dense ES warm observations are 1,455.3 and 968.6 ms; their
`/models/page` resource intervals are 630 and 748 ms, respectively. Resource
intervals include transport/scheduling and do not establish backend CPU time or
prove a frontend-only cause. No threshold, sample count, percentile definition
or event suppression policy was relaxed to pass the series.

All **17/17 production-browser functional cases** pass across both corpora,
including the complete 90-Model pagination. CI's first attempt passed 2,864
frontend assertions but failed three repository test-organization rules: the
public service-worker test lacked a source-module mirror, a Playwright suite
lacked `describe`, and two new test names increased conjunction-name debt.
The worker contract now lives in `tests/repo/`, Playwright cases are grouped,
and those names describe one behavior. These corrections keep the production
implementation and existing gate thresholds unchanged.

The targeted CI correction rerun passes **31/31** tests (suite hygiene, shipped worker contracts and auth bootstrap). Format, lint, typecheck and Playwright test collection pass again.
