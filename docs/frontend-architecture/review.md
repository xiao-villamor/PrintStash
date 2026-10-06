# Frontend architecture evidence and review coverage

2026-10-06. This is the evidence supporting a plan, not certification that every
frontend file or behaviour has been manually audited. Production implementation
and comprehensive qualification remain pending.

## Provenance

Two different source states must not be conflated:

- The supplied `docs/library-startup-audit.md` and earlier exploratory review used
  `fix/library-navigation-continuity`, HEAD `82b2427c7d159e5b0584a4337a3edf962906d691`
  plus pre-existing local changes. That supplied report is an uncommitted working
  document, not part of the clean documentation base. Its measurements cannot be
  reproduced from HEAD alone.
- This documentation branch starts at main
  `b0e79c3e8e41a62a4ad1ad7d092b0bc61f7f4b1b`. The per-file inventory and the targeted
  checks labelled **B** below refer to this clean source state. The separate
  working checkout contains unrelated edits and an unfinished earlier prototype;
  neither is included in this documentation change or treated as implemented work.

No resets, bulk staging, source-code changes or production deployment belong to
this documentation increment. Reconcile the implementation base in I0; do not
reapply a fix just because it appeared in an earlier audit.

## Review coverage

The [ledger](review-ledger.tsv) inventories tracked frontend source, both workspace
packages, the extension, tests/support, frontend scripts/configuration, workflows
and selected deployment/backend integration boundaries. It contains **762 files**:
**30 targeted inspections**, **727 inventory-only entries**, and **5
generated/lock metadata entries**. Targeted inspection means reading relevant
symbols or searching a contract, not line-by-line review of the whole file.

The original working-tree inventory was provisional (758 paths) and is not this
ledger's denominator. Generated printer contracts and lockfiles are inspected
only for provenance/compatibility when needed. Dependencies, build/coverage/test
output and vendored tools are excluded from manual first-party source review.
`tools/oxlint/anti-slop` is a vendored lint dependency, not application logic.

Earlier flow-oriented inspection covered library navigation, favourite/detail
return, saved views, documents, identity, ingestion/tasks, Inbox, printers, search,
storage, profiles, public share and PWA. It does not supply a per-file comprehensive
sign-off. The clean-base ledger deliberately does not promote all those earlier
reads to current review status.

| Area                     | Inspected evidence                                                                                                                | Remaining work before its increment                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Library/list/navigation  | ModelBrowser sorting/membership/loadMore; card StarOverride; URL shim; Query identity; supplied browser repro descriptions.       | Reproduce on implementation base; read all list/outliner assertions; mixed-sort/filter/permission writer inventory.          |
| Transport/session        | GET cache, write invalidation, QueryClient lifecycle; auth contracts previously traced.                                           | Every direct JSON/form/blob/stream consumer; late-body/old-401 tests; current private-cache cleanup.                         |
| Async workflows          | Printer connection cleanup and events connect/disconnect; Inbox/nav refresh paths.                                                | Full task-center transfer lifecycle, reconnect tests, event delivery and all provider branches.                              |
| Documents/forms/settings | DocumentBrowser error-to-empty behaviour; Model/Multipart/Document update route signatures.                                       | Conditional version coverage; all settings/provider validators, drafts and recovery paths.                                   |
| Assets/platform/packages | Asset cache lifetime, route/bootstrap/Vite, locale catalogs, SW paths, nginx excerpt, package manifests, extension configuration. | Complete package/browser preference reads, viewer/worker contracts, extension capture parsers, deployment/PWA browser cases. |
| Tests                    | Suite/configuration inventory and repository test-design rules.                                                                   | Assertion-by-assertion matrix reconciliation; coverage percentages alone cannot fill it.                                     |

There is no new whole-frontend cycle proof, physical-device audit, accessibility
certification, multi-replica qualification or complete review of every test body.
I0 and each owning increment must close these named gaps before claiming coverage.

## Historical measurements versus current verification

The supplied report's **600 startup samples** and **120 corrected thumbnail samples**
are historical evidence for its stated corpus, build, proxy and browser. Its four
correctness scenarios describe unstable mixed ordering, inaccessible results after
a visually empty page, stale confirmed favourites during refetch/pagination and
lost Back scroll. None was freshly rerun as part of this documentation increment.

An earlier diagnostic run in this planning session recorded **803 passing tests
in 47 files (56.11 seconds)** and a Vite-only production build (**1.70 seconds**).
That does not establish a full build/type gate on the current base. Its locale
chunk was 549.05 kB / 156.08 kB gzip. Separate small timing samples were 10 warm
and 5 fresh per viewport: desktop readiness medians 433.75 / 731.10 ms; mobile
1317.35 / 1811.20 ms, including opening the tree drawer. These are provenance-
limited diagnostics, not before/after evidence for a refactor. The early script's
image-enumeration timestamp did not prove decoded thumbnails and is excluded from
thumbnail conclusions.

Already-present work in the original dirty baseline included eager Home, deferred
forms, outliner restoration fences/seeding, coherent collection snapshots, auth
bootstrap generation guards, GET/asset session guards and service-worker fallback
when Cache Storage fails. These must be preserved if they are on the implementation
base. They must not be attributed to this plan; some are absent on clean main.

## Findings

P1: correctness/access/session or lifecycle risk. P2: ownership/efficiency issue.
P3: maintenance opportunity. **B** is targeted clean-base code evidence; **H** is
a historical report reproduction; **R** is earlier working-tree inspection.
Code evidence explains a mechanism, not a newly observed browser failure.

| ID  | Severity / evidence           | Paths and symbols                                                                                                         | Problem / consequence                                                                                                                                                  | Concrete recommendation                                                                                                                          |
| --- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| F01 | P1, B + H                     | `frontend/src/components/model-grid.tsx`: `sortLibraryItems`, `libraryItems`                                              | All returned Multipart Models are mixed with only loaded Model pages; append can change the prefix. Browser and SQL comparisons can disagree.                          | Server orders the combined eligible set before pagination; stable kind/id ties; preserve returned order.                                         |
| F02 | P1, B + H                     | same file: `memberModelIds`, `groupedModelIds`, membership queries with limit 500, empty-state branch                     | Client membership is incomplete and applied after the page cut; a visually empty page can make valid later results unreachable.                                        | Retire Organized/Parts only and their membership downloads; paginate all remaining filters on the server; never hide an advertised continuation. |
| F03 | P1, B + H                     | same file: `loadMore`; `components/model-card.tsx`: `StarOverride`, `toggleStar`                                          | Guard checks loading-more, not every in-flight list fetch; confirmed state is local to a mounted card.                                                                 | Query-owned confirmed mutation reconciliation and coordinated continuation.                                                                      |
| F04 | P1, B + H                     | `lib/navigation.ts`: `NavOptions`, `useRouter`; ModelBrowser scroll container                                             | Scroll/prefetch options are no-ops; remounting the nested scroller loses the reader's position.                                                                        | History-entry + view identity restoration after matching data/layout, with anchor and bounded fallback.                                          |
| F05 | P2, B + H                     | `lib/asset-cache.ts`: `getCachedAssetUrl`, `evictIfNeeded`; card thumbnail consumers                                      | Entry-count LRU can revoke a mounted consumer's URL; imperative blob fetch admission is not controlled by img lazy loading. Historical traces show network contention. | Consumer leases, viewport admission, cancellation and byte-aware idle budget; measure rather than assume the best concurrency.                   |
| F06 | P2, B                         | `lib/queries.ts`: `modelListOptions`                                                                                      | Page size absent from key; queryFn does not consume the signal. Distinct results can share a key and cancellation cannot reach fetch.                                  | Complete normalized keys and end-to-end AbortSignal plumbing. Preserve outliner options that already forward signals.                            |
| F07 | P2, B                         | `lib/api/request.ts`: `responseCache`, `inflight`, `invalidateApiCache`; `lib/query-client.ts`                            | Two 30-second freshness owners plus transport-to-query policy coupling; refetch may reuse a transport-cached response.                                                 | Query owns reusable remote JSON; transport owns HTTP only; remove cache after consumer migration.                                                |
| F08 | P1, B/R; runtime gaps         | request transport and auth-change cleanup                                                                                 | Clearing maps does not prevent pending bodies/writes/401 handlers from acting on the next session. Some GET protections already exist in the working baseline.         | Fence all response publication and side effects by session generation; test headers, body and late callbacks separately.                         |
| F09 | P2, B                         | `components/document-browser.tsx`: `LoadedDocuments`, load effect                                                         | A failed list read settles as an empty collection; remote data has a separate lifecycle from other readers.                                                            | Feature Query owner; distinct initial/background errors and empty success; keep drafts local.                                                    |
| F10 | P2, B/R                       | `components/bottom-nav-bar.tsx`: refresh interval; `pages/inbox.tsx`; `lib/task-center.ts`                                | Independent consumers coordinate remote counts/process state separately; naive unification could lose local transfer progress.                                         | One Inbox read owner; separate remote Job snapshots from local transfer lifecycle.                                                               |
| F11 | P1, B; reproduction pending   | `components/printer-detail.tsx`: `connect`, effect cleanup                                                                | close handler schedules reconnection after cleanup; pending ticket resolution can also outlive the page.                                                               | Disposal/generation-scoped connection with late socket closure.                                                                                  |
| F12 | P2, B; reproduction pending   | `lib/events.ts`: `connect`, `disconnect`                                                                                  | Listener count alone cannot distinguish an old pending connection from a later subscription/session.                                                                   | Generation-scoped connection and delivery plus authorized reconnect resync.                                                                      |
| F13 | P2, B/H                       | ModelBrowser `toggleSelect` dependency on `sortedModels`                                                                  | Appending pages changes a callback supplied to old cards; historical profile observed broad rerendering.                                                               | Stable selection contract; verify range selection/DnD and compare actual render work.                                                            |
| F14 | P2, B/R                       | ModelBrowser libraryView/sort state and popstate listener; `filter-sidebar.tsx`                                           | URL parsing and persisted mirrors can diverge; saved views/return links have competing precedence.                                                                     | One typed URL codec; migrate retired values with replace and preserve unaffected parameters.                                                     |
| F15 | P1, B; product contract gap   | `backend/app/api/v1/models.py:update_model`, `multipart_models.py:update_multipart_model`, `documents.py:update_document` | Inspected routes lack a caller edit-version precondition; last writer can overwrite another editor.                                                                    | Atomic conditional edits with explicit compatibility rollout; preserve conflicted drafts.                                                        |
| F16 | P2, B/R                       | `frontend/src/locales/catalogs.ts`; route lazy loading and app recovery                                                   | Both catalogs import eagerly; chunk-failure recovery needs a complete contract. Eager catalogs alone do not prove a bottleneck.                                        | Profile selected-catalog loading as a separate optional change; preserve locale startup and add recoverable route errors.                        |
| F17 | P3, R; breadth review pending | `frontend/packages/domain/src/metadata-preferences.ts`, `last-collection.ts`; app wrappers                                | A nominal domain package also owns browser persistence/subscriptions. Naming is not itself a defect.                                                                   | Make environment ownership explicit only where it removes duplicate subscriptions or test coupling.                                              |
| F18 | P2, R; hypotheses to qualify  | settings, storage/provider panels, profiles, statistics, search and extension capture                                     | Repeated read/draft/process coordination was observed in selected flows; not every branch has been reviewed.                                                           | Complete per-file review in I8–I10, record specific failures, migrate behaviour owners without blanket rewrites.                                 |

## Preserve

Keep SQL-backed permissions/live scopes and explicit backend capability owners;
same-origin cookie auth; independent Model identity; UI primitives with focus and
motion contracts; outliner branch/reveal query identities; lazy 3D/PDF/Markdown
viewers and cancellable G-code work; authorized resync on event reconnect; local
upload progress; existing real API/browser tests. Large files or legacy naming
alone do not justify replacing any of these designs.
