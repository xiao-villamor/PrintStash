# M0 baseline failure evidence

2026-10-07. Bounded reconciliation of the six failure families in
[the architecture review](review.md), using committed implementation base
`710e4eb7aafe05642693d6ff2696146e042dca53` and the current checkout at
`086d3a3df086a73d8ef09568409af72b3bdf79ec` plus pre-existing dirty M5 work.
Base mechanisms below were read with `git show`; current assertions and their
owning source were inspected. No tests, builds, browser scenarios or timing runs
were executed for the initial reconciliation below. The later exact-base execution
checkpoint at the end supersedes its runtime gaps within the stated scope. Earlier results are attributed records, not new
qualification. No full-file or whole-frontend manual review is claimed.

M0's subsequent qualification is recorded in [the measurement checkpoint](baseline-measurement.md#m0-acceptance).
M1–M11 remain subject to ordered closure, including M2's locally qualified work.
Existing later-goal implementations and their evidence are preserved; they do
not close unmet prerequisites.

## Requirement-derived assertion matrix

These rows express the six families' observable requirements before proposing
any additional baseline test work. `✅` means the named current assertion was
read, not that it was run here or existed at the base. Paths are repository-relative.
The narrower missing assertion is a gap in this inspected set, not a claim that
no related test exists anywhere. Exact-base execution gaps follow separately.

| #   | Behaviour (test name)                                            | Category | Precondition / input                                                            | Observable outcome asserted                                                                   | Tier          | Status                                                                                                             |
| --- | ---------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------ |
| 1   | preserves server order when another mixed page arrives           | Happy    | Multipart Zeta precedes later Model Älpha                                       | original card remains before appended card                                                    | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::preserves server order when another mixed page arrives` |
| 2   | reaches matching results after an empty browse page              | Edge     | no displayed rows; advertised next cursor                                       | Load more reaches Match                                                                       | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::reaches matching results after an empty browse page`    |
| 3   | removes a confirmed favorite from the displayed grid             | Happy    | unstar response held; stale list fixture                                        | card remains pending, disappears after acknowledgement                                        | Frontend unit | ✅ `frontend/src/components/__tests__/model-grid.test.tsx::removes a confirmed favorite from the displayed grid`   |
| 4   | rejects a late page after a confirmed star                       | Edge     | continuation pending across unstar acknowledgement                              | displayed Model stays unstarred when old page settles                                         | Frontend unit | ✅ `frontend/src/features/library/__tests__/mutations.test.tsx::rejects a late page after a confirmed star`        |
| 5   | preserves a confirmed favorite through a first-page refetch race | Edge     | old first-page refetch held across acknowledgement, with continuation attempted | confirmed membership/state survives both responses                                            | Frontend unit | ❌ missing                                                                                                         |
| 6   | keeps a favorite when unstar fails                               | Error    | server rejects command                                                          | favorite remains starred with failure message                                                 | Frontend unit | ✅ `frontend/src/features/library/__tests__/mutations.test.tsx::keeps a favorite when unstar fails`                |
| 7   | restores the nested reading position                             | Happy    | grid/list on desktop/mobile; detail then Back/Forward/Back                      | original nested offsets and entity viewport position restored                                 | Playwright    | ✅ `frontend/tests/e2e/library-navigation.spec.ts::restores the nested ${layout} reading position on ${viewport}`  |
| 8   | admits visible thumbnails before distant cards                   | Happy    | 24 cards; image responses held                                                  | four requests maximum; distant image waits for scroll; visible images have real decoded bytes | Playwright    | ✅ `frontend/tests/e2e/protected-assets.spec.ts::admits visible thumbnails before distant cards`                   |
| 9   | keeps a mounted image URL under count pressure                   | Edge     | mounted lease; 405 other assets                                                 | mounted URL remains cached and unrevoked                                                      | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::keeps a mounted image URL under count pressure`                |
| 10  | returns fresh JSON on every transport read                       | Happy    | sequential same-path reads; differing responses                                 | second server payload returned; two requests                                                  | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::returns fresh JSON on every transport read`                    |
| 11  | keeps concurrent transport reads independent                     | Edge     | two same-path reads resolve out of order                                        | each caller receives its own HTTP outcome                                                     | Frontend unit | ✅ `frontend/src/lib/api/__tests__/request.test.ts::keeps concurrent transport reads independent`                  |

Rows 1–2 prove client consumption, not server collation or filter correctness.
Rows 7–8 use real Chromium with mock HTTP; they do not prove backend permissions,
persistence or timing budgets. Row 11 intentionally removes transport-level
coalescing; shared remote reads should still coalesce through their Query owner.

## Six-family classification

### 1. Mixed append ordering — P1; owner M3

**Exists at base:** `frontend/src/components/model-grid.tsx::sortLibraryItems`
sorts all returned Multipart Models together with only the loaded Model prefix.
Name comparison uses `localeCompare`; `libraryItems` recomputes on append. This
can move an existing card when a later Model page arrives and disagrees with
server ordering. It is code-confirmed at the base, not a fresh browser observation.

**Historical evidence:** review F01 attributes an earlier browser reproduction to
the dirty navigation/startup checkout. It provides no independently replayable
exact-base output. No numeric ordering performance conclusion follows.

**Prior branch RED/GREEN:** [ordered client validation](../library-browse-client-validation.md)
records the two grid regressions failing against the old separate-list consumer,
then 156/156 component tests and 23/23 vault browser cases passing after cutover
(record introduced at `4cb3998c`). Initial missing-module failures for the new
browse hook are explicitly excluded as behavioral reproduction. The inspected
current component/browser cases assert Zeta then Älpha in DOM order; their
`/models/browse` fixture does not exercise the base's `/models/page` consumer.

**Current source / remaining hypothesis:** `features/library/browse.ts::libraryBrowseOptions`
and ModelBrowser preserve discriminated server pages. Client sorting is removed.
Global ordering, ties, permission scopes and revision consistency belong to the
M3 backend qualification; this bounded read does not certify those contracts.
No fix for this family was already present before the base; retain its existing
coherent folder/card snapshot and paged Model reads while qualifying it.

### 2. Empty visual page hides valid results — P1; owners M2 then M3

**Exists at base:** ModelBrowser's `memberModelIds`/`groupedModelIds` derive from
separate membership downloads capped at 500. `nextSnapshot` filters Models after
the server page cut. The all-empty branch renders `EmptyState` instead of the
grid/list containing `LoadMore`, even if snapshot `hasMore` is true. A valid later
result is inaccessible; incomplete membership can also misclassify Models.

**Historical evidence:** review F02 records the earlier inaccessible-result
scenario, without an exact-base run artifact. No relevant numeric measurement.

**Prior branch RED/GREEN:** the same `4cb3998c` record reports a real component
failure and passing component/browser cutover. The assertion read here exposes a
cursor on an empty response and reaches Match through Load more. The mode-removal
record at `36f8c1ca` separately reports 4 failed/4 passed retired-mode cases before
implementation; that is not itself the empty-page reproduction.

**Current source / remaining hypothesis:** current ModelBrowser consumes ordered
browse entries and exposes continuation for an empty page; retired membership
queries are removed. Missing baseline reproduction must use the original
Organized/Parts-only membership fixture, not only an artificial empty new-endpoint
page. Server-side filtering before pagination remains M3. No base fix closed this
family; keep independent Model identity and the existing accessible empty/error UI.

### 3. Confirmed mutations stale during reads/pages — P1; owner M4

**Exists at base:** `model-grid.tsx::loadMore` only checks `!loadingMore`, not every
in-flight list fetch; base `lib/queries.ts::modelListOptions` does not consume the
Query signal. `model-card.tsx::StarOverride` stores confirmed-looking state only
inside the mounted card. `toggleStar` changes the icon but does not reconcile
Favorites membership across cached pages. A stale list or remount can contradict
an acknowledged write.

**Historical evidence:** review F03 attributes stale confirmed favorites during
refetch/pagination to the earlier dirty checkout; no exact-base race trace is
attached in the inspected records. No numeric timing measurement.

**Prior branch RED/GREEN:** [mutation validation](../library-mutations-validation.md)
records the retained Favorite after acknowledgement failing before card migration,
then 226 tests and four focused browser cases passing (`11ded233`). The new owner
initially failed module resolution: that is not a race reproduction. The current
owner test in matrix row 4 holds a continuation through acknowledgement and asserts
the confirmed value, but the record does not identify that specific test as an
actual pre-fix behavioral RED. Later `81271aa9` records another real failure: a
retired acknowledgement aborted a new-session browse request, followed by 10
favorite-owner tests passing. That session race is distinct from the original
first-page refetch/Load-more interleaving.

**Current source / remaining hypothesis:** `features/library/mutations.ts::useLibraryStar`
cancels earlier reads, fences acknowledgement publication, updates both kinds and
removes confirmed Favorites; `useLibraryBrowse::loadMore` guards `isFetching`.
These mechanisms do not replace the missing first-page/refetch assertion in row 5.
Base GET generation guards already prevented invalidated responses repopulating
its transport cache; they did not establish confirmed Query membership. Preserve
pending-button feedback, failure retention, subject-kind identity and session fences.

### 4. Back scroll loss — P1; owner M5

**Exists at base:** `lib/navigation.ts::NavOptions` explicitly accepts but ignores
scroll options; `useRouter` has no restoration owner. ModelBrowser scrolls nested
containers, while Model links lack the full originating view and Model detail
reconstructs a collection return. Cached data alone cannot retain nested geometry.

**Historical evidence:** review F04 records lost Back scroll on the earlier
checkout; it is not exact-base browser qualification.

**Prior branch RED/GREEN:** [navigation validation](../library-navigation-validation.md)
records two origin-link failures then passes at `9f8d6a36`. The cached-position
increment at `a354f85d` records actual desktop grid/list failures (selected entity
3,626px/1,343px away), then both passing and four desktop/mobile cases passing.
The current inspected cases assert container offsets and viewport positions after
UI Back plus browser Forward/Back. Later reconstruction/refresh passes are scoped
in that record; they are not new runs in this document.

**Current source / remaining hypothesis:** `features/library/navigation-state.ts`
owns bounded entry/session metadata; `reading-position.ts::useLibraryReadingPosition`
restores nested geometry and only reconstructs visited pages. Current dirty M5
snapshot/navigation work is preserved, not qualified here. Full rapid destination,
private-DOM retirement and refreshed folder projections remain their owning M5
acceptance questions. Coherent cards/folder/breadcrumb snapshots were already at
the base: preserving them is not a new Back-scroll fix.

### 5. Thumbnail and secondary-read contention — P2; owner M6

**Exists at base:** `lib/use-authenticated-asset-url.ts::useAuthenticatedAssetUrl`
starts imperative blob reads for every mounted card. Native image `loading=lazy`
cannot admit that earlier fetch. `lib/asset-cache.ts::getCachedAssetUrl` has no
concurrency or consumer lease; `evictIfNeeded` revokes the oldest URL above 400
entries even when mounted. Byte ownership is unmeasured by that count.

**Historical evidence:** the [startup audit](../library-startup-audit.md) reports
Model/child-folder/tree-restore medians of 14/30/51ms alone versus 97/121/186ms in
seven concurrent reads, measured on the earlier installed system. Dense English
visible-thumbnail medians of 760.5ms warm/1146.5ms fresh concern its combined dirty
implementation; they are not this base. The review also cites 120 corrected
thumbnail samples, but this audit document does not include a matching 120-sample
series or its artifacts; that count remains an inherited claim pending provenance.

**Already fixed before base:** eager Home in `router.tsx`; primary-read release
through two animation frames in `library-startup-provider.tsx`; deferred dialogs;
page size 24; task-center first snapshot owned by resync with a one-second fallback;
SW v5 network-first bootstrap. Existing asset request-identity/auth invalidation
prevents late bytes from restoring the old cache. Therefore the original startup
fanout, duplicate initial Jobs fetch and route suspension must not be reasserted
as unchanged base defects. Remaining contention needs measurement at this base.

**Prior branch RED/GREEN:** [state validation](../frontend-state-validation.md),
M6 checkpoint (`b6a50aa2`), reports 8 failed/18 passed lease/admission tests when
the new lease API was absent; these are not eight observed production failures.
It separately records genuine hook RED (3 failed/1 passed: unleased cached mount,
retired resolved URL, eager unadmitted read), then focused GREEN and one decoded
image browser pass. A malformed Blob byte-test arrangement was corrected and
excluded. No exact-base functional browser RED or performance comparison is recorded.

**Current source / remaining hypothesis:** `asset-cache.ts::acquireAssetUrl`,
`pump`, `evictInactive` provide leases, four slots and inactive encoded-byte/count
bounds; `use-viewport-admission.ts::useViewportAssetUrl` admits near the viewport.
The read browser assertion checks four held requests and real image completion.
Whether four slots improve production thumbnail/API tails, and whether mounted
image memory is acceptable, remains unmeasured here. Keep cache-hit reuse, session
retirement, actual decoded-image readiness and lazy viewers.

### 6. Duplicate GET cache / remote-state ownership — P2; owner M1, removal M10

**Exists at base:** `lib/api/request.ts::getJson` owns `responseCache` and
`inflight` maps with a 30-second TTL; `lib/query-client.ts::queryClient` also owns
30-second freshness. A Query refetch may receive old transport-cached JSON.
This means duplicate freshness owners, not a claim that every GET is duplicated
on the network. Base `modelListOptions` also omits page size from its key and does
not forward the Query signal (review F06, adjacent to F07).

**Historical evidence:** review F07 was a code finding; no numeric user-facing
reproduction or request-count series was established. The startup Jobs duplicate
is a different mechanism and already addressed before this base.

**Prior branch RED/GREEN:** [state validation](../frontend-state-validation.md)
explicitly names base `710e4eb7`, records initial request RED 17 failed/26 passed,
then 553 tests/44 files and final 137 tests/7 files passing at the M1 checkpoint
(`3cdf85b6`, follow-up `08fa9c66`). The two assertions read here prove sequential
fresh payloads and independent concurrent outcomes. The aggregate record does not
name each initial failure or preserve raw output in this document; do not claim
17 cache defects. This is the strongest base-associated RED record among the six,
but a per-case exact-source reproduction receipt is still absent here.

**Current source / remaining hypothesis:** current `request.ts::getJson` delegates
to uncached session-scoped `requestApi`. `fresh` and `invalidateApiCache` remain
compatibility surface; the latter still couples endpoint writes to Query policy.
Audit remaining callers in M1/M10 before removing it. Base generation/session
checks on GETs were already valuable protections; retain them through broader
body/mutation cancellation. Query shared-read coalescing remains desirable.

## Historical numbers that are not M0 qualification

The startup audit has 400 combined-working-tree before/after navigations plus a
separate 200-navigation task-only series. The latter measured production source
`5b3fd1442ee7017aaf9e3c3c8026a6581b327c17`, not `710e4eb7`: its dense Spanish warm
p95 is 968.6ms, explicitly failing the 800ms budget. Do not turn the review's
600-sample aggregate into 600 accepted implementation-base samples.

The review's 803 tests/47 files (56.11s), 1.70s Vite-only build, and 10 warm/5 fresh
samples per viewport are earlier provenance-limited diagnostics. Desktop medians
433.75/731.10ms and mobile 1317.35/1811.20ms are not a fresh baseline. Mobile also
included opening the tree drawer. Early image enumeration is not decoded-image
completion. A successful Vite build, smaller chunk, functional browser pass or
inherited timing result cannot establish an optimization or close timing acceptance.

## Concrete M0 gaps and narrow next checks

1. Recover exact-source failure receipts for the original six scenarios. F01/F02
   have intermediate-branch consumer RED; F03 has acknowledgement-membership RED
   but no demonstrated original refetch/continuation interleaving; F04 has later
   branch browser RED; F05 has hook RED and browser GREEN, not exact-base contention
   reproduction; F06 has a base-labelled aggregate RED lacking per-case output.
   None of these distinctions requires repeating already qualified current suites.
2. Where receipts cannot be recovered, prepare isolated base-compatible fixtures
   on frozen `710e4eb7`, preserving the existing checkout. The current F01/F02/F04
   tests target the new browse endpoint and cannot be copied blindly: use the base
   Model-page/Multipart/membership interfaces, with equivalent visible outcomes.
   Missing modules or unmatched HTTP fixtures are setup failures, not reproductions.
3. For F03, complete matrix row 5 before a new test: hold the first-page read,
   acknowledge unstar, attempt continuation, release old results and assert final
   membership/state. The existing late-continuation row cannot silently stand in
   for the additional first-page race.
4. The coordinator owns fresh baseline gates and comparable production timing.
   Use the startup harness's production nginx/real-backend corpus, cache/SW policy,
   browser/viewport and provenance recording. Separate thumbnail decoding, request
   admission/API contention and usable-library timing; no timing run occurred here.

Prepared command shapes, **not executed**: after validating baseline-compatible
fixtures in the existing mirrors, use the smallest selection rather than full suites:

```sh
# From the isolated baseline's frontend, as Linux local:
pnpm exec vitest run src/components/__tests__/model-grid.test.tsx -t 'preserves server order when another mixed page arrives|reaches matching results after an empty browse page|removes a confirmed favorite from the displayed grid'
pnpm exec vitest run src/lib/api/__tests__/request.test.ts -t 'returns fresh JSON on every transport read|keeps concurrent transport reads independent'
pnpm exec playwright test tests/e2e/library-navigation.spec.ts --grep 'restores the nested.*reading position on desktop' --project=chromium --workers=1 --retries=0 --trace=on
pnpm exec playwright test tests/e2e/protected-assets.spec.ts --grep 'admits visible thumbnails before distant cards' --project=chromium --workers=1 --retries=0 --trace=on
```

These new cases/specs are not present at the base merely because current paths
exist. Fixture preparation is outstanding. For the cache rows, port just the two
existing `getJson` assertions into the base request mirror; do not import the
post-base session module. The protected-assets fixture already routes both old and
new list endpoints, making it a narrow candidate for an actual base admission RED;
verify its setup before crediting failure. Mounted-URL pressure needs a base hook
fixture, not a call to absent `acquireAssetUrl`. This document proposes no production
change and does not close M0, any later goal, CI or final delivery.

## Exact-base execution checkpoint

The coordinator subsequently ran isolated tests against unchanged production
source at `710e4eb7aafe05642693d6ff2696146e042dca53`. Only test fixtures/observers
were added to that detached checkout; its app, core and frontend workspace imports
were checked to resolve inside it. The baseline reproduction overlay and terminal
receipts are retained separately from the current implementation's passing tests.

| Family                  | Exact-base observation                                                                                                                                 | Receipt / qualification                                                                                                           |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| F01 mixed ordering      | Initial Alpha/Zeta becomes Alpha/Beta/Zeta after Load more; the displayed prefix moves                                                                 | `reproduction-order-corrected.log`: 1 intended assertion failure, 7.24s                                                           |
| F02 empty visual page   | Parts only receives a nonmember page with a continuation and membership containing the next page's Model; no Load more is available                    | `reproductions-unit.log`: named empty-page assertion fails                                                                        |
| F03 confirmed mutation  | Acknowledged unstar leaves the card in Favorites                                                                                                       | Same unit receipt: named confirmed-favorite assertion fails                                                                       |
| F03 refetch/page race   | First-page refetch held; unstar acknowledged; continuation starts; both old responses released; next page renders but confirmed-unstarred card remains | `reproduction-race-corrected.log`: final membership assertion fails, 5.87s                                                        |
| F04 nested scroll       | Actual browser Back returns to the same Library entry; anchor top moves from 228px to 3854px                                                           | `reproductions-browser.log`: 3,626px error, Chromium trace retained                                                               |
| F05 image admission     | All 24 mounted card image requests start while responses are held, including distant cards                                                             | Same browser receipt: 24 admitted against the candidate four-slot contract; this proves eager admission, not that four is optimal |
| F07 duplicate freshness | Sequential read returns old id 1 instead of new id 2; concurrent callers both receive id 1                                                             | `reproductions-unit.log`: two transport assertions fail                                                                           |

The first unit run was **5 failed / 1 passed / 174 name-deselected**, 6.81s.
One failure was a fixture error, not evidence: its `article h3` selector omitted
Model `h2` headings. After correcting the heading query, another arrangement error
was identified: this base reads sort from `ps-vault-sort`, not the test router's
`sort` parameter. The final ordering receipt explicitly seeds that existing
preference and reaches the post-pagination assertion. Both setup failures remain
retained and are excluded from the defect count. The passed existing pagination
control proves Load more is present for a nonempty page.

The first race arrangement incorrectly required exactly two first-page requests;
background readers made three. The corrected arrangement waits for the held
refetch to start, supplies separately consumable response clones and then asserts
both the acknowledged UI state and the continuation before releasing the old
responses. Only the final membership failure is credited. This reproduction does
not fill the current implementation's missing GREEN regression in matrix row 5;
that remains M4 work.

The browser run has **2 intended assertion failures**, zero retries. Vite reported
an existing dependency-scan warning for the viewer pilot's unresolved comparison/
thumbnail-camera aliases. Both cases nevertheless reached their actual Library
assertions; the warning is not the failure or a performance measurement.

Existing production startup behavior tests passed **8/8 distributed** (1.2m) and
**9/9 dense** (1.7m), including reading all 90 dense Models. These are functional
results, with a real SQLite backend and production nginx/build; they are separate
from mock-API failure reproductions and timing distributions. Concurrent functional
work means their durations are not performance evidence. See
[measurement qualification](baseline-measurement.md) for the serial timing run.

All six requested failure families now have bounded exact-base runtime evidence.
This checkpoint does not certify every route, permission combination or all
first-party assertions, nor qualify their current fixes. No production mechanism
was edited to obtain these baseline results.

Reproduction source SHA-256 (retained with the local run receipts):

- `unit-reproductions.patch`: `dbf98aa4afa410741cf075a483674f78852550d9066d523bbc0f1404cb1338c7`
- `m0-baseline-assets.spec.ts`: `f82d2d914abe418d2ccb35ce9da8706f564faea5451443ed9d7b144d3c412ae8`
- `m0-baseline-navigation.spec.ts`: `38f3bed24242d5b27d8d5c9ffee030dc520e9322a282d44f3e9aa410c4e96df7`
